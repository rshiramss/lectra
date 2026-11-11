"""Map the transcript onto slides: when does each slide's discussion begin?

An LLM reads the slide texts plus the timestamped transcript and returns, for
each slide, the time its discussion starts and a few key phrases with times.
We validate that answer and turn it into a Timeline of contiguous segments.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from lectra.ingest import Slide
from lectra.llm import LlmError, ask_claude, extract_json
from lectra.transcribe import Word

WORDS_PER_TRANSCRIPT_LINE = 15
MAX_CUES_PER_SLIDE = 6
MAX_ATTEMPTS = 3


class AlignmentError(ValueError):
    pass


@dataclass(frozen=True)
class Cue:
    time: float  # seconds from audio start
    phrase: str
    visual: str = ""  # what to show when this idea is spoken (set by beats.py)


@dataclass(frozen=True)
class SlideSegment:
    number: int
    start: float
    end: float
    cues: tuple[Cue, ...]

    @property
    def duration(self) -> float:
        return self.end - self.start


def format_transcript(
    words: list[Word], words_per_line: int = WORDS_PER_TRANSCRIPT_LINE
) -> str:
    """One line per `words_per_line` words, each prefixed with its start time in seconds."""
    lines = []
    for first in range(0, len(words), words_per_line):
        chunk = words[first : first + words_per_line]
        lines.append(f"[{chunk[0].start:.1f}] " + " ".join(word.text for word in chunk))
    return "\n".join(lines)


def build_timeline(
    answer: dict, slides: list[Slide], audio_duration: float
) -> list[SlideSegment]:
    """Validate the LLM answer and build contiguous segments covering the audio."""
    entries = {entry["number"]: entry for entry in answer.get("slides", [])}
    missing = [slide.number for slide in slides if slide.number not in entries]
    if missing:
        raise AlignmentError(f"Answer is missing slides {missing}")

    # Slide 1 owns any intro chatter, so its segment starts at 0.
    starts = [0.0] + [float(entries[slide.number]["start"]) for slide in slides[1:]]
    for previous, current in zip(starts, starts[1:]):
        if current <= previous:
            raise AlignmentError("Slide start times must strictly increase")
    if starts[-1] >= audio_duration:
        raise AlignmentError("Last slide starts after the audio ends")

    ends = starts[1:] + [audio_duration]
    return [
        SlideSegment(
            number=slide.number,
            start=start,
            end=end,
            cues=_clean_cues(entries[slide.number].get("cues", []), start, end),
        )
        for slide, start, end in zip(slides, starts, ends)
    ]


def _clean_cues(raw_cues: list[dict], start: float, end: float) -> tuple[Cue, ...]:
    cues = [
        Cue(float(raw["time"]), str(raw["phrase"]))
        for raw in raw_cues
        if start <= float(raw["time"]) < end
    ]
    return tuple(sorted(cues, key=lambda cue: cue.time)[:MAX_CUES_PER_SLIDE])


def build_prompt(slides: list[Slide], words: list[Word]) -> str:
    slide_block = "\n\n".join(f"### Slide {s.number}\n{s.text}" for s in slides)
    return f"""You align a podcast transcript to the slides it discusses.

SLIDES:
{slide_block}

TRANSCRIPT (each line starts with its time in seconds):
{format_transcript(words)}

For every slide, give the time (seconds) when the hosts START discussing it,
and up to {MAX_CUES_PER_SLIDE} cues: moments inside that slide's discussion
where something visual should appear or be highlighted. A cue phrase is the
short idea being spoken (under 8 words), with the time it is spoken.

Rules: slide starts must strictly increase; every slide must appear; the hosts
may skip or reorder, so choose the best order-preserving fit.
Reply with ONLY JSON:
{{"slides": [{{"number": 1, "start": 0.0, "cues": [{{"time": 4.2, "phrase": "..."}}]}}]}}"""


def align(
    slides: list[Slide],
    words: list[Word],
    audio_duration: float,
    ask: Callable[[str], str] = ask_claude,
) -> list[SlideSegment]:
    """Ask the LLM, retrying with the validation error if its answer is unusable."""
    prompt = build_prompt(slides, words)
    last_error = ""
    for _ in range(MAX_ATTEMPTS):
        retry_note = f"\n\nYour previous answer was rejected: {last_error}" if last_error else ""
        try:
            return build_timeline(extract_json(ask(prompt + retry_note)), slides, audio_duration)
        except (AlignmentError, LlmError, KeyError, ValueError) as error:
            last_error = str(error)
    raise AlignmentError(f"Alignment failed after {MAX_ATTEMPTS} attempts: {last_error}")


def save_timeline(timeline: list[SlideSegment], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(segment) for segment in timeline], indent=2))


def load_timeline(path: Path) -> list[SlideSegment]:
    return [
        SlideSegment(
            number=raw["number"],
            start=raw["start"],
            end=raw["end"],
            cues=tuple(Cue(**cue) for cue in raw["cues"]),
        )
        for raw in json.loads(path.read_text())
    ]
