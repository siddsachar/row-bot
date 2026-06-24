from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Iterable

from langchain_core.messages import SystemMessage


class PromptStability(StrEnum):
    STABLE = "stable"
    EPHEMERAL = "ephemeral"


@dataclass(frozen=True)
class PromptSection:
    section_id: str
    content: str
    stability: PromptStability
    cache_eligible: bool = False
    source: str = ""

    def system_message(self) -> SystemMessage:
        return SystemMessage(content=self.content)


@dataclass(frozen=True)
class PromptSectionMessage:
    section: PromptSection
    message: SystemMessage


def stable_section(
    section_id: str,
    content: str,
    *,
    cache_eligible: bool = True,
    source: str = "",
) -> PromptSection | None:
    text = str(content or "").strip()
    if not text:
        return None
    return PromptSection(
        section_id=section_id,
        content=text,
        stability=PromptStability.STABLE,
        cache_eligible=cache_eligible,
        source=source,
    )


def ephemeral_section(section_id: str, content: str, *, source: str = "") -> PromptSection | None:
    text = str(content or "").strip()
    if not text:
        return None
    return PromptSection(
        section_id=section_id,
        content=text,
        stability=PromptStability.EPHEMERAL,
        cache_eligible=False,
        source=source,
    )


def section_messages(sections: Iterable[PromptSection]) -> list[PromptSectionMessage]:
    return [
        PromptSectionMessage(section=section, message=section.system_message())
        for section in sections
        if section.content
    ]


def cache_eligible_message_ids(section_message_pairs: Iterable[PromptSectionMessage]) -> set[int]:
    return {
        id(pair.message)
        for pair in section_message_pairs
        if pair.section.stability == PromptStability.STABLE and pair.section.cache_eligible
    }


def stable_prefix_fingerprint(sections: Iterable[PromptSection]) -> str:
    hasher = sha256()
    for section in sections:
        if section.stability != PromptStability.STABLE or not section.cache_eligible:
            continue
        hasher.update(section.section_id.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(section.content.encode("utf-8"))
        hasher.update(b"\0")
    return hasher.hexdigest()


def stable_cache_sections(sections: Iterable[PromptSection]) -> tuple[PromptSection, ...]:
    return tuple(
        section
        for section in sections
        if section.stability == PromptStability.STABLE and section.cache_eligible
    )
