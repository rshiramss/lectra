"""Generate a NotebookLM audio overview from a slide deck by driving Brave.

Flow: start/attach to Brave over CDP -> open notebook -> upload deck ->
select only that source -> Audio Overview (Deep Dive, chosen length, focus prompt)
-> wait for generation -> download the audio file.

Why a dedicated Brave profile: Chromium refuses a remote-debugging port on the
default profile, and Google blocks sign-in inside automation-launched
browsers. So we start Brave as a normal process (plus the debug port) and you
sign in by hand once (`--headed`). The session persists in PROFILE_DIR, so
later runs default to a private headless Brave on that profile, which is
started for the run and quit afterwards.

Selectors come from the live UI (Gemini Notebook, Nov 2026). Notebook-page UI
knowledge lives in this file; the home page (listing and creating notebooks)
lives in notebooks.py. A UI change means editing one of those two only.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import time
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import BrowserContext, Page, expect, sync_playwright

logger = logging.getLogger("lectra.podcast")

BRAVE_BINARY ="/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"
PROFILE_DIR = Path.home() / ".lectra" / "brave-profile"
HEADED_DEBUG_PORT = 9222
HEADLESS_DEBUG_PORT = 9223

BRAVE_STARTUP_TIMEOUT_SECONDS = 20
LOGIN_TIMEOUT_MS = 5 * 60 * 1000
UI_TIMEOUT_MS = 30 * 1000
UPLOAD_TIMEOUT_MS = 3 * 60 * 1000
AUDIO_HOST = "googleusercontent.com"
DEFAULT_AUDIO_FILENAME = "audio_overview.m4a"
GENERATION_TIMEOUT_MS = 45 * 60 * 1000
POLL_INTERVAL_SECONDS = 10

SIGN_IN_HOST = "accounts.google.com"
NOTEBOOK_HOST = "notebook.google.com"
NOTEBOOK_URL_ENV_VAR ="LECTRA_NOTEBOOK_URL"
DEFAULT_OUTPUT_DIR = Path("audio")
AUDIO_LENGTHS = ("Short", "Default", "Long")
DEFAULT_AUDIO_LENGTH = "Long"

TECHNICAL_FOCUS_PROMPT = (
    "Make this a deeply technical, university-level walkthrough of the deck. "
    "Cover every slide in order, and say which slide you are on "
    "(for example 'on the next slide'). Explain mechanisms, derivations, and "
    "definitions precisely instead of giving analogies. Keep hosts on topic; "
    "no tangents that are not in the slides."
)


# --- Brave / Playwright connection -------------------------------------------


def is_debug_port_open(port: int) -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1)
        return True
    except OSError:
        return False


def wait_for_debug_port(port: int, process: subprocess.Popen | None = None) -> None:
    deadline = time.monotonic() + BRAVE_STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if is_debug_port_open(port):
            return
        if process is not None and process.poll() is not None:
            break
        time.sleep(0.5)
    raise RuntimeError(
        f"Brave did not open debug port {port}. If a Lectra Brave window is already "
        f"open, close it first: one profile ({PROFILE_DIR}) can't run in two browsers."
    )


def launch_brave(port: int, extra_flags: list[str]) -> subprocess.Popen:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        [
            BRAVE_BINARY,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={PROFILE_DIR}",
            "--no-first-run",
            *extra_flags,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@contextmanager
def brave_session(headless: bool) -> Iterator[int]:
    """Yield the debug port of a Brave running the signed-in Lectra profile.

    Headless: start a private headless Brave on its own port and quit it on exit.
    Headed: reuse an already-open Lectra Brave window if there is one, and leave
    it running afterwards (it is where you sign in).
    """
    if not headless:
        if not is_debug_port_open(HEADED_DEBUG_PORT):
            wait_for_debug_port(HEADED_DEBUG_PORT, launch_brave(HEADED_DEBUG_PORT, []))
        yield HEADED_DEBUG_PORT
        return

    process = launch_brave(HEADLESS_DEBUG_PORT, ["--headless=new", "--window-size=1440,1000"])
    try:
        wait_for_debug_port(HEADLESS_DEBUG_PORT, process)
        yield HEADLESS_DEBUG_PORT
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def hide_headless_user_agent(page: Page) -> None:
    """Report a normal Chrome user agent; Google treats "HeadlessChrome" as a bot."""
    user_agent = page.evaluate("navigator.userAgent").replace("HeadlessChrome", "Chrome")
    cdp = page.context.new_cdp_session(page)
    cdp.send("Network.setUserAgentOverride", {"userAgent": user_agent})


def open_signed_in_page(context: BrowserContext, url: str, headless: bool) -> Page:
    """Open a Gemini Notebook URL, pausing for a manual Google sign-in if needed (headed only)."""
    page = context.pages[0] if context.pages else context.new_page()
    if headless:
        hide_headless_user_agent(page)
    page.goto(url)

    if urlparse(page.url).hostname == SIGN_IN_HOST:
        if headless:
            raise RuntimeError(
                "Google sign-in required. Run once with --headed, sign in in the Brave "
                "window, then headless runs reuse that session."
            )
        logger.warning("Sign in to Google in the Brave window that just opened (one-time).")
        # Match the hostname, not a substring: the sign-in URL carries the
        # notebook URL in its ?continue= parameter.
        page.wait_for_url(
            lambda url: urlparse(url).hostname == NOTEBOOK_HOST, timeout=LOGIN_TIMEOUT_MS
        )
    return page


def open_notebook(context: BrowserContext, notebook_url: str, headless: bool) -> Page:
    """Open the notebook and wait until its source panel is usable."""
    page = open_signed_in_page(context, notebook_url, headless)
    add_sources_button(page).wait_for(timeout=UI_TIMEOUT_MS)
    return page


# --- Locators (the only place UI wording is encoded) -------------------------


def add_sources_button(page: Page):
    return page.get_by_role("button", name=re.compile(r"^Add sources?$")).first


def select_all_checkbox(page: Page):
    return page.get_by_label("Select all sources")


def source_checkbox(page: Page, source_name: str):
    return page.get_by_label(re.compile(rf"^Select {re.escape(source_name[:40])}")).first


def studio_play_buttons(page: Page):
    # exact=True: a substring match also hits chat suggestion chips that contain "play".
    return page.get_by_role("button", name="Play", exact=True)


# --- Steps -------------------------------------------------------------------


def upload_sources(page: Page, source_paths: list[Path]) -> None:
    # A retry after a later failure must not upload duplicate sources.
    missing = [path for path in source_paths if source_checkbox(page, path.name).count() == 0]
    if not missing:
        logger.info("All %d sources are already uploaded; reusing them.", len(source_paths))
        return

    add_sources_button(page).click()
    with page.expect_file_chooser(timeout=UI_TIMEOUT_MS) as chooser_info:
        page.get_by_text("Upload files").click()
    chooser_info.value.set_files([str(path) for path in missing])
    for path in missing:
        source_checkbox(page, path.name).wait_for(timeout=UPLOAD_TIMEOUT_MS)


def select_only_sources(page: Page, source_names: list[str]) -> None:
    """Check exactly the given sources and uncheck everything else.

    Reads each checkbox's real state instead of toggling "Select all": a freshly
    uploaded source can arrive pre-checked, which makes toggling unreliable.
    """
    wanted_labels = [f"Select {name}" for name in source_names]
    checkboxes = page.get_by_label(re.compile(r"^Select (?!all sources)"))

    for index in range(checkboxes.count()):
        checkbox = checkboxes.nth(index)
        # Long names are truncated with "…" in the label, so match by prefix.
        label = (checkbox.get_attribute("aria-label") or "").rstrip("… ")
        should_be_checked = any(wanted.startswith(label) for wanted in wanted_labels)
        if checkbox.is_checked() != should_be_checked:
            checkbox.click()

    for name in source_names:
        expect(source_checkbox(page, name)).to_be_checked(timeout=UI_TIMEOUT_MS)
    checked_count = sum(checkboxes.nth(i).is_checked() for i in range(checkboxes.count()))
    if checked_count != len(source_names):
        raise RuntimeError(
            f"Expected {len(source_names)} selected sources, found {checked_count}"
        )


def wait_for_idle_studio(page: Page) -> None:
    """Wait out a generation already running (e.g. from an aborted earlier run).

    Its disabled placeholder button also matches "Audio Overview", and its
    finished audio would be mistaken for the one we are about to request.
    """
    in_progress = page.locator('button[disabled][aria-description="Audio Overview"]')
    deadline = time.monotonic() + GENERATION_TIMEOUT_MS / 1000
    while in_progress.count() > 0:
        if time.monotonic() > deadline:
            raise TimeoutError("An earlier audio generation never finished")
        logger.info("An earlier audio generation is still running; waiting ...")
        time.sleep(POLL_INTERVAL_SECONDS)


def request_audio_overview(page: Page, focus_prompt: str, length: str) -> None:
    page.get_by_role("button", name="Audio Overview").click()

    dialog_heading = page.get_by_role("heading", name="Customize Audio Overview")
    dialog_heading.wait_for(timeout=UI_TIMEOUT_MS)

    page.get_by_text("Deep Dive", exact=True).click()
    page.get_by_role("radiogroup").get_by_text(length, exact=True).click()
    page.get_by_role("textbox", name=re.compile("What should the AI hosts focus")).fill(
        focus_prompt
    )
    page.get_by_role("button", name="Generate now").click()


def wait_for_new_audio(page: Page, play_count_before: int) -> None:
    """Block until a new playable item appears in the Studio list.

    Items still generating have no Play button, so the count only rises once
    the new overview is ready.
    """
    deadline = time.monotonic() + GENERATION_TIMEOUT_MS / 1000
    while time.monotonic() < deadline:
        if studio_play_buttons(page).count() > play_count_before:
            return
        time.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError("Audio overview did not finish generating in time")


def download_newest_audio(page: Page, output_dir: Path) -> Path:
    """Download the newest Studio item (the list is sorted newest first).

    The row is the largest ancestor holding exactly one Play button. Its menu
    button is the row's last button: on a just-finished item the "More" button
    shows a "new" dot instead, so it can't be found by its "More" label.
    """
    newest_item = studio_play_buttons(page).first.locator(
        "xpath=ancestor::*[count(.//button[@aria-label='Play'])=1][last()]"
    )
    logger.info("Downloading Studio item: %s", newest_item.inner_text().split("\n")[0])
    newest_item.locator("button").last.click()

    # NotebookLM's "Download" now opens a blank tab that requests the audio
    # from googleusercontent instead of firing a download event on this page,
    # so catch that request and fetch it with the signed-in session's cookies.
    with page.context.expect_event(
        "request",
        predicate=lambda request: AUDIO_HOST in request.url,
        timeout=UPLOAD_TIMEOUT_MS,
    ) as request_info:
        page.get_by_role("menuitem", name="Download").click()

    response = page.context.request.get(request_info.value.url, timeout=UPLOAD_TIMEOUT_MS)
    if not response.ok:
        raise RuntimeError(f"Audio download failed with HTTP {response.status}")
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / audio_filename(response.headers.get("content-disposition", ""))
    destination.write_bytes(response.body())
    return destination


def audio_filename(content_disposition: str) -> str:
    """Return the filename from a Content-Disposition header, or a default."""
    match = re.search(r'filename="([^"]+)"', content_disposition)
    return Path(match.group(1)).name if match else DEFAULT_AUDIO_FILENAME


# --- Orchestration -----------------------------------------------------------


def generate_audio_overview(
    source_paths: list[Path],
    notebook_url: str,
    output_dir: Path,
    focus_prompt: str = TECHNICAL_FOCUS_PROMPT,
    length: str = DEFAULT_AUDIO_LENGTH,
    should_generate: bool = True,
    should_download_only: bool = False,
    headless: bool = True,
) -> Path | None:
    """Upload the sources and return the downloaded audio path.

    With should_generate=False, stops after uploading and selecting the source
    so you can check the UI flow without spending generation quota.
    With should_download_only=True, skips straight to downloading the newest
    existing overview (to recover from a failed download without regenerating).
    """
    with brave_session(headless) as port, sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        page = open_notebook(browser.contexts[0], notebook_url, headless)

        if should_download_only:
            return download_newest_audio(page, output_dir)

        upload_sources(page, source_paths)
        select_only_sources(page, [path.name for path in source_paths])
        if not should_generate:
            return None

        wait_for_idle_studio(page)  # before counting, or a stale finish looks like ours
        play_count_before = studio_play_buttons(page).count()
        request_audio_overview(page, focus_prompt, length)
        wait_for_new_audio(page, play_count_before)
        return download_newest_audio(page, output_dir)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "sources", type=Path, nargs="+", help="Files to use as sources (a deck, or several)"
    )
    parser.add_argument(
        "--focus-file", type=Path, help="Text file with the AI hosts' focus prompt"
    )
    parser.add_argument(
        "--notebook-url",
        default=os.environ.get(NOTEBOOK_URL_ENV_VAR),
        help=f"Notebook to use (or set {NOTEBOOK_URL_ENV_VAR})",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--length", choices=AUDIO_LENGTHS, default=DEFAULT_AUDIO_LENGTH,
        help="Audio Overview length setting in NotebookLM",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show the Brave window (needed once, to sign in to Google); default is headless",
    )
    parser.add_argument(
        "--no-generate",
        action="store_true",
        help="Only upload and select the source; skip audio generation",
    )
    parser.add_argument(
        "--download-only",
        action="store_true",
        help="Skip upload/generation; download the newest existing overview",
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    arguments = parse_arguments()
    if not arguments.notebook_url:
        raise SystemExit(f"Pass --notebook-url or set {NOTEBOOK_URL_ENV_VAR}")
    missing = [path for path in arguments.sources if not path.is_file()]
    if missing:
        raise SystemExit(f"Source not found: {missing[0]}")
    focus_prompt = (
        arguments.focus_file.read_text().strip() if arguments.focus_file else TECHNICAL_FOCUS_PROMPT
    )

    audio_path = generate_audio_overview(
        source_paths=[path.resolve() for path in arguments.sources],
        notebook_url=arguments.notebook_url,
        output_dir=arguments.output_dir,
        focus_prompt=focus_prompt,
        length=arguments.length,
        should_generate=not arguments.no_generate,
        should_download_only=arguments.download_only,
        headless=not arguments.headed,
    )
    logger.info("Saved audio: %s", audio_path or "(skipped: --no-generate)")


if __name__ == "__main__":
    main()
