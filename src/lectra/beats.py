"""Find the "beats" of each slide: moments where the narrator introduces a new idea.

The coarse alignment only says when each slide starts. This second pass reads
one slide's slice of the transcript and marks a beat every time a new idea,
term, step, or example is introduced, plus a visual hint for what to show.
The scene generator then animates every beat, so the screen changes when the
narration does.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable

from lectra.align import Cue, SlideSegment, format_transcript
from lectra.ingest import Slide
from lectra.llm import LlmError, ask_claude, extract_json
from lectra.transcribe import Word

TARGET_SECONDS_PER_BEAT = 10
MIN_SECONDS_BETWEEN_BEATS = 3.0
WORDS_PER_BEAT_LINE = 8
MAX_ATTEMPTS = 3


class BeatError(ValueError):
    pass


def words_in_segment(words: list[Word], segment: SlideSegment) -> list[Word]:
    return [word for word in words if segment.start <= word.start < segment.end]


def clean_beats(raw_beats: list[dict], segment: SlideSegment) -> tuple[Cue, ...]:
    """Keep beats inside the segment, in time order, at least a few seconds apart."""
    in_range = sorted(
        (
            Cue(float(raw["time"]), str(raw["idea"]), str(raw.get("visual", "")))
            for raw in raw_beats
            if segment.start <= float(raw["time"]) < segment.end
        ),
        key=lambda cue: cue.time,
    )
    spaced: list[Cue] = []
    for cue in in_range:
        if not spaced or cue.time - spaced[-1].time >= MIN_SECONDS_BETWEEN_BEATS:
            spaced.append(cue)
    return tuple(spaced)


def build_beats_prompt(slide: Slide, segment: SlideSegment, words: list[Word]) -> str:
    beat_count = max(3, round(segment.duration / TARGET_SECONDS_PER_BEAT))
    transcript = format_transcript(words_in_segment(words, segment), WORDS_PER_BEAT_LINE)
    return f"""You plan the visuals for a 3Blue1Brown-style explainer video.
A narrator is speaking over one lecture slide. Mark every moment where the
narrator introduces a NEW idea, term, step, example, or contrast, so the screen
can change at exactly that moment.

SLIDE {slide.number} TEXT:
{slide.text}

NARRATION (each line starts with its time in seconds; this slide runs
{segment.start:.1f}s to {segment.end:.1f}s):
{transcript}

Give about {beat_count} beats (more if the narrator moves fast), spread across the
whole slide, never more than ~15 seconds apart. For each beat:
- time: when the narrator STARTS saying the idea (use the line times)
- idea: the idea in under 10 words
- visual: ONE concrete on-screen action, in 3Blue1Brown style: a shape or
  diagram appearing, one object morphing into another, a term being highlighted
  or circled, an arrow showing flow, a worked number example advancing a step.
  Say what appears and which earlier object it connects to.

Reply with ONLY JSON:
{{"beats": [{{"time": 12.5, "idea": "...", "visual": "..."}}]}}"""


def extract_beats(
    slide: Slide,
    segment: SlideSegment,
    words: list[Word],
    ask: Callable[[str], str] = ask_claude,
) -> SlideSegment:
    """Return the segment with its cues replaced by narration-aligned beats."""
    prompt = build_beats_prompt(slide, segment, words)
    last_error = ""
    for _ in range(MAX_ATTEMPTS):
        retry_note = f"\n\nYour previous answer was rejected: {last_error}" if last_error else ""
        try:
            beats = clean_beats(extract_json(ask(prompt + retry_note))["beats"], segment)
            if not beats:
                raise BeatError("No beats fell inside the slide's time range")
            return replace(segment, cues=beats)
        except (BeatError, LlmError, KeyError, ValueError, TypeError) as error:
            last_error = str(error)
    raise BeatError(f"Slide {slide.number}: beats failed after {MAX_ATTEMPTS} attempts: {last_error}")
