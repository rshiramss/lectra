"""One command: source material -> NotebookLM podcast -> narrated Manim video.

Re-running resumes: an audio file already in <out-dir>/audio is reused, and
the pipeline skips every stage whose artifact exists.
"""

from __future__ import annotations

import argparse
import fcntl
import logging
import os
from contextlib import contextmanager
from pathlib import Path

from lectra.notebooks import DEFAULT_NOTEBOOK_FILE, create_notebook
from lectra.pipeline import run_pipeline
from lectra.podcast import (
    AUDIO_LENGTHS,
    NOTEBOOK_URL_ENV_VAR,
    TECHNICAL_FOCUS_PROMPT,
    generate_audio_overview,
)
from lectra.render import QUALITY_FLAGS

logger = logging.getLogger("lectra.lecture")

PODCAST_LOCK_FILE = Path.home() / ".lectra" / "podcast.lock"
AUDIO_SUFFIXES = (".m4a", ".mp3", ".wav", ".mp4")
DEFAULT_LENGTH = "Default"
# Remembers a notebook made by --new-notebook so a resumed run reuses it.
RUN_NOTEBOOK_FILENAME = "notebook_url"


def find_notebook_url(cli_value: str | None) -> str:
    """CLI flag, then $LECTRA_NOTEBOOK_URL, then ~/.lectra/notebook_url."""
    url = cli_value or os.environ.get(NOTEBOOK_URL_ENV_VAR)
    if not url and DEFAULT_NOTEBOOK_FILE.is_file():
        url = DEFAULT_NOTEBOOK_FILE.read_text().strip()
    if not url:
        raise SystemExit(
            f"No NotebookLM notebook. Pass --notebook-url or --new-notebook, set "
            f"{NOTEBOOK_URL_ENV_VAR}, or write the URL to {DEFAULT_NOTEBOOK_FILE}"
        )
    return url


def get_run_notebook(out_dir: Path, title: str | None, headless: bool) -> str:
    """Return the notebook this run created, creating it on the first attempt.

    With no title the notebook stays untitled until its sources are added.
    """
    saved_url_file = out_dir / RUN_NOTEBOOK_FILENAME
    if saved_url_file.is_file():
        url = saved_url_file.read_text().strip()
        logger.info("Reusing the notebook this run created earlier: %s", url)
        return url

    url = create_notebook(title, headless)
    out_dir.mkdir(parents=True, exist_ok=True)
    saved_url_file.write_text(url + "\n")
    return url


@contextmanager
def podcast_lock():
    """One podcast run at a time per machine.

    Runs from different repos share one headless Brave profile and one
    notebook; two at once close each other's browser. Released automatically
    if the process dies.
    """
    PODCAST_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PODCAST_LOCK_FILE, "w") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger.info("Another lecture is generating its podcast; waiting for it ...")
            fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def find_existing_audio(audio_dir: Path) -> Path | None:
    if not audio_dir.is_dir():
        return None
    candidates = [p for p in audio_dir.iterdir() if p.suffix.lower() in AUDIO_SUFFIXES]
    return max(candidates, key=lambda p: p.stat().st_mtime, default=None)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="lectra.lecture", description=__doc__.splitlines()[0])
    parser.add_argument("sources", type=Path, nargs="+",
                        help=".pdf/.pptx/.md; the FIRST is also the slide/outline source")
    parser.add_argument("--out-dir", type=Path, default=Path("lecture_build"))
    parser.add_argument("--length", choices=AUDIO_LENGTHS, default=DEFAULT_LENGTH,
                        help="Podcast length: Short ~6min, Default ~15-20, Long ~40+")
    parser.add_argument("--focus-file", type=Path, help="Focus prompt for the AI hosts")
    parser.add_argument("--quality", choices=QUALITY_FLAGS, default="medium")
    notebook = parser.add_mutually_exclusive_group()
    notebook.add_argument("--notebook-url", help="Existing notebook to use")
    notebook.add_argument("--new-notebook", action="store_true",
                          help="Create a fresh notebook for this run")
    parser.add_argument("--notebook-title",
                        help="Title for --new-notebook (default: named after its sources)")
    parser.add_argument("--headed", action="store_true",
                        help="Show Brave (needed once to sign in to Google)")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    arguments = parse_arguments()
    missing = [p for p in arguments.sources if not p.is_file()]
    if missing:
        raise SystemExit(f"Source not found: {missing[0]}")

    audio_dir = arguments.out_dir / "audio"
    audio = find_existing_audio(audio_dir)
    if audio:
        logger.info("Reusing audio %s", audio)
    else:
        focus_prompt = (arguments.focus_file.read_text().strip()
                        if arguments.focus_file else TECHNICAL_FOCUS_PROMPT)
        with podcast_lock():
            if arguments.new_notebook:
                notebook_url = get_run_notebook(
                    arguments.out_dir, arguments.notebook_title, headless=not arguments.headed
                )
            else:
                notebook_url = find_notebook_url(arguments.notebook_url)
            audio = generate_audio_overview(
                source_paths=[p.resolve() for p in arguments.sources],
                notebook_url=notebook_url,
                output_dir=audio_dir,
                focus_prompt=focus_prompt,
                length=arguments.length,
                should_generate=True,
                should_download_only=False,
                headless=not arguments.headed,
            )
    video = run_pipeline(arguments.sources[0], audio, arguments.out_dir / "build", arguments.quality)
    logger.info("Lecture video: %s", video)


if __name__ == "__main__":
    main()
