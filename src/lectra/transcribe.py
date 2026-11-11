"""Transcribe an audio file into words with start/end timestamps.

Uses mlx-whisper (Whisper on Apple Silicon via MLX). Word timings feed the
alignment step, which maps spoken sections to slides and Manim cues.
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger("lectra.transcribe")

# Good speed/accuracy balance for long technical podcasts. Downloaded on first use.
DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"


@dataclass(frozen=True)
class Word:
    text: str
    start: float  # seconds from audio start
    end: float


def words_from_result(result: dict) -> list[Word]:
    """Flatten Whisper's segments into one ordered list of words."""
    return [
        Word(text=raw["word"].strip(), start=raw["start"], end=raw["end"])
        for segment in result.get("segments", [])
        for raw in segment.get("words", [])
    ]


def transcribe_words(audio_path: Path, model: str = DEFAULT_MODEL) -> list[Word]:
    import mlx_whisper  # heavy import, kept out of module load

    result = mlx_whisper.transcribe(
        str(audio_path),
        path_or_hf_repo=model,
        word_timestamps=True,
        # Each window is decoded independently, so one hallucinated loop
        # cannot poison the rest of a 30+ minute episode.
        condition_on_previous_text=False,
    )
    return words_from_result(result)


def save_words(words: list[Word], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(word) for word in words], indent=2))


def load_words(path: Path) -> list[Word]:
    return [Word(**raw) for raw in json.loads(path.read_text())]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("audio", type=Path)
    parser.add_argument("--output", type=Path, default=Path("words.json"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    arguments = parser.parse_args()

    words = transcribe_words(arguments.audio, arguments.model)
    save_words(words, arguments.output)
    logger.info("Wrote %d words to %s", len(words), arguments.output)


if __name__ == "__main__":
    main()
