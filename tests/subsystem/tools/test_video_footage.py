"""Real-footage helpers (C4): what's said finds where to cut, keep and lay b-roll; a tidy cuts pauses and fillers in
one step; a clip's voice is cleaned; and a clip's moments are its shots and stretches of speech.

Pure planning: the words are given as speech-to-text would hear them, and nothing runs ffmpeg.
"""

from __future__ import annotations

import pytest

from row_bot.video_editing import (
    MAX_FRAMES, VOICE_CLEANUP, EditError, InputVideo, SceneCut, Step, VideoInfo, build_plan, heard_steps, moments,
    parse_steps, pauses_and_fillers, read_shots, said,
)

pytestmark = pytest.mark.subsystem

# "So, um, today we open the box." … a pause … "And here it is." … "Thanks for watching."
TALK = [(0.8, 1.0, "So,"), (1.1, 1.4, "um,"), (1.5, 1.9, "today"), (1.9, 2.1, "we"), (2.1, 2.4, "open"),
        (2.4, 2.5, "the"), (2.5, 2.9, "box."), (5.0, 5.3, "And"), (5.3, 5.6, "here"), (5.6, 5.8, "it"),
        (5.8, 6.2, "is."), (6.5, 6.9, "Thanks"), (6.9, 7.0, "for"), (7.0, 7.5, "watching.")]
CLIP = InputVideo("clip.mp4", VideoInfo(duration=10.0, width=1920, height=1080, fps=30.0, has_audio=True))


def test_a_tidy_cuts_each_filler_and_every_pause_down_to_a_breath():
    cuts = pauses_and_fillers(TALK, 10.0)

    assert cuts == [(0.0, 0.7), (1.1, 1.4), (3.05, 4.9), (7.65, 10.0)], (
        "the lead-in before the first word, the um, the pause after 'box.' with a breath each side, and the tail")
    assert (2.9, 5.0) not in cuts and not any(start < 6.4 < end for start, end in cuts), \
        "a short gap between sentences stays"
    assert pauses_and_fillers(TALK, None)[-1] == (7.65, float("inf")), "with no known end, all after the last word"
    assert pauses_and_fillers([], 10.0) == []


def test_words_are_found_where_they_are_said_even_as_speech_to_text_writes_them():
    assert said(TALK, "open the box") == (2.1, 2.9)
    assert said(TALK, "Open the box!") == (2.1, 2.9), "case and punctuation don't matter"
    assert said(TALK, "and here it's") == (5.0, 5.8), "close enough: most of it matches"
    assert said(TALK, "today … here it is") == (1.5, 6.2), "from the first words to the last"
    with pytest.raises(EditError, match="isn't heard there"):
        said(TALK, "a dragon appears")


def test_words_said_more_than_once_need_more_of_them_or_where_to_look():
    words = TALK + [(8.0, 8.4, "open"), (8.4, 8.5, "the"), (8.5, 9.0, "box.")]

    with pytest.raises(EditError, match=r"is said 2 times \(0:02.1, 0:08\)"):
        said(words, "open the box")
    assert said(words, "open the box", start=7.5) == (8.0, 9.0)
    assert said(words, "we open the box") == (1.9, 2.9)


def test_steps_by_their_words_land_on_the_video_as_the_steps_before_have_left_it():
    """A tidy first, then a trim by words: the trim's words are where they are once the pauses are gone."""
    steps = parse_steps([{"op": "tidy"}, {"op": "trim", "text": "today … here it is"},
                         {"op": "cover", "source": "box.jpg", "text": "the box"}])

    tidy, trim, cover = heard_steps(steps, TALK, 10.0)

    assert (tidy.op, tidy.ranges) == ("cut", ((0.0, 0.7), (1.1, 1.4), (3.05, 4.9), (7.65, 10.0)))
    # Once the cuts are gone "today" starts at 1.5 - 0.7 - 0.3 = 0.5, and "is." ends at 6.2 - 2.85 = 3.35.
    assert (trim.op, trim.start, trim.end, trim.text) == ("trim", pytest.approx(0.5), pytest.approx(3.35), "")
    assert (cover.start, cover.end, cover.source) == (pytest.approx(0.9), pytest.approx(2.4), "box.jpg"), \
        "where 'the box' is on the trimmed video, shown at least 1.5 s"
    sped = heard_steps(parse_steps([{"op": "speed", "factor": 2, "start": 0, "end": 4},
                                    {"op": "cut", "text": "and here it is"}]), TALK, 10.0)
    assert sped[1].ranges == ((3.0, 4.2),), "past the sped-up part, 2 s earlier"
    with pytest.raises(EditError, match=r"Step 1 \(tidy\): no speech was heard"):
        heard_steps(parse_steps([{"op": "tidy"}]), [], 10.0)


