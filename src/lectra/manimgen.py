"""Generate one Manim scene file per slide with an LLM."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Callable

from lectra.align import SlideSegment
from lectra.ingest import Slide
from lectra.llm import ask_claude, extract_code_block


class SceneSourceError(ValueError):
    pass


def scene_class_name(slide_number: int) -> str:
    return f"Slide{slide_number}Scene"


def build_prompt(slide: Slide, segment: SlideSegment) -> str:
    cue_lines = "\n".join(
        f"- t={cue.time - segment.start:.1f}s: {cue.phrase}"
        + (f"  => SHOW: {cue.visual}" if cue.visual else "")
        for cue in segment.cues
    ) or "- (no cues; pace the reveal evenly)"
    return f"""Write a Manim Community (v0.22) scene in the style of 3Blue1Brown that visually
explains one lecture slide while a narrator speaks. Output ONE python code block and nothing else.

SLIDE {slide.number} TEXT:
{slide.text}

The scene lasts {segment.duration:.1f} seconds. Narration beats (seconds from scene start).
The narrator introduces a new idea at each one:
{cue_lines}

Requirements:
- `from manim import *` and `from lectra.timed_scene import TimedScene`.
- Define `class {scene_class_name(slide.number)}(TimedScene)` implementing `def build(self):`.
- Call `self.wait_until(t)` right before the animation for each beat so it starts exactly when
  the narrator says the idea. Do something visible at EVERY beat: the screen must change each
  time the narration moves to a new idea. Never wait past {segment.duration:.1f}s; the base
  class pads the rest.
- 3Blue1Brown style:
  * Build ONE evolving diagram instead of replacing the screen. Keep earlier objects and
    extend them; use Transform/ReplacementTransform to morph one idea into the next.
  * Color means something: pick one color per concept and reuse it everywhere (in labels,
    equations and shapes) so the viewer links them. Use `set_color_by_tex` for equations.
  * Guide attention: Indicate, Circumscribe, Flash, or color-pulse the exact object being
    discussed, and fade or dim (set_opacity 0.3) objects that are done.
  * Prefer concrete worked examples (real numbers for technical slides, a named scenario or
    short quote card for humanities slides), animated step by step, over abstract boxes.
    Show flow with arrows or moving dots; show arguments as premises that stack into a
    conclusion and comparisons as two side-by-side columns that fill in row by row.
  * Chinese terms are fine: use `Text("仁", font="PingFang SC")` and pair each with its
    pinyin and English gloss.
  * Short labels only, never bullet walls or pasted slide text. Start with a brief title that
    moves to the top corner.
- Keep everything inside the frame (14 wide, 8 tall units) and never overlap objects: lay out
  with `arrange`, `next_to`, and `to_edge`, and use `.scale_to_fit_width` when unsure.
- The narration is the source of truth. Do not invent numbers, dollar amounts, names, or
  example data that the narration beats and slide text never mention; if an illustration
  needs example values, label them generically (for example "value A", "item 1").
- Avoid known Manim crashes: group shapes with `VGroup`, never plain `Group`; do not use
  `save_state()`/`Restore` (set explicit opacity or position back instead); only
  `Transform`/`ReplacementTransform` between objects of the same kind (VMobject to VMobject).
- No external files, images, or fonts. Dark background is the default; keep contrast high.
- Each beat's animation should take 0.5-1.5s so the next beat is hit on time."""


def validate_source(source: str, slide_number: int) -> None:
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        raise SceneSourceError(f"Syntax error: {error}") from error
    class_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    if scene_class_name(slide_number) not in class_names:
        raise SceneSourceError(f"Missing class {scene_class_name(slide_number)}")


def generate_scene_source(
    slide: Slide,
    segment: SlideSegment,
    ask: Callable[[str], str] = ask_claude,
) -> str:
    source = extract_code_block(ask(build_prompt(slide, segment)))
    validate_source(source, slide.number)
    return source


def repair_scene_source(
    source: str,
    slide_number: int,
    error_output: str,
    ask: Callable[[str], str] = ask_claude,
) -> str:
    prompt = f"""This Manim scene failed to render. Fix it and reply with ONE python code block
containing the complete corrected file. Keep the class name {scene_class_name(slide_number)}.

ERROR OUTPUT (tail):
{error_output[-2000:]}

SOURCE:
```python
{source}
```"""
    repaired = extract_code_block(ask(prompt))
    validate_source(repaired, slide_number)
    return repaired


def build_densify_prompt(source: str, slide_number: int, coverage: float) -> str:
    return f"""This Manim scene renders correctly, but its fullest frame uses only
{coverage:.0%} of the screen (a title and a few objects, with large empty regions).
Rewrite it so the diagram fills the 14x8 canvas: scale objects up, spread them out, and
add the missing content (a worked example, a second column, the flow between objects, or
a running summary) in the empty regions as the narration beats arrive. Keep the same beat
timings, the same class name {scene_class_name(slide_number)}, and every rule from the
original brief. Reply with ONE python code block containing the complete file.

SOURCE:
```python
{source}
```"""


def densify_scene_source(
    source: str,
    slide_number: int,
    coverage: float,
    ask: Callable[[str], str] = ask_claude,
) -> str:
    denser = extract_code_block(ask(build_densify_prompt(source, slide_number, coverage)))
    validate_source(denser, slide_number)
    return denser


def write_scene_file(source: str, slide_number: int, scenes_dir: Path) -> Path:
    scenes_dir.mkdir(parents=True, exist_ok=True)
    path = scenes_dir / f"slide_{slide_number:02d}.py"
    path.write_text(source)
    return path
