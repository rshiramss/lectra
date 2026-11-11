"""Read a slide deck (.pptx or .pdf) into per-slide text."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Slide:
    number: int  # 1-based, matches what a human sees
    text: str


def load_slides(deck_path: Path) -> list[Slide]:
    suffix = deck_path.suffix.lower()
    if suffix == ".pptx":
        return _load_pptx(deck_path)
    if suffix == ".pdf":
        return _load_pdf(deck_path)
    if suffix == ".md":
        return _load_outline(deck_path)
    raise ValueError(f"Unsupported deck type {suffix!r}; use .pptx, .pdf or .md")


def _load_outline(outline_path: Path) -> list[Slide]:
    """A markdown outline: each top-level '# ' heading starts one unit.

    For material that has no slides (a study guide, notes), so the video can
    still be split into sections.
    """
    units: list[list[str]] = []
    for line in outline_path.read_text().splitlines():
        if line.startswith("# "):
            units.append([line[2:].strip()])
        elif units:
            units[-1].append(line)
    return [Slide(number, "\n".join(lines).strip()) for number, lines in enumerate(units, start=1)]


def _load_pptx(deck_path: Path) -> list[Slide]:
    from pptx import Presentation

    slides = []
    for number, slide in enumerate(Presentation(str(deck_path)).slides, start=1):
        lines = [
            shape.text_frame.text.strip()
            for shape in slide.shapes
            if shape.has_text_frame and shape.text_frame.text.strip()
        ]
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                lines.append(f"[speaker notes] {notes}")
        slides.append(Slide(number, "\n".join(lines)))
    return slides


def _load_pdf(deck_path: Path) -> list[Slide]:
    import pymupdf

    with pymupdf.open(deck_path) as document:
        return [
            Slide(number, page.get_text().strip())
            for number, page in enumerate(document, start=1)
        ]
