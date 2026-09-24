"""Bounded public reads of the existing SQLite MessagePack checkpoint blob.

The checkpoint remains the only transcript store. This reader skips unrelated
values by encoded length and never instantiates persisted Python objects.
"""
from __future__ import annotations

import codecs
import hashlib
import json
import re
import struct
from contextlib import contextmanager
from dataclasses import dataclass
from collections.abc import Iterator
from typing import Any


_PUBLIC_REFERENCE = re.compile(r"^[A-Za-z0-9:_-]{1,256}$")


@dataclass(frozen=True)
class Node:
    kind: str
    body: int
    size: int
    code: int = 0


class BlobReader:
    MAX_NODES = 2_000_000

    def __init__(self, blob: Any, revision: str) -> None:
        self.blob = blob
        self.revision = revision
        self._nodes = 0

    def _read(self, size: int) -> bytes:
        value = self.blob.read(size)
        if len(value) != size:
            raise ValueError("checkpoint_format_invalid")
        return value

    def node(self, position: int) -> Node:
        self._nodes += 1
        if self._nodes > self.MAX_NODES:
            raise ValueError("checkpoint_read_limit")
        self.blob.seek(position)
        tag = self._read(1)[0]
        if tag <= 0x7f or tag >= 0xe0 or tag in {0xc0, 0xc2, 0xc3}:
            return Node("scalar", position, 1)
        if 0xa0 <= tag <= 0xbf:
            return Node("str", self.blob.tell(), tag & 31)
        if 0x90 <= tag <= 0x9f:
            return Node("array", self.blob.tell(), tag & 15)
        if 0x80 <= tag <= 0x8f:
            return Node("map", self.blob.tell(), tag & 15)
        sizes = {0xc4: ("bin", 1), 0xc5: ("bin", 2), 0xc6: ("bin", 4),
                 0xd9: ("str", 1), 0xda: ("str", 2), 0xdb: ("str", 4),
                 0xdc: ("array", 2), 0xdd: ("array", 4), 0xde: ("map", 2), 0xdf: ("map", 4),
                 0xc7: ("ext", 1), 0xc8: ("ext", 2), 0xc9: ("ext", 4)}
        if tag in sizes:
            kind, width = sizes[tag]
            size = int.from_bytes(self._read(width), "big")
            code = self._read(1)[0] if kind == "ext" else 0
            return Node(kind, self.blob.tell(), size, code)
        if 0xd4 <= tag <= 0xd8:
            code = self._read(1)[0]
            return Node("ext", self.blob.tell(), 1 << (tag - 0xd4), code)
        widths = {0xca: 4, 0xcb: 8, 0xcc: 1, 0xcd: 2, 0xce: 4, 0xcf: 8,
                  0xd0: 1, 0xd1: 2, 0xd2: 4, 0xd3: 8}
        if tag in widths:
            return Node("scalar", position, 1 + widths[tag])
        raise ValueError("checkpoint_format_invalid")

    def end(self, position: int, depth: int = 0) -> int:
        if depth > 64:
            raise ValueError("checkpoint_format_invalid")
        node = self.node(position)
        if node.kind not in {"map", "array"}:
            return node.body + node.size
        position = node.body
        for _ in range(node.size * (2 if node.kind == "map" else 1)):
            position = self.end(position, depth + 1)
        return position

    def text(self, position: int, maximum: int = 1024) -> str:
        node = self.node(position)
        if node.kind != "str" or node.size > maximum:
            return ""
        self.blob.seek(node.body)
        return self._read(node.size).decode("utf-8")

    def fields(self, position: int, wanted: set[str]) -> dict[str, int]:
        node = self.node(position)
        if node.kind != "map":
            return {}
        found = {}
        position = node.body
        for _ in range(node.size):
            key = self.text(position)
            position = self.end(position)
            if key in wanted:
                found[key] = position
            position = self.end(position)
        return found

    def _public_scalar(self, position: int, maximum: int) -> Any:
        """Decode one bounded primitive without materializing persisted objects."""

        self.blob.seek(position)
        tag = self._read(1)[0]
        if tag <= 0x7F:
            return tag
        if tag >= 0xE0:
            return tag - 256
        if tag == 0xC0:
            return None
        if tag == 0xC2:
            return False
        if tag == 0xC3:
            return True
        if tag in {0xCA, 0xCB}:
            width = 4 if tag == 0xCA else 8
            return struct.unpack(">f" if width == 4 else ">d", self._read(width))[0]
        unsigned = {0xCC: 1, 0xCD: 2, 0xCE: 4, 0xCF: 8}
        signed = {0xD0: 1, 0xD1: 2, 0xD2: 4, 0xD3: 8}
        if tag in unsigned:
            return int.from_bytes(self._read(unsigned[tag]), "big")
        if tag in signed:
            return int.from_bytes(self._read(signed[tag]), "big", signed=True)
        node = self.node(position)
        if node.kind == "str" and node.size <= maximum:
            self.blob.seek(node.body)
            return self._read(node.size).decode("utf-8")
        return ...

    def safe_tool_args(self, position: int) -> dict[str, Any]:
        """Read only reviewed primitive tool arguments from one encoded map."""

        from row_bot.application.conversation_traces import SAFE_TOOL_CALL_ARG_KEYS

        node = self.node(position)
        if node.kind != "map" or node.size > 64:
            return {}
        safe: dict[str, Any] = {}
        current = node.body
        for _ in range(node.size):
            key = self.text(current, 128)
            current = self.end(current)
            value_position = current
            value_node = self.node(value_position)
            if key in SAFE_TOOL_CALL_ARG_KEYS:
                if value_node.kind == "array" and value_node.size <= 8:
                    values: list[Any] = []
                    child = value_node.body
                    for _ in range(value_node.size):
                        value = self._public_scalar(child, 120)
                        if value is not ...:
                            values.append(value)
                        child = self.end(child)
                    safe[key] = values
                else:
                    value = self._public_scalar(value_position, 180)
                    if value is not ...:
                        safe[key] = value
            current = self.end(value_position)
        return safe

    def safe_attachments(self, position: int) -> list[dict[str, Any]]:
        """Decode only the closed public metadata written at input admission."""

        from row_bot.application.attachments import MAX_ATTACHMENT_BYTES, PUBLIC_MIME_TYPES

        node = self.node(position)
        if node.kind != "array" or node.size > 32:
            return []
        attachments: list[dict[str, Any]] = []
        current = node.body
        for _ in range(node.size):
            fields = self.fields(
                current,
                {"attachment_ref", "name", "mime_type", "size_bytes", "revision"},
            )
            reference = self.text(fields.get("attachment_ref", -1), 256) if "attachment_ref" in fields else ""
            name = self.text(fields.get("name", -1), 240) if "name" in fields else ""
            mime_type = self.text(fields.get("mime_type", -1), 128) if "mime_type" in fields else ""
            revision = self.text(fields.get("revision", -1), 20) if "revision" in fields else ""
            size = self._public_scalar(fields["size_bytes"], 0) if "size_bytes" in fields else None
            if (
                _PUBLIC_REFERENCE.fullmatch(reference)
                and name
                and len(name.encode("utf-8")) <= 240
                and mime_type in PUBLIC_MIME_TYPES
                and isinstance(size, int)
                and not isinstance(size, bool)
                and 1 <= size <= MAX_ATTACHMENT_BYTES
                and revision.isdigit()
                and (revision == "0" or not revision.startswith("0"))
            ):
                attachments.append(
                    {
                        "attachment_ref": reference,
                        "name": name,
                        "mime_type": mime_type,
                        "size_bytes": size,
                        "revision": revision,
                    }
                )
            current = self.end(current)
        return attachments

    def safe_platform_media(self, position: int) -> tuple[list[dict[str, Any]], str]:
        """Decode only opaque generated-media references and public error codes."""

        from row_bot.application.attachments import PUBLIC_MIME_TYPES

        node = self.node(position)
        if node.kind != "array" or node.size > 8:
            return [], ""
        media: list[dict[str, Any]] = []
        error = ""
        current = node.body
        for _ in range(node.size):
            fields = self.fields(current, {"type", "payload"})
            kind = self.text(fields["type"], 32) if "type" in fields else ""
            payload = self.fields(fields["payload"], {"media_ref", "mime_type", "code"}) if "payload" in fields else {}
            if kind == "media.available":
                reference = self.text(payload["media_ref"], 256) if "media_ref" in payload else ""
                mime_type = self.text(payload["mime_type"], 128) if "mime_type" in payload else ""
                if _PUBLIC_REFERENCE.fullmatch(reference) and mime_type in PUBLIC_MIME_TYPES:
                    media.append(
                        {
                            "type": kind,
                            "payload": {"media_ref": reference, "mime_type": mime_type},
                        }
                    )
            elif kind == "media.error":
                code = self.text(payload["code"], 80) if "code" in payload else ""
                if code in {"payload_too_large", "media_unavailable"}:
                    error = code
            current = self.end(current)
        return media, error

    def records(self, *, start: int = 0) -> Iterator[tuple[int, dict]]:
        root = self.fields(0, {"channel_values"})
        channel = self.fields(root["channel_values"], {"messages"}) if "channel_values" in root else {}
        if "messages" not in channel:
            return
        messages = self.node(channel["messages"])
        if messages.kind != "array":
            raise ValueError("checkpoint_format_invalid")
        position = messages.body
        first = max(0, messages.size + start) if start < 0 else start
        for index in range(messages.size):
            end = self.end(position)
            if index >= first:
                record = self.message(position)
                if record:
                    yield index, record
            position = end

    def message(self, position: int) -> dict | None:
        node = self.node(position)
        if node.kind != "ext" or node.code not in {4, 5}:
            return None
        envelope = self.node(node.body)
        if envelope.kind != "array" or envelope.size < 3:
            return None
        module = self.text(envelope.body)
        class_position = self.end(envelope.body)
        name = self.text(class_position)
        roles = {"HumanMessage": "user", "AIMessage": "assistant", "ToolMessage": "tool"}
        if not module.startswith("langchain_core.messages.") or name not in roles:
            return None
        fields = self.fields(
            self.end(class_position),
            {"id", "content", "tool_calls", "tool_call_id", "additional_kwargs"},
        )
        identity = self.text(fields["id"]) if "id" in fields else ""
        if not identity:
            raise ValueError("checkpoint_identity_migration_required")
        tool_ids = []
        tool_calls = []
        tool_ids_lazy = False
        tool_id_bytes = 0
        if "tool_calls" in fields:
            calls = self.node(fields["tool_calls"])
            current = calls.body
            if calls.kind == "array":
                for _ in range(calls.size):
                    call = self.fields(current, {"id", "name", "args"})
                    if "id" in call:
                        identity_text = self.text(call["id"])
                        tool_ids.append(identity_text)
                        tool_calls.append({
                            "id": identity_text,
                            "name": self.text(call["name"])[:180] if "name" in call else "tool",
                            "args": self.safe_tool_args(call["args"]) if "args" in call else {},
                        })
                        tool_id_bytes += len(identity_text.encode())
                        if len(tool_ids) > 128 or tool_id_bytes > 8192:
                            tool_ids, tool_calls, tool_ids_lazy = [], [], True
                            break
                    current = self.end(current)
        content_position = fields.get("content")
        attachments: list[dict[str, Any]] = []
        media: list[dict[str, Any]] = []
        media_error = ""
        if "additional_kwargs" in fields:
            metadata = self.fields(
                fields["additional_kwargs"],
                {
                    "platform_public_content",
                    "platform_attachments",
                    "platform_media",
                    "platform_media_error",
                },
            )
            if name == "HumanMessage":
                public_position = metadata.get("platform_public_content")
                if public_position is not None and self.node(public_position).kind == "str":
                    content_position = public_position
                if "platform_attachments" in metadata:
                    attachments = self.safe_attachments(metadata["platform_attachments"])
            elif name == "ToolMessage":
                if "platform_media" in metadata:
                    media, media_error = self.safe_platform_media(metadata["platform_media"])
                if not media_error and "platform_media_error" in metadata:
                    candidate = self.text(metadata["platform_media_error"], 80)
                    if candidate in {"payload_too_large", "media_unavailable"}:
                        media_error = candidate
        return {"message_id": identity, "role": roles[name], "content_position": content_position,
                "tool_call_ids": tool_ids, "tool_calls": tool_calls,
                "tool_calls_position": fields.get("tool_calls"), "tool_ids_lazy": tool_ids_lazy,
                "tool_call_id": self.text(fields["tool_call_id"]) if "tool_call_id" in fields else "",
                "attachments": attachments, "media": media, "media_error": media_error}

    def _json_string(self, position: int) -> Iterator[bytes]:
        node = self.node(position)
        if node.kind != "str":
            yield b'""'
            return
        yield b'"'
        decoder = codecs.getincrementaldecoder("utf-8")()
        offset, remaining = node.body, node.size
        while remaining:
            size = min(512, remaining)
            self.blob.seek(offset)
            text = decoder.decode(self._read(size), final=size == remaining)
            yield json.dumps(text, ensure_ascii=False)[1:-1].encode("utf-8")
            offset += size
            remaining -= size
        yield b'"'

    def content_chunks(self, record: dict) -> Iterator[bytes]:
        position = record.get("content_position")
        yield b"["
        if position is not None:
            content = self.node(position)
            def positions() -> Iterator[int]:
                if content.kind == "str":
                    yield position
                elif content.kind == "array":
                    current = content.body
                    for _ in range(content.size):
                        fields = self.fields(current, {"type", "text"})
                        if "type" in fields and self.text(fields["type"]) == "text" and "text" in fields:
                            yield fields["text"]
                        current = self.end(current)
            for index, text_position in enumerate(positions()):
                if index:
                    yield b","
                yield b'{"type":"text","text":'
                yield from self._json_string(text_position)
                yield b"}"
        yield b"]"

    def public_text_chunks(self, record: dict) -> Iterator[str]:
        """Yield only public text, never JSON structure or nontext metadata."""
        position = record.get("content_position")
        if position is None:
            return
        content = self.node(position)
        def positions() -> Iterator[int]:
            if content.kind == "str":
                yield position
            elif content.kind == "array":
                current = content.body
                for _ in range(content.size):
                    fields = self.fields(current, {"type", "text"})
                    if "type" in fields and self.text(fields["type"]) == "text" and "text" in fields:
                        yield fields["text"]
                    current = self.end(current)
        for text_position in positions():
            node = self.node(text_position)
            if node.kind != "str":
                continue
            decoder = codecs.getincrementaldecoder("utf-8")()
            offset, remaining = node.body, node.size
            while remaining:
                size = min(512, remaining)
                self.blob.seek(offset)
                yield decoder.decode(self._read(size), final=size == remaining)
                offset += size
                remaining -= size
            yield "\n"

    def text_only(self, record: dict) -> bool:
        """Whether public text blocks represent the complete native content."""
        position = record.get("content_position")
        if position is None:
            return False
        content = self.node(position)
        if content.kind == "str":
            return True
        if content.kind != "array":
            return False
        current = content.body
        for _ in range(content.size):
            block = self.node(current)
            if block.kind != "map" or block.size != 2:
                return False
            fields = self.fields(current, {"type", "text"})
            if (set(fields) != {"type", "text"} or self.text(fields["type"]) != "text"
                    or self.node(fields["text"]).kind != "str"):
                return False
            current = self.end(current)
        return True

    def tool_ids_chunks(self, record: dict) -> Iterator[bytes]:
        yield b"["
        position = record.get("tool_calls_position")
        emitted = False
        if position is not None:
            calls = self.node(position)
            current = calls.body
            if calls.kind == "array":
                for _ in range(calls.size):
                    fields = self.fields(current, {"id"})
                    if "id" in fields:
                        if emitted:
                            yield b","
                        yield from self._json_string(fields["id"])
                        emitted = True
                    current = self.end(current)
        yield b"]"

    def public_row(self, record: dict, maximum: int = 128 * 1024) -> dict:
        role, identity = record["role"], record["message_id"]
        row = {"id": f"user:submission:{identity}" if role == "user" else f"{role}:checkpoint:{identity}",
               "message_id": identity, "role": role, "tool_call_ids": record["tool_call_ids"],
               "tool_call_id": record["tool_call_id"], "blocks": []}
        if record["tool_ids_lazy"]:
            row["tool_calls_ref"] = identity + ":tool_calls"
        attachments = [
            {
                "id": "attachment:"
                + hashlib.sha256(
                    f"{identity}\0{index}\0{item['attachment_ref']}\0{item['revision']}".encode()
                ).hexdigest()[:24],
                "type": "attachment",
                **item,
            }
            for index, item in enumerate(record.get("attachments") or [])
        ]
        if record.get("media"):
            row["media"] = record["media"]
        if record.get("media_error"):
            row["media_error"] = record["media_error"]
        content = bytearray()
        for chunk in self.content_chunks(record):
            if len(content) + len(chunk) > maximum:
                return {
                    **row,
                    "blocks": attachments,
                    "content_status": "lazy",
                    "content_ref": identity,
                }
            content.extend(chunk)
        row["blocks"] = [*json.loads(content), *attachments]
        return row


@contextmanager
def open_checkpoint(conversation_id: str, revision: str = "") -> Iterator[BlobReader | None]:
    from row_bot import threads
    saver = threads.checkpointer
    with saver.cursor(transaction=False) as cursor:
        query = "SELECT rowid,checkpoint_id,type FROM checkpoints WHERE thread_id=? AND checkpoint_ns=''"
        params = [conversation_id]
        if revision:
            query += " AND checkpoint_id=?"
            params.append(revision)
        row = cursor.execute(query + " ORDER BY checkpoint_id DESC LIMIT 1", params).fetchone()
        if not row:
            yield None
            return
        if row[2] != "msgpack":
            raise ValueError("checkpoint_format_unsupported")
        with saver.conn.blobopen("checkpoints", "checkpoint", int(row[0]), readonly=True) as blob:
            yield BlobReader(blob, str(row[1]))
