"""Render scenes with Manim and assemble the final video with ffmpeg."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from lectra.scene_env import DURATION_ENV_VAR

QUALITY_FLAGS = {"low": "-ql", "medium": "-qm", "high": "-qh"}
FRAME_RATE = 30
ERROR_TAIL_CHARS = 3000
SCENE_LINE_CONTEXT = 4  # a frame's file line plus the source lines under it
RENDER_TIMEOUT_BASE_SECONDS = 600  # a scene can hang (seen: 26 min at full CPU)
RENDER_TIMEOUT_PER_SCENE_SECOND = 10


class RenderError(RuntimeError):
    """Raised with the tail of Manim's output so the LLM can repair the scene."""


def probe_duration(media_path: Path) -> float:
    completed = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(media_path),
        ],
        capture_output=True, text=True, check=True,
    )
    return float(completed.stdout.strip())


def render_scene(
    scene_file: Path, class_name: str, duration: float, media_dir: Path, quality: str
) -> Path:
    # Use the venv's own Manim: a global install can pair Manim with an
    # incompatible PyAV and fail to stitch frames ("Invalid data found").
    timeout = RENDER_TIMEOUT_BASE_SECONDS + RENDER_TIMEOUT_PER_SCENE_SECOND * duration
    try:
        completed = _run_manim(scene_file, class_name, duration, media_dir, quality, timeout)
    except subprocess.TimeoutExpired as error:
        raise RenderError(
            f"Manim render timed out after {timeout:.0f}s (likely an infinite loop or "
            f"runaway animation in {scene_file.name}); simplify the scene."
        ) from error
    if completed.returncode != 0:
        raise RenderError(error_report(completed.stdout + completed.stderr, scene_file))

    matches = sorted(media_dir.rglob(f"{class_name}.mp4"), key=lambda p: p.stat().st_mtime)
    if not matches:
        raise RenderError(f"Manim produced no {class_name}.mp4 under {media_dir}")
    return matches[-1]


def _run_manim(
    scene_file: Path, class_name: str, duration: float, media_dir: Path, quality: str,
    timeout: float,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable, "-m", "manim", QUALITY_FLAGS[quality], "--fps", str(FRAME_RATE),
            "--media_dir", str(media_dir), "-o", f"{class_name}.mp4",
            str(scene_file), class_name,
        ],
        env={**os.environ, DURATION_ENV_VAR: f"{duration:.3f}", "PYTHONPATH": _src_path()},
        capture_output=True, text=True, timeout=timeout,
    )


def error_report(output: str, scene_file: Path, tail_chars: int = ERROR_TAIL_CHARS) -> str:
    """Manim's tail, plus the lines that point into the scene file itself.

    A traceback from deep inside Manim buries the one frame that names the
    offending line of generated code. The repair prompt needs that frame, so
    it is pulled out of the part of the output the tail would cut off.
    """
    lines = output.splitlines()
    tail = output[-tail_chars:]
    scene_lines = []
    for index, line in enumerate(lines):
        if scene_file.name in line and line not in tail:
            scene_lines.extend(lines[index : index + SCENE_LINE_CONTEXT])
    if not scene_lines:
        return tail
    return "\n".join(scene_lines) + "\n...\n" + tail


def fit_clip(clip: Path, duration: float, destination: Path) -> Path:
    """Re-encode to exactly `duration`: hold the last frame if short, cut if long."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(clip),
            "-vf", f"tpad=stop_mode=clone:stop_duration={duration:.3f},fps={FRAME_RATE},format=yuv420p",
            "-t", f"{duration:.3f}", "-c:v", "libx264", "-an", str(destination),
        ],
        check=True,
    )
    return destination


def concat_and_mux(clips: list[Path], audio: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    list_file = destination.with_suffix(".clips.txt")
    list_file.write_text("".join(f"file '{clip.resolve()}'\n" for clip in clips))
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
            "-i", str(list_file), "-i", str(audio),
            "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-shortest",
            str(destination),
        ],
        check=True,
    )
    return destination


def _src_path() -> str:
    return str(Path(__file__).resolve().parent.parent)
