from __future__ import annotations

from voice.cue_policy import VoiceCuePolicy
from voice.cues import (
    VoiceCue,
    VoiceCuePriority,
    VoiceCueType,
    approval_needed_cue,
    heard_cue,
    long_running_cue,
    results_found_cue,
    thinking_cue,
    tool_progress_cue,
    tool_start_cue,
)
from voice.output_controller import VoiceOutputController


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class FakeTTS:
    enabled = True

    def __init__(self) -> None:
        self.spoken: list[str] = []
        self.flushed: list[str] = []

    def speak_streaming(self, text: str) -> None:
        self.spoken.append(text)

    def flush_streaming(self, text: str = "") -> None:
        self.flushed.append(text)


def test_realtime_policy_allows_quick_heard_cue_after_short_delay():
    clock = Clock()
    policy = VoiceCuePolicy(mode="realtime", now=clock)

    assert policy.should_speak(heard_cue(), generation_elapsed=0.1) is False
    assert policy.should_speak(heard_cue(), generation_elapsed=0.7) is True


def test_normal_policy_delays_low_priority_cues_longer():
    clock = Clock()
    policy = VoiceCuePolicy(mode="normal", now=clock)

    assert policy.should_speak(heard_cue(), generation_elapsed=1.0) is False
    assert policy.should_speak(heard_cue(), generation_elapsed=2.1) is True


def test_policy_rate_limits_and_deduplicates_noncritical_cues():
    clock = Clock()
    policy = VoiceCuePolicy(mode="realtime", now=clock)
    cue = tool_start_cue("browser_open")

    assert policy.should_speak(cue, generation_elapsed=1.0) is False
    assert policy.should_speak(cue, generation_elapsed=5.0) is True
    policy.record_spoken(cue)
    clock.value = 4.0

    assert policy.should_speak(cue, generation_elapsed=4.0) is False


def test_policy_allows_only_one_opening_low_priority_cue():
    clock = Clock()
    policy = VoiceCuePolicy(mode="realtime", now=clock)

    cue = heard_cue()
    assert policy.should_speak(cue, generation_elapsed=1.0) is True
    policy.record_spoken(cue)
    clock.value = 12.0

    assert policy.should_speak(thinking_cue(), generation_elapsed=12.0) is False


def test_normal_policy_allows_sparse_long_running_cues_after_delay():
    clock = Clock()
    policy = VoiceCuePolicy(mode="normal", now=clock)

    cue = heard_cue()
    assert policy.should_speak(cue, generation_elapsed=3.0) is True
    policy.record_spoken(cue)
    clock.value = 8.0
    assert policy.should_speak(long_running_cue(), generation_elapsed=8.0) is False

    clock.value = 14.0
    long_cue = long_running_cue()
    assert policy.should_speak(long_cue, generation_elapsed=14.0) is True
    policy.record_spoken(long_cue)

    clock.value = 17.0
    assert policy.should_speak(long_running_cue(), generation_elapsed=17.0) is False


def test_realtime_policy_allows_one_generic_status_cue_per_generation():
    clock = Clock()
    policy = VoiceCuePolicy(mode="realtime", now=clock)

    cue = heard_cue()
    assert policy.should_speak(cue, generation_elapsed=1.0) is True
    policy.record_spoken(cue)
    clock.value = 30.0

    assert policy.should_speak(long_running_cue(), generation_elapsed=30.0) is True


def test_realtime_tool_cues_repeat_sparsely_after_opening_cue():
    clock = Clock()
    policy = VoiceCuePolicy(mode="realtime", now=clock)

    opening = heard_cue()
    assert policy.should_speak(opening, generation_elapsed=1.0) is True
    policy.record_spoken(opening)

    tool = tool_start_cue("browser_open")
    assert policy.should_speak(tool, generation_elapsed=1.1) is False

    clock.value = 7.0
    assert policy.should_speak(tool, generation_elapsed=7.0) is True
    policy.record_spoken(tool)

    clock.value = 13.0
    assert policy.should_speak(tool_progress_cue(), generation_elapsed=13.0) is False

    clock.value = 21.1
    assert policy.should_speak(tool_progress_cue(), generation_elapsed=21.1) is True


def test_realtime_tool_cues_are_generic_not_tool_specific():
    texts = {
        tool_start_cue("browser_open").text,
        tool_start_cue("workspace_read_file").text,
        tool_start_cue("search_gmail").text,
    }

    joined = " ".join(texts).lower()
    assert "browser" not in joined
    assert "file" not in joined
    assert "search" not in joined


def test_voice_cues_avoid_repeating_checking_language():
    texts = {
        heard_cue().text,
        thinking_cue().text,
        long_running_cue().text,
        tool_start_cue("browser_open").text,
        tool_progress_cue().text,
    }

    assert all("checking" not in text.lower() for text in texts)


def test_critical_approval_cue_bypasses_user_speaking_and_interval():
    clock = Clock()
    policy = VoiceCuePolicy(mode="normal", now=clock)
    policy.record_spoken(VoiceCue(VoiceCueType.THINKING, "I'm working on that."))

    assert policy.should_speak(approval_needed_cue(), user_speaking=True) is True


