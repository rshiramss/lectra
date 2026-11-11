from pathlib import Path

import pytest

from lectra.align import AlignmentError, Cue, align, build_timeline, format_transcript
from lectra.ingest import Slide, load_slides
from lectra.llm import LlmError, extract_code_block, extract_json
from lectra.manimgen import SceneSourceError, validate_source
from lectra.transcribe import Word

SLIDES = [Slide(1, "Counters"), Slide(2, "Ripple"), Slide(3, "Sync")]


def answer(starts, cues=None):
    return {
        "slides": [
            {"number": i + 1, "start": s, "cues": (cues or {}).get(i + 1, [])}
            for i, s in enumerate(starts)
        ]
    }


def test_timeline_is_contiguous_and_slide_one_starts_at_zero():
    timeline = build_timeline(answer([3.0, 10.0, 25.0]), SLIDES, audio_duration=40.0)

    assert [(s.start, s.end) for s in timeline] == [(0.0, 10.0), (10.0, 25.0), (25.0, 40.0)]


def test_timeline_rejects_non_increasing_starts():
    with pytest.raises(AlignmentError):
        build_timeline(answer([0.0, 10.0, 10.0]), SLIDES, audio_duration=40.0)


def test_timeline_rejects_missing_slide():
    two_of_three = {"slides": answer([0.0, 10.0, 20.0])["slides"][:2]}

    with pytest.raises(AlignmentError):
        build_timeline(two_of_three, SLIDES, audio_duration=40.0)


def test_cues_outside_their_segment_are_dropped():
    cues = {2: [{"time": 5.0, "phrase": "early"}, {"time": 12.0, "phrase": "ok"}]}

    timeline = build_timeline(answer([0.0, 10.0, 25.0], cues), SLIDES, audio_duration=40.0)

    assert timeline[1].cues == (Cue(12.0, "ok"),)


def test_align_retries_after_invalid_answer():
    replies = iter(['{"slides": []}', '{"slides": ' + str(answer([0, 10, 20])["slides"]).replace("'", '"') + "}"])

    timeline = align(SLIDES, [Word("hi", 0.0, 0.2)], 30.0, ask=lambda _prompt: next(replies))

    assert len(timeline) == 3


def test_format_transcript_prefixes_line_start_times():
    words = [Word(f"w{i}", float(i), i + 0.5) for i in range(20)]

    lines = format_transcript(words).splitlines()

    assert lines[0].startswith("[0.0] w0 w1") and lines[1].startswith("[15.0] w15")


def test_extract_json_handles_fences_and_chatter():
    assert extract_json('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(LlmError):
        extract_json("no json here")


def test_extract_code_block_returns_unfenced_text_as_is():
    assert extract_code_block("x = 1") == "x = 1"


def test_validate_source_requires_expected_class():
    validate_source("class Slide2Scene:\n    pass", 2)
    with pytest.raises(SceneSourceError):
        validate_source("class Other:\n    pass", 2)
    with pytest.raises(SceneSourceError):
        validate_source("def (:", 2)


def test_load_pptx_slides_reads_title_and_body():
    slides = load_slides(Path(__file__).parent.parent / "sample_deck.pptx")

    assert [s.number for s in slides] == [1, 2]
    assert "Binary Counters" in slides[0].text and "Ripple" in slides[1].text


def test_load_markdown_outline_splits_units_on_top_level_headings(tmp_path):
    outline = tmp_path / "outline.md"
    outline.write_text("# Vocabulary\nsprouts\nritual\n# Question 1\nMengzi vs Xunzi\n")

    slides = load_slides(outline)

    assert [s.number for s in slides] == [1, 2]
    assert slides[0].text.startswith("Vocabulary") and "Mengzi vs Xunzi" in slides[1].text
