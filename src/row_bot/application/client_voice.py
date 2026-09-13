"""Voice presentation callbacks through normal conversation command admission."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from row_bot.runtime.executions import ExecutionHandle
    from row_bot.voice.openai_realtime import OpenAIRealtimeProvider

from collections.abc import Callable
from copy import deepcopy
import json
from typing import Any
from uuid import UUID, uuid5

from row_bot.application.client_platform import _COMMAND_LOCK
from row_bot.runtime import admissions
from row_bot.voice.client_transport import DictationError, DictationOwner
from row_bot.voice.client_talk import TalkSpeechSource


class ClientVoiceAdmission:
    def __init__(self, service: Any) -> None:
        self.service = service

    def _policy(self, owner: DictationOwner, context: dict) -> dict:
        from row_bot.application.profile_controls import freeze_profile
        from row_bot.application.reasoning_controls import freeze_reasoning
        from row_bot.approval_policy import normalize_approval_mode
        from row_bot.providers.selection import model_choice_value, parse_model_ref
        row = self.service._metadata(owner.conversation_id)
        selection = context["model_selection"]
        parsed = parse_model_ref(selection["model_ref"])
        if parsed and parsed[0] != selection["provider_id"]:
            raise DictationError("model_selection_mismatch")
        policy = {"model_override": model_choice_value(selection["model_ref"], provider_id=selection["provider_id"]),
            "runtime_surface": "normal_chat", "runtime_mode": row.get("client_runtime_mode") or "agent",
            "approval_mode": normalize_approval_mode(row.get("approval_mode")),
            "agent_profile_id": row.get("agent_profile_id") or ""}
        freeze_profile(policy)
        freeze_reasoning(policy, owner.conversation_id)
        return policy

    def prepare_context(self, owner: DictationOwner, context: dict, validate: Callable[[], None]) -> dict:
        """Capture private policy once; wire input cannot supply frozen controls."""
        with _COMMAND_LOCK:
            result = {key: deepcopy(context[key]) for key in ("conversation_revision", "model_selection", "write_targets")}
            self.validate_context(owner, result, validate)
            policy = self._policy(owner, result)
            result["_policy"] = policy
            result["_policy_digest"] = admissions.keyed_digest(policy)
            self.validate_context(owner, result, validate)
            return result

    def validate_context(self, owner: DictationOwner, context: dict, validate: Callable[[], None], *,
                         refresh_resources: bool = False) -> None:
        validate()
        if owner.server_epoch != self.service.server_epoch:
            raise DictationError("voice_session_expired")
        row = self.service._metadata(owner.conversation_id)
        if admissions.deletion_state(owner.conversation_id) != "active":
            raise DictationError("conversation_deleting")
        if str(row["client_revision"]) != context.get("conversation_revision"):
            raise DictationError("revision_conflict")
        from row_bot.conversation_resources import list_bindings, describe
        bindings = list_bindings(owner.conversation_id).bindings
        seen = set()
        for target in context.get("write_targets", []):
            binding = next((item for item in bindings if item.binding_id == target["binding_id"]), None)
            if (binding is None or binding.kind in seen or binding.kind != target["kind"] or
                    binding.resource_id != target["resource_id"] or binding.revision != target["binding_revision"]):
                raise DictationError("resource_binding_revoked")
            descriptor = describe(binding)
            if not descriptor.available or (not refresh_resources and descriptor.resource_revision != target["resource_revision"]):
                raise DictationError("resource_revision_conflict")
            if refresh_resources:
                target["resource_revision"] = descriptor.resource_revision
            seen.add(binding.kind)
        if context.get("_policy_digest") is not None:
            if (admissions.keyed_digest(context.get("_policy")) != context["_policy_digest"]
                    or admissions.keyed_digest(self._policy(owner, context)) != context["_policy_digest"]):
                raise DictationError("voice_policy_changed")
        validate()

    def _context(self, owner: DictationOwner) -> dict:
        transport = self.service.dictation
        with transport.coordinator._dictation_lock:
            lease = transport.coordinator._dictation_lease
            if lease is None or lease.owner != owner or lease.revoked or lease.chat_context is None:
                raise DictationError("voice_session_expired")
            return deepcopy(lease.chat_context)

    def active(self, owner: DictationOwner) -> ExecutionHandle | None:
        return next((handle for handle in self.service.registry.active(owner.conversation_id)
                     if handle.domain == "conversation"), None)

    def _command(self, owner: DictationOwner, event_id: str, kind: str, payload: dict,
                 revision: str, validate: Callable[[], None], *, frozen_context: dict | None = None) -> dict:
        identity = str(uuid5(UUID(event_id), owner.client_session_id + ":" + owner.conversation_id + ":" + kind))
        return self.service.execute(owner_id=self.service.instance_id, idempotency_key=identity,
            command={"command_id": identity, "client_session_id": owner.client_session_id,
                     "type": kind, "expected_revision": revision, "payload": payload},
            target=owner.conversation_id, validate=validate,
            **({"frozen_context": frozen_context} if frozen_context is not None else {}))

    def submit(self, owner: DictationOwner, text: str, event_id: str, validate: Callable[[], None]) -> str:
        with _COMMAND_LOCK:
            context = self._context(owner)
            self.validate_context(owner, context, validate, refresh_resources=True)
            if "_policy" not in context:
                raise DictationError("voice_policy_changed")
            from row_bot.conversation_resources import list_bindings
            from row_bot.application.client_queue import freeze_context
            ids = {target["binding_id"] for target in context["write_targets"]}
            bindings = tuple(binding for binding in list_bindings(owner.conversation_id).bindings if binding.binding_id in ids)
            frozen = freeze_context({"configurable": context["_policy"]}, bindings, context["write_targets"])
            def authority() -> None:
                self.validate_context(owner, context, validate)
            authority()
            result = self._command(owner, event_id, "conversation.submit", {
                "submission_id": event_id, "text": text, "attachment_refs": [],
                "model_selection": context["model_selection"], "write_targets": context["write_targets"]},
                context["conversation_revision"], authority, frozen_context=frozen)
            generation = str(result.get("generation_id") or "")
            handle = self.service.registry.conversation_generation(owner.conversation_id, generation)
            if handle is None:
                raise DictationError("voice_run_changed")
            with self.service.dictation.coordinator._dictation_lock:
                lease = self.service.dictation.coordinator._dictation_lease
                if lease is not None and lease.owner == owner and not lease.revoked:
                    lease.admitted_run = handle
            return generation

    def control(self, owner: DictationOwner, expected: Any, action: str, text: str,
                event_id: str, validate: Callable[[], None]) -> dict:
        with _COMMAND_LOCK:
            validate()
            context = self._context(owner)
            if owner.server_epoch != self.service.server_epoch or admissions.deletion_state(owner.conversation_id) != "active":
                raise DictationError("voice_session_expired")
            if self.active(owner) is not expected:
                raise DictationError("voice_run_changed")
            self.service._metadata(owner.conversation_id)
            if expected is None:
                return {"status": "idle", "speakable": "No conversation run is active."}
            if action == "input":
                from row_bot.voice.actions import classify_active_run_control
                classified = classify_active_run_control(text)
                if classified in {"status", "cancel"}:
                    action = classified
            if action == "status":
                return {"status": expected.status, "control": "status", "speakable": "The conversation run is " + expected.status + "."}
            revision = str(self.service._metadata(owner.conversation_id)["client_revision"])
            def authority() -> None:
                validate()
                if self.active(owner) is not expected:
                    raise DictationError("voice_run_changed")
                if action == "input":
                    self.validate_context(owner, context, validate)
            if action == "cancel":
                self._command(owner, event_id, "conversation.stop", {}, revision, authority)
                return {"status": "cancel_requested", "control": "cancel", "speakable": "Stopping the conversation run."}
            if action != "input" or not text.strip():
                raise DictationError("invalid_voice_event")
            self.validate_context(owner, context, validate, refresh_resources=True)
            if expected.model_ref != context["_policy"]["model_override"]:
                raise DictationError("voice_policy_changed")
            self._command(owner, event_id, "conversation.steer", {"text": text, "steering_id": event_id}, revision, authority)
            return {"status": "queued", "control": "follow_up", "speakable": "Your follow-up is queued in this conversation."}

    def output(self, owner: DictationOwner, run_id: str, output_id: str, validate: Callable[[], None]) -> TalkSpeechSource:
        validate()
        self._context(owner)
        if owner.server_epoch != self.service.server_epoch or admissions.deletion_state(owner.conversation_id) != "active":
            raise DictationError("voice_session_expired")
        handle = self.service.registry.conversation_generation(owner.conversation_id, run_id)
        if (handle is None or handle.server_epoch != owner.server_epoch or not handle.producer_done.is_set()
                or handle.status != "completed" or not handle.segment_committed or handle.output_message_id != output_id):
            raise DictationError("voice_output_unavailable")
        self.service._metadata(owner.conversation_id)
        from row_bot.runtime.checkpoint_reader import open_checkpoint
        with open_checkpoint(owner.conversation_id, handle.output_checkpoint_revision) as reader:
            if reader is None:
                raise DictationError("voice_output_unavailable")
            matches = []
            for _, record in reader.records():
                if record["message_id"] == output_id:
                    matches.append(record)
                    if len(matches) > 1:
                        break
            if len(matches) != 1 or matches[0]["role"] != "assistant" or matches[0]["tool_call_ids"] or matches[0]["tool_ids_lazy"]:
                raise DictationError("voice_output_unavailable")
            encoded = bytearray()
            for chunk in reader.content_chunks(matches[0]):
                if len(encoded) + len(chunk) > 256000:
                    raise DictationError("voice_output_too_large")
                encoded.extend(chunk)
        value = json.loads(encoded)
        if isinstance(value, list):
            value = "\n".join(item["text"] for item in value if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str))
        if not isinstance(value, str) or not value.strip():
            raise DictationError("voice_output_unavailable")
        validate()
        self._context(owner)
        if admissions.deletion_state(owner.conversation_id) != "active":
            raise DictationError("conversation_deleting")
        self.service._metadata(owner.conversation_id)
        return TalkSpeechSource(value)

    def run_view(self, owner: DictationOwner, handle: Any, validate: Callable[[], None]) -> dict:
        """Read exact lease run and final speakable output without synthesis."""
        transport = self.service.dictation
        def admitted() -> None:
            validate()
            with transport.coordinator._dictation_lock:
                transport._lease(owner, handle)
        admitted()
        with transport.coordinator._dictation_lock:
            lease = transport._lease(owner, handle)
            run_id = lease.run_id
        if not run_id:
            return {"run_id": None, "state": "idle", "output_id": None, "text": None}
        run = self.service.registry.conversation_generation(owner.conversation_id, run_id)
        if run is None or run.server_epoch != owner.server_epoch:
            raise DictationError("voice_run_changed")
        result = {"run_id": run_id, "state": run.status, "output_id": None, "text": None}
        if run.producer_done.is_set() and run.status == "completed" and run.segment_committed and run.output_message_id:
            from row_bot.voice.speech_policy import make_speakable_response
            source = self.output(owner, run_id, run.output_message_id, admitted)
            text = make_speakable_response(source.text, allow_long=source.allow_long).text
            if len(text) > 4000:
                raise DictationError("voice_output_too_large")
            result.update(output_id=run.output_message_id, text=text)
        admitted()
        with transport.coordinator._dictation_lock:
            if transport._lease(owner, handle).run_id != run_id:
                raise DictationError("voice_run_changed")
        return result


def realtime_provider() -> OpenAIRealtimeProvider:
    from row_bot.voice.openai_realtime import OpenAIRealtimeProvider
    from row_bot.voice.runtime import load_voice_runtime_settings
    settings = load_voice_runtime_settings()
    return OpenAIRealtimeProvider(model=settings.talk_model, voice=settings.realtime_voice)
