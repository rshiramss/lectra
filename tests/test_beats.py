from lectra.align import Cue, SlideSegment
from lectra.beats import clean_beats, extract_beats, words_in_segment
from lectra.ingest import Slide
from lectra.manimgen import build_prompt
from lectra.transcribe import Word

SEGMENT = SlideSegment(number=2, start=10.0, end=40.0, cues=(Cue(12.0, "old sparse cue"),))
SLIDE = Slide(2, "Ripple carry")
WORDS = [Word(f"w{i}", float(i), i + 0.5) for i in range(50)]


def raw(time, idea="idea", visual="box"):
    return {"time": time, "idea": idea, "visual": visual}


def test_words_in_segment_keeps_only_that_slides_time_range():
    selected = words_in_segment(WORDS, SEGMENT)

    assert selected[0].start == 10.0 and selected[-1].start == 39.0


def test_clean_beats_drops_out_of_range_and_sorts():
    beats = clean_beats([raw(30.0), raw(5.0), raw(15.0), raw(45.0)], SEGMENT)

    assert [beat.time for beat in beats] == [15.0, 30.0]


def test_clean_beats_drops_beats_too_close_to_the_previous_one():
    beats = clean_beats([raw(15.0), raw(15.5), raw(20.0)], SEGMENT)

    assert [beat.time for beat in beats] == [15.0, 20.0]


def test_extract_beats_replaces_cues_and_keeps_visual_hint():
    reply = '{"beats": [{"time": 14.0, "idea": "carry", "visual": "arrow between adders"}]}'

    refined = extract_beats(SLIDE, SEGMENT, WORDS, ask=lambda _prompt: reply)

    assert refined.cues == (Cue(14.0, "carry", "arrow between adders"),)
    assert (refined.start, refined.end) == (10.0, 40.0)


def test_extract_beats_retries_when_no_usable_beats():
    replies = iter(['{"beats": []}', '{"beats": [{"time": 20.0, "idea": "x", "visual": "y"}]}'])

    refined = extract_beats(SLIDE, SEGMENT, WORDS, ask=lambda _prompt: next(replies))

    assert len(refined.cues) == 1


def test_scene_prompt_demands_an_animation_per_beat_with_visual_hint():
    segment = SlideSegment(2, 10.0, 40.0, (Cue(14.0, "carry", "arrow between adders"),))

    prompt = build_prompt(SLIDE, segment)

    assert "arrow between adders" in prompt and "EVERY" in prompt


def test_scene_prompt_forbids_inventing_figures_the_narration_never_says():
    prompt = build_prompt(SLIDE, SEGMENT)

    assert "Do not invent" in prompt