def test_a_cut_keeps_the_rest_in_one_pass_and_the_video_is_that_much_shorter():
    plan = build_plan([CLIP], [Step(op="cut", ranges=((1.0, 2.0), (4.0, 6.5)))], "output.mp4")

    assert plan.duration == pytest.approx(6.5)
    assert "select='gte(t,0)*lt(t,1)+gte(t,2)*lt(t,4)+gte(t,6.5)*lt(t,10)'" in plan.graph
    assert "setpts='PTS-(0+gte(T,2)*1+gte(T,6.5)*2.5)/TB'" in plan.graph, "each part moved back by what went"
    assert "aselect=" in plan.graph and "asetpts=N/SR/TB" in plan.graph, "the sound with it, unbroken"
    assert "split=" not in plan.graph, "never a piece per part"
    one = build_plan([CLIP], parse_steps([{"op": "cut", "start": 3, "end": 5}]), "output.mp4")
    assert one.duration == pytest.approx(8.0) and "Cut a part out" in one.summary
    with pytest.raises(EditError, match="nothing would be left"):
        build_plan([CLIP], [Step(op="cut", ranges=((0.0, 10.0),))], "output.mp4")


def test_a_cleaned_voice_is_cleaned_once_in_the_picture_run_and_brought_to_one_loudness():
    plan = build_plan([CLIP], parse_steps([{"op": "trim", "start": 1, "end": 8},
                                           {"op": "sound", "role": "original", "clean": True}]), "output.mp4")

    assert plan.graph.count(VOICE_CLEANUP) == 1
    assert plan.mix is not None and plan.mix.loudness == -14, "then levelled in the mix"
    assert "cleaned the voice" in plan.summary
    with pytest.raises(EditError, match="clean is for the video's own sound"):
        parse_steps([{"op": "sound", "role": "music", "source": "song.wav", "clean": True}])


def test_b_roll_is_read_only_as_long_as_it_shows_and_laid_over_the_picture_while_the_sound_goes_on():
    picture = InputVideo("cover-1.png", VideoInfo(1.0, 4032, 3024, 30.0, False, still=True))
    shelf = InputVideo("shelf.mp4", VideoInfo(20.0, 1280, 720, 25.0, True))
    plan = build_plan([CLIP], parse_steps([{"op": "cover", "source": "box.jpg", "start": 2, "end": 4.5},
                                           {"op": "cover", "source": "shelf.mp4", "start": 6, "end": 8},
                                           {"op": "text", "text": "Unboxing"}]),
                      "output.mp4", covers={"box.jpg": picture, "shelf.mp4": shelf})

    args = plan.args
    looped = args.index("-loop")
    assert args[looped - 2:looped + 6] == ["-f", "image2", "-loop", "1", "-framerate", "30", "-t", "2.5"]
    assert ["-ss", "0", "-t", "2", "-protocol_whitelist", "file"] == args[args.index("file:cover-1.png") + 1:][:6]
    assert "overlay=x=0:y=0:eof_action=pass:enable='between(t,2,4.5)'" in plan.graph
    assert "[2:v]setpts=PTS-STARTPTS+6/TB" in plan.graph, "the clip from its start, at its time"
    assert "[3:v]format=rgba" in plan.graph, "the words' sheet is read after the b-roll"
    assert plan.duration == 10.0 and plan.has_audio, "the video's own sound goes on under it"
    assert plan.enlarged == [(6.0, 8.0, 1.5)], "a smaller clip shown larger is noted for the check"
    with pytest.raises(EditError, match="cover needs when"):
        parse_steps([{"op": "cover", "source": "box.jpg"}])


