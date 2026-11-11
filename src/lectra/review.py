"""Check a rendered scene for wasted screen space.

An LLM writes each scene without seeing it, so some come out as a title and a
few boxes in the top strip of an otherwise empty frame. We measure how much of
the frame the content covers, so the pipeline can ask for a denser rewrite.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from lectra.render import probe_duration

BACKGROUND_BRIGHTNESS = 24  # pixels at or below this are background (0-255)
SPARSE_AREA_FRACTION = 0.45  # content box covering less than this is "sparse"
SAMPLE_END_MARGIN_SECONDS = 0.3  # stay clear of the last frame: seeking past it yields nothing
SAMPLE_POSITIONS = (0.6, 0.8, 0.97)  # fractions of the clip; scenes build up, so look late


def content_area_fraction(frame: np.ndarray) -> float:
    """Fraction of the frame covered by the bounding box of non-background pixels."""
    is_content = frame.max(axis=2) > BACKGROUND_BRIGHTNESS
    rows, columns = np.where(is_content)
    if rows.size == 0:
        return 0.0
    box_height = rows.max() - rows.min() + 1
    box_width = columns.max() - columns.min() + 1
    return float(box_height * box_width) / (frame.shape[0] * frame.shape[1])


def is_sparse(area_fraction: float) -> bool:
    return area_fraction < SPARSE_AREA_FRACTION


def clip_coverage(clip: Path) -> float:
    """Highest content coverage among frames sampled late in the clip.

    Uses the clip's own length: Manim's raw clip can be shorter than the
    slide's slot (the pipeline pads it later), and seeking past its end
    yields no frame.
    """
    last_safe_time = max(probe_duration(clip) - SAMPLE_END_MARGIN_SECONDS, 0.0)
    return max(
        content_area_fraction(_read_frame(clip, min(last_safe_time * position, last_safe_time)))
        for position in SAMPLE_POSITIONS
    )


def _read_frame(clip: Path, time_seconds: float) -> np.ndarray:
    with tempfile.TemporaryDirectory() as scratch:
        frame_path = Path(scratch) / "frame.png"
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error", "-ss", f"{time_seconds:.3f}",
                "-i", str(clip), "-frames:v", "1", str(frame_path),
            ],
            check=True,
        )
        if not frame_path.exists():
            raise RuntimeError(f"No frame at {time_seconds:.2f}s in {clip}")
        return np.array(Image.open(frame_path).convert("RGB"))
