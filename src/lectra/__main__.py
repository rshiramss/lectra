"""Command line: python -m lectra deck.pptx audio.m4a"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from lectra.pipeline import run_pipeline
from lectra.render import QUALITY_FLAGS
from lectra.transcribe import DEFAULT_MODEL as DEFAULT_WHISPER_MODEL


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(prog="lectra", description=__doc__)
    parser.add_argument("deck", type=Path, help=".pptx or .pdf slide deck")
    parser.add_argument("audio", type=Path, help="Podcast audio of the deck")
    parser.add_argument("--out-dir", type=Path, default=Path("build"))
    parser.add_argument("--quality", choices=QUALITY_FLAGS, default="medium")
    parser.add_argument(
        "--whisper-model",
        default=DEFAULT_WHISPER_MODEL,
        help="mlx-whisper model repo (large-v3-turbo downloads ~1.6GB on first use)",
    )
    arguments = parser.parse_args()

    for path in (arguments.deck, arguments.audio):
        if not path.is_file():
            raise SystemExit(f"File not found: {path}")

    run_pipeline(
        arguments.deck,
        arguments.audio,
        arguments.out_dir,
        arguments.quality,
        arguments.whisper_model,
    )


if __name__ == "__main__":
    main()
