"""End-to-end: deck + podcast audio -> narrated Manim video.

Each stage writes an artifact into out_dir and is skipped if it already
exists, so you can hand-edit timeline.json or a scene file and re-run. Delete
an artifact to redo its stage.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from lectra.align import SlideSegment, align, load_timeline, save_timeline
from lectra.beats import extract_beats
from lectra.ingest import Slide, load_slides
from lectra.llm import LlmError
from lectra.manimgen import (
    SceneSourceError,
    densify_scene_source,
    generate_scene_source,
    repair_scene_source,
    scene_class_name,
    write_scene_file,
)
from lectra.render import RenderError, concat_and_mux, fit_clip, probe_duration, render_scene
from lectra.review import clip_coverage, is_sparse
from lectra.transcribe import DEFAULT_MODEL as DEFAULT_WHISPER_MODEL
from lectra.transcribe import load_words, save_words, transcribe_words

logger = logging.getLogger("lectra.pipeline")

MAX_RENDER_REPAIRS = 2


def run_pipeline(
    deck: Path,
    audio: Path,
    out_dir: Path,
    quality: str = "medium",
    whisper_model: str = DEFAULT_WHISPER_MODEL,
) -> Path:
    slides = load_slides(deck)
    coarse_timeline = _load_or_build_timeline(slides, audio, out_dir, whisper_model)
    timeline = _load_or_build_beats(slides, coarse_timeline, out_dir)

    clips = [
        _render_slide(slide, segment, out_dir, quality)
        for slide, segment in zip(slides, timeline)
    ]
    final_video = concat_and_mux(clips, audio, out_dir / "lecture.mp4")
    logger.info("Done: %s", final_video)
    return final_video


def _load_or_build_timeline(
    slides: list[Slide], audio: Path, out_dir: Path, whisper_model: str
) -> list[SlideSegment]:
    timeline_path = out_dir / "timeline.json"
    if timeline_path.exists():
        return load_timeline(timeline_path)

    words_path = out_dir / "words.json"
    if words_path.exists():
        words = load_words(words_path)
    else:
        logger.info("Transcribing %s ...", audio.name)
        words = transcribe_words(audio, whisper_model)
        save_words(words, words_path)

    logger.info("Aligning transcript to %d slides ...", len(slides))
    timeline = align(slides, words, probe_duration(audio))
    save_timeline(timeline, timeline_path)
    return timeline


def _load_or_build_beats(
    slides: list[Slide], timeline: list[SlideSegment], out_dir: Path
) -> list[SlideSegment]:
    """Replace the coarse cues with per-idea beats (needs words.json from the timeline stage)."""
    beats_path = out_dir / "beats.json"
    if beats_path.exists():
        return load_timeline(beats_path)

    words = load_words(out_dir / "words.json")
    refined = []
    for slide, segment in zip(slides, timeline):
        logger.info("Finding beats for slide %d ...", slide.number)
        refined.append(extract_beats(slide, segment, words))
    save_timeline(refined, beats_path)
    return refined


def _render_slide(slide: Slide, segment: SlideSegment, out_dir: Path, quality: str) -> Path:
    fitted_path = out_dir / "clips" / f"slide_{slide.number:02d}.mp4"
    if fitted_path.exists():
        return fitted_path

    scene_path = out_dir / "scenes" / f"slide_{slide.number:02d}.py"
    if scene_path.exists():
        source = scene_path.read_text()
    else:
        logger.info("Writing scene for slide %d ...", slide.number)
        source = generate_scene_source(slide, segment)
        write_scene_file(source, slide.number, out_dir / "scenes")

    raw_clip = _render_with_repairs(source, slide.number, segment, out_dir, quality)
    raw_clip = _densify_if_sparse(raw_clip, source, slide, segment, out_dir, quality)
    return fit_clip(raw_clip, segment.duration, fitted_path)


def _densify_if_sparse(
    raw_clip: Path,
    source: str,
    slide: Slide,
    segment: SlideSegment,
    out_dir: Path,
    quality: str,
) -> Path:
    """Ask for one denser rewrite of a scene that leaves most of the screen empty.

    This is polish, so it never fails the run: if the rewrite errors out or
    renders no fuller than the original, the original clip is kept.
    """
    coverage = clip_coverage(raw_clip)
    if not is_sparse(coverage):
        return raw_clip

    logger.info("Slide %d uses only %.0f%% of the screen; asking for a denser scene.",
                slide.number, coverage * 100)
    original_clip = shutil.copy(raw_clip, out_dir / "media" / f"original_{slide.number:02d}.mp4")
    try:
        denser_source = densify_scene_source(source, slide.number, coverage)
        denser_clip = _render_with_repairs(denser_source, slide.number, segment, out_dir, quality)
        denser_coverage = clip_coverage(denser_clip)
    except (RenderError, LlmError, SceneSourceError) as error:
        logger.warning("Slide %d: denser rewrite failed (%s); keeping original.",
                       slide.number, str(error)[:120])
        write_scene_file(source, slide.number, out_dir / "scenes")
        return Path(original_clip)

    if denser_coverage > coverage:
        return denser_clip
    logger.info("Slide %d: rewrite is no fuller (%.0f%%); keeping original.",
                slide.number, denser_coverage * 100)
    write_scene_file(source, slide.number, out_dir / "scenes")
    return Path(original_clip)


def _render_with_repairs(
    source: str, slide_number: int, segment: SlideSegment, out_dir: Path, quality: str
) -> Path:
    for attempt in range(MAX_RENDER_REPAIRS + 1):
        scene_file = write_scene_file(source, slide_number, out_dir / "scenes")
        try:
            logger.info("Rendering slide %d (attempt %d) ...", slide_number, attempt + 1)
            return render_scene(
                scene_file, scene_class_name(slide_number),
                segment.duration, out_dir / "media", quality,
            )
        except RenderError as error:
            if attempt == MAX_RENDER_REPAIRS:
                raise
            logger.warning("Slide %d failed to render; asking LLM to repair.", slide_number)
            source = repair_scene_source(source, slide_number, str(error))
    raise AssertionError("unreachable")
