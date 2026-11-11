"""Ask an LLM a question through headless Claude Code (`claude -p`).

Isolated here so swapping in the Anthropic API later touches one file.
"""

from __future__ import annotations

import json
import re
import subprocess

DEFAULT_MODEL = "sonnet"
REQUEST_TIMEOUT_SECONDS = 900
MAX_TIMEOUT_ATTEMPTS = 2  # total tries for a call that times out or exits non-zero
ERROR_DETAIL_CHARS = 500

# Skip user hooks, skills and CLAUDE.md so replies stay plain and parseable.
CLAUDE_ARGUMENTS = [
    "claude",
    "-p",
    "--no-session-persistence",
    "--disable-slash-commands",
    "--setting-sources",
    "",
]

_FENCE_PATTERN = re.compile(r"```(?:\w+)?\n(.*?)```", re.DOTALL)


class LlmError(RuntimeError):
    pass


def ask_claude(prompt: str, model: str = DEFAULT_MODEL) -> str:
    """Ask Claude; a timed-out or failed call (usually transient) is retried once."""
    failure = ""
    for attempt in range(MAX_TIMEOUT_ATTEMPTS):
        try:
            # The prompt goes over stdin: slide text plus transcript can exceed argv limits.
            completed = subprocess.run(
                [*CLAUDE_ARGUMENTS, "--model", model],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            failure = f"timed out after {REQUEST_TIMEOUT_SECONDS}s"
            continue
        if completed.returncode == 0:
            return completed.stdout.strip()
        # stderr can hold only a harmless warning while the real cause is in stdout.
        failure = (
            f"exited {completed.returncode}: {completed.stderr.strip()} "
            f"{completed.stdout.strip()[-ERROR_DETAIL_CHARS:]}"
        )
    raise LlmError(f"claude failed {MAX_TIMEOUT_ATTEMPTS} times; last: {failure}")


def extract_code_block(reply: str) -> str:
    """Return the first fenced code block, or the whole reply if unfenced."""
    match = _FENCE_PATTERN.search(reply)
    return (match.group(1) if match else reply).strip()


def extract_json(reply: str) -> dict:
    """Parse the JSON object in a reply, tolerating fences and chatter."""
    candidate = extract_code_block(reply)
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end <= start:
        raise LlmError(f"No JSON object in reply: {reply[:200]!r}")
    try:
        return json.loads(candidate[start : end + 1])
    except json.JSONDecodeError as error:
        raise LlmError(f"Invalid JSON in reply: {error}") from error
