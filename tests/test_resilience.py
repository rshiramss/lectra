import subprocess
from pathlib import Path

import pytest

from lectra import llm
from lectra.render import error_report


def completed(stdout="ok", returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def test_ask_claude_retries_once_after_a_timeout(monkeypatch):
    outcomes = iter([subprocess.TimeoutExpired("claude", 1), completed("answer")])

    def fake_run(*_args, **_kwargs):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(llm.subprocess, "run", fake_run)

    assert llm.ask_claude("prompt") == "answer"


def test_ask_claude_raises_llm_error_when_every_attempt_times_out(monkeypatch):
    def always_times_out(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("claude", 1)

    monkeypatch.setattr(llm.subprocess, "run", always_times_out)

    with pytest.raises(llm.LlmError):
        llm.ask_claude("prompt")


def test_error_report_keeps_scene_file_lines_that_fall_outside_the_tail():
    scene_file = Path("scenes/slide_12.py")
    user_frame = "│ /x/scenes/slide_12.py:161 in build │\n│ ❱ 161 Restore(rest_grp) │"
    noise = "│ manim internals line │\n" * 500
    output = user_frame + "\n" + noise + "NotImplementedError: Please override"

    report = error_report(output, scene_file, tail_chars=300)

    assert "slide_12.py:161" in report and "Restore(rest_grp)" in report
    assert report.endswith("NotImplementedError: Please override")


def test_ask_claude_retries_once_after_a_failed_exit(monkeypatch):
    outcomes = iter([completed("transient failure", returncode=1), completed("answer")])
    monkeypatch.setattr(llm.subprocess, "run", lambda *_a, **_k: next(outcomes))

    assert llm.ask_claude("prompt") == "answer"


def test_ask_claude_error_includes_stdout_when_stderr_is_only_a_warning(monkeypatch):
    failing = subprocess.CompletedProcess([], 1, stdout="rate limit reached", stderr="Warning: x")
    monkeypatch.setattr(llm.subprocess, "run", lambda *_a, **_k: failing)

    with pytest.raises(llm.LlmError, match="rate limit reached"):
        llm.ask_claude("prompt")


def test_render_scene_turns_a_hung_manim_into_a_render_error(monkeypatch, tmp_path):
    import subprocess
    from lectra import render

    def hang(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="manim", timeout=1)

    monkeypatch.setattr(render, "_run_manim", hang)

    try:
        render.render_scene(tmp_path / "s.py", "SliceScene", 5.0, tmp_path, "low")
    except render.RenderError as error:
        assert "timed out" in str(error)
    else:
        raise AssertionError("expected RenderError")