def test_output_controller_routes_normal_cues_to_local_tts():
    clock = Clock()
    tts = FakeTTS()
    controller = VoiceOutputController.for_generation(
        voice_mode=True,
        transport="local",
        tts_service=tts,
        realtime_speaker=None,
        now=clock,
    )

    assert controller.speak_cue(tool_start_cue("search"), generation_elapsed=3.0) is True
    assert len(tts.spoken) == 1
    assert tts.spoken[0] in {
        "I'm working through the next step.",
        "One moment while I work through that.",
        "I'm taking the next step.",
    }
    assert tts.flushed == []


def test_output_controller_flushes_normal_final_speech():
    clock = Clock()
    tts = FakeTTS()
    controller = VoiceOutputController.for_generation(
        voice_mode=True,
        transport="local",
        tts_service=tts,
        realtime_speaker=None,
        now=clock,
    )

    assert controller.speak_final("Done.") is True
    assert tts.spoken == []
    assert tts.flushed == ["Done."]


def test_output_controller_routes_realtime_cues_to_realtime_speaker():
    clock = Clock()
    spoken: list[tuple[str, str]] = []

    def speaker(text: str, *, origin: str = "final") -> bool:
        spoken.append((text, origin))
        return True

    controller = VoiceOutputController.for_generation(
        voice_mode=True,
        transport="realtime",
        tts_service=FakeTTS(),
        realtime_speaker=speaker,
        now=clock,
    )

    assert controller.speak_cue(tool_start_cue("browser_open"), generation_elapsed=1.0) is False
    assert controller.speak_cue(tool_start_cue("browser_open"), generation_elapsed=5.0) is True
    assert len(spoken) == 1
    assert spoken[0][1] == "tool_start"
    assert spoken[0][0] in {
        "I'm working through the next step.",
        "One moment while I work through that.",
        "I'm taking the next step.",
    }


def test_realtime_results_found_cue_is_generic_progress():
    cue = results_found_cue()

    assert cue.type == VoiceCueType.TOOL_PROGRESS
    assert cue.priority == VoiceCuePriority.NORMAL
    assert "browser" not in cue.text.lower()
    assert "file" not in cue.text.lower()


def test_output_controller_routes_realtime_final_with_final_origin():
    clock = Clock()
    spoken: list[tuple[str, str]] = []

    def speaker(text: str, *, origin: str = "cue") -> bool:
        spoken.append((text, origin))
        return True

    controller = VoiceOutputController.for_generation(
        voice_mode=True,
        transport="realtime",
        tts_service=FakeTTS(),
        realtime_speaker=speaker,
        now=clock,
    )

    assert controller.speak_final("Done.") is True
    assert spoken == [("Done.", "final")]


def test_output_controller_is_silent_when_voice_mode_is_off():
    clock = Clock()
    tts = FakeTTS()
    spoken: list[str] = []
    controller = VoiceOutputController.for_generation(
        voice_mode=False,
        transport="realtime",
        tts_service=tts,
        realtime_speaker=spoken.append,
        now=clock,
    )

    assert controller.speak_cue(
        VoiceCue(VoiceCueType.ERROR, "Nope.", VoiceCuePriority.CRITICAL),
        generation_elapsed=10.0,
    ) is False
    assert tts.spoken == []
    assert spoken == []


def test_output_controller_treats_realtime_speaker_failure_as_recoverable():
    clock = Clock()

    def fail(_: str) -> None:
        raise RuntimeError("browser speech failed")

    controller = VoiceOutputController.for_generation(
        voice_mode=True,
        transport="realtime",
        tts_service=FakeTTS(),
        realtime_speaker=fail,
        now=clock,
    )

    assert controller.speak_final("Done.") is False
    assert controller.speak_cue(
        VoiceCue(VoiceCueType.ERROR, "Error.", VoiceCuePriority.CRITICAL),
        generation_elapsed=10.0,
    ) is False


def test_output_controller_honors_realtime_speaker_false_result():
    clock = Clock()
    calls: list[str] = []

    def fail_once(text: str) -> bool:
        calls.append(text)
        return False

    controller = VoiceOutputController.for_generation(
        voice_mode=True,
        transport="realtime",
        tts_service=FakeTTS(),
        realtime_speaker=fail_once,
        now=clock,
    )

    cue = tool_start_cue("browser_open")
    assert controller.speak_cue(cue, generation_elapsed=5.0) is False
    assert controller.speak_cue(cue, generation_elapsed=5.2) is False
    assert calls == [cue.text]


def test_output_controller_counts_busy_realtime_cue_as_attempted():
    clock = Clock()
    calls: list[str] = []

    def busy(text: str, *, origin: str = "cue") -> bool:
        calls.append(text)
        return False

    controller = VoiceOutputController.for_generation(
        voice_mode=True,
        transport="realtime",
        tts_service=FakeTTS(),
        realtime_speaker=busy,
        now=clock,
    )

    assert controller.speak_cue(heard_cue(), generation_elapsed=1.0) is False
    clock.value = 30.0
    assert controller.speak_cue(long_running_cue(), generation_elapsed=30.0) is False
    assert len(calls) == 2


def test_output_controller_realtime_final_false_result_is_not_spoken():
    clock = Clock()

    controller = VoiceOutputController.for_generation(
        voice_mode=True,
        transport="realtime",
        tts_service=FakeTTS(),
        realtime_speaker=lambda _text: False,
        now=clock,
    )

    assert controller.speak_final("Done.") is False