def test_a_projects_scene_lays_its_b_roll_at_its_own_times_graded_as_its_footage():
    from row_bot.video_editing import Finish

    scenes = Step(op="scenes", finish=Finish(grade="warm"), scenes=[
        SceneCut(input=0, start=0.0, end=3.0), SceneCut(input=0, start=5.0, end=9.0, covers=(("box.jpg", 1.0, 2.5),))])
    picture = InputVideo("cover-1.png", VideoInfo(1.0, 1920, 1080, 30.0, False, still=True))

    plan = build_plan([CLIP], [scenes], "output.mp4", covers={"box.jpg": picture})

    assert "enable='between(t,4,5.5)'" in plan.graph, "a second into the second scene, which starts at 3 s"
    cover = next(line for line in plan.graph.split(";\n") if line.startswith("[1:v]"))
    assert "colorbalance=" in cover, "graded like the footage it covers"


def test_a_clips_moments_are_its_shots_and_its_stretches_of_speech():
    found = moments([4.0], TALK, 10.0)

    assert [(start, end) for start, end, _words in found] == [(0.0, 4.0), (4.0, 6.35), (6.35, 10.0)], \
        "a new shot just after a sentence ends is where that moment ends"
    assert [words for _start, _end, words in found] == [
        "So, um, today we open the box.", "And here it is.", "Thanks for watching."]
    assert [(start, end) for start, end, _words in moments([1.5], TALK, 10.0)][:2] == [(0.0, 1.5), (1.5, 3.95)], \
        "a shot change mid-sentence starts a moment of its own"
    assert len(moments([], [], 3600.0)) <= MAX_FRAMES and moments([], [], 50.0) == [
        (0.0, 25.0, ""), (25.0, 50.0, "")], "a long stretch with nowhere to end is split evenly"
    many = moments([float(at) for at in range(2, 400, 2)], [], 400.0)
    assert len(many) == MAX_FRAMES and many[0][0] == 0.0 and many[-1][1] == 400.0, "neighbours joined to fit"


def test_a_long_winded_moment_quotes_only_the_start_of_what_is_said():
    """Up to 48 moments are listed: a clip that talks without a pause never makes the listing huge."""
    from row_bot import video_edits
    from row_bot.video_editing import MOMENT_WORDS

    rambling = [(index * 0.3, index * 0.3 + 0.25, f"word{index}") for index in range(90)]
    [(_start, _end, quoted)] = moments([], rambling, 27.0)

    assert len(quoted) <= MOMENT_WORDS and quoted.endswith("…") and quoted.startswith("word0 word1 word2")
    assert quoted[:-1].split()[-1] in {word for _start, _end, word in rambling}, "cut between words, not in one"
    older = video_edits.VideoEdit(edit_id="edit-0000c4c4", conversation_id="conversation-a", kind="frames",
                                  summary="Moments in talk.mp4", output_name="talk.mp4", started_at=1.0,
                                  status="saved", moments=[[0.0, 27.0, " ".join(word for *_times, word in rambling)]])
    listed = video_edits.summary(older).splitlines()[1]
    assert len(listed) < MOMENT_WORDS + 20 and listed.endswith("…”"), "an edit kept before the cap is listed capped"


def test_where_each_shot_starts_is_read_from_ffmpegs_scene_score():
    written = ("frame:0    pts:60      pts_time:2\nlavfi.scene_score=1.000000\n"
               "frame:1    pts:110     pts_time:3.666667\nlavfi.scene_score=0.512000\n")

    assert read_shots(written) == [2.0, 3.667]
    assert read_shots("") == []
