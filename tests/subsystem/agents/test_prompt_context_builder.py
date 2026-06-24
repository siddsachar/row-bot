from __future__ import annotations


def test_stable_prefix_fingerprint_ignores_ephemeral_sections_and_tracks_stable_changes():
    from row_bot.prompt_context import ephemeral_section, stable_prefix_fingerprint, stable_section

    base_sections = [
        stable_section("agent.root", "root prompt"),
        stable_section("platform.context", "windows powershell"),
        ephemeral_section("turn.date_time", "Tuesday"),
    ]
    changed_ephemeral = [
        stable_section("agent.root", "root prompt"),
        stable_section("platform.context", "windows powershell"),
        ephemeral_section("turn.date_time", "Wednesday"),
    ]
    changed_stable = [
        stable_section("agent.root", "root prompt"),
        stable_section("platform.context", "macos zsh"),
        ephemeral_section("turn.date_time", "Tuesday"),
    ]

    assert stable_prefix_fingerprint(section for section in base_sections if section is not None)
    assert stable_prefix_fingerprint(section for section in base_sections if section is not None) == stable_prefix_fingerprint(
        section for section in changed_ephemeral if section is not None
    )
    assert stable_prefix_fingerprint(section for section in base_sections if section is not None) != stable_prefix_fingerprint(
        section for section in changed_stable if section is not None
    )


def test_section_messages_track_cache_eligible_stable_systems_only():
    from row_bot.prompt_context import cache_eligible_message_ids, ephemeral_section, section_messages, stable_section

    pairs = section_messages(section for section in [
        stable_section("agent.root", "root prompt"),
        stable_section("agent.disabled_cache", "stable but not cached", cache_eligible=False),
        ephemeral_section("turn.date_time", "Tuesday"),
    ] if section is not None)

    eligible_ids = cache_eligible_message_ids(pairs)

    assert id(pairs[0].message) in eligible_ids
    assert id(pairs[1].message) not in eligible_ids
    assert id(pairs[2].message) not in eligible_ids
