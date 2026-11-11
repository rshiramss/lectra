"""Base class for generated slide scenes.

Generated scenes implement `build()` and call `wait_until(t)` to land
animations on narration cues. `t` is seconds from the scene's start. After
`build()` the scene pads with a still frame up to the slide's duration, so the
rendered clip always spans the segment of audio it illustrates.
"""

from __future__ import annotations

import os

from manim import Scene

from lectra.scene_env import DURATION_ENV_VAR

MIN_WAIT_SECONDS = 0.05


class TimedScene(Scene):
    duration: float

    def construct(self) -> None:
        self.duration = float(os.environ[DURATION_ENV_VAR])
        self.build()
        self.wait_until(self.duration)

    def build(self) -> None:
        raise NotImplementedError("Generated scenes must implement build()")

    def wait_until(self, seconds: float) -> None:
        remaining = seconds - self.renderer.time
        if remaining > MIN_WAIT_SECONDS:
            self.wait(remaining)
