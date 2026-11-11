"""List and create Gemini Notebook (NotebookLM) notebooks by driving Brave.

Uses the same signed-in Lectra Brave profile as podcast.py. All home-page UI
knowledge (the notebook list and the "New notebook" button) lives in this file.

    python -m lectra.notebooks list [--json]
    python -m lectra.notebooks create [--title TITLE] [--set-default]
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from lectra.podcast import NOTEBOOK_HOST, UI_TIMEOUT_MS, brave_session, open_signed_in_page

logger = logging.getLogger("lectra.notebooks")

HOME_URL = f"https://{NOTEBOOK_HOST}/"
DEFAULT_NOTEBOOK_FILE = Path.home() / ".lectra" / "notebook_url"
UNTITLED_NOTEBOOK = "Untitled notebook"
NOTEBOOK_URL_PATTERN = re.compile(r"/notebook/[0-9a-f-]{36}")
# Card subtitle, e.g. "Nov 5, 2026·31 sources" or "Jun 9, 2026·1 source".
SUBTITLE_PATTERN = re.compile(r"^(?P<created>.+?)\s*·\s*(?P<count>\d+)\s+sources?$")

# Each card links to /notebook/<id> and labels itself with elements whose ids
# are project-<id>-title and project-<id>-subtitle.
READ_CARDS_SCRIPT = """() =>
  [...document.querySelectorAll('mat-card a[href*="/notebook/"]')].map(link => {
    const id = link.getAttribute('href').split('/').pop();
    const text = (suffix) => {
      const element = document.getElementById(`project-${id}-${suffix}`);
      return element ? element.innerText.trim() : '';
    };
    return {id, title: text('title'), subtitle: text('subtitle')};
  })"""


@dataclass(frozen=True)
class Notebook:
    title: str
    url: str
    created: str
    source_count: int | None


def parse_card(card: dict[str, str]) -> Notebook:
    """Turn one scraped card ({id, title, subtitle}) into a Notebook."""
    match = SUBTITLE_PATTERN.match(card["subtitle"])
    return Notebook(
        title=card["title"],
        url=f"{HOME_URL}notebook/{card['id']}",
        created=match.group("created") if match else card["subtitle"],
        source_count=int(match.group("count")) if match else None,
    )


# --- Locators ----------------------------------------------------------------


def new_notebook_button(page: Page):
    return page.get_by_role("button", name="New notebook")


def see_all_notebooks_button(page: Page):
    return page.get_by_role("button", name="See more recent notebooks")


def notebook_title_input(page: Page):
    # The title has no accessible name; its CSS class is the only stable hook.
    return page.locator("input.title-input")


# --- Steps -------------------------------------------------------------------


def read_notebooks(page: Page) -> list[Notebook]:
    """Read every notebook you own from the home page, newest first.

    The home page shows only a few recent notebooks; "See more" opens the full
    list, which also leaves out the featured (public) notebooks.
    """
    new_notebook_button(page).wait_for(timeout=UI_TIMEOUT_MS)
    if see_all_notebooks_button(page).count():
        see_all_notebooks_button(page).click()
        page.get_by_role("button", name="Back to homepage").wait_for(timeout=UI_TIMEOUT_MS)
    return [parse_card(card) for card in page.evaluate(READ_CARDS_SCRIPT)]


def make_notebook(page: Page, title: str | None) -> str:
    """Click "New notebook", optionally rename it, and return its URL.

    Without a title it stays "Untitled notebook" until sources are added.
    """
    new_notebook_button(page).click()
    page.wait_for_url(NOTEBOOK_URL_PATTERN, timeout=UI_TIMEOUT_MS)
    title_input = notebook_title_input(page)
    title_input.wait_for(timeout=UI_TIMEOUT_MS)

    if title:
        title_input.fill(title)
        title_input.press("Enter")
        if title_input.input_value() != title:
            raise RuntimeError(f"Created {page.url} but could not rename it to {title!r}")
    return page.url.split("?")[0]


# --- Orchestration -----------------------------------------------------------


def list_notebooks(headless: bool = True) -> list[Notebook]:
    with brave_session(headless) as port, sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        page = open_signed_in_page(browser.contexts[0], HOME_URL, headless)
        return read_notebooks(page)


def create_notebook(title: str | None = None, headless: bool = True) -> str:
    """Create an empty notebook and return its URL."""
    with brave_session(headless) as port, sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        page = open_signed_in_page(browser.contexts[0], HOME_URL, headless)
        new_notebook_button(page).wait_for(timeout=UI_TIMEOUT_MS)
        url = make_notebook(page, title)
    logger.info("Created notebook %r: %s", title or UNTITLED_NOTEBOOK, url)
    return url


def save_default_notebook(url: str) -> None:
    DEFAULT_NOTEBOOK_FILE.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_NOTEBOOK_FILE.write_text(url + "\n")
    logger.info("Default notebook is now %s (%s)", url, DEFAULT_NOTEBOOK_FILE)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="lectra.notebooks", description=__doc__.splitlines()[0])
    parser.add_argument("--headed", action="store_true",
                        help="Show Brave (or reuse an open Lectra Brave window)")
    commands = parser.add_subparsers(dest="command", required=True)

    list_command = commands.add_parser("list", help="List the notebooks you own")
    list_command.add_argument("--json", action="store_true", help="Print JSON instead of a table")

    create_command = commands.add_parser("create", help="Create a new empty notebook")
    create_command.add_argument("--title", help="Notebook title (default: Untitled notebook)")
    create_command.add_argument("--set-default", action="store_true",
                                help=f"Also make it the default notebook ({DEFAULT_NOTEBOOK_FILE})")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    arguments = parse_arguments()
    headless = not arguments.headed

    if arguments.command == "list":
        notebooks = list_notebooks(headless)
        if arguments.json:
            print(json.dumps([asdict(notebook) for notebook in notebooks], indent=2))
            return
        for notebook in notebooks:
            print(f"{notebook.title}\t{notebook.source_count} sources\t{notebook.created}\t{notebook.url}")
        return

    url = create_notebook(arguments.title, headless)
    print(url)
    if arguments.set_default:
        save_default_notebook(url)


if __name__ == "__main__":
    main()
