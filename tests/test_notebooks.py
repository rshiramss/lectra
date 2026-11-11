from lectra import lecture, notebooks
from lectra.notebooks import Notebook, parse_card

NOTEBOOK_ID = "00000000-1111-2222-3333-444444444444"


def test_parse_card_reads_date_and_source_count():
    card = {"id": NOTEBOOK_ID, "title": "Anatomy of a Binary Counter",
            "subtitle": "Nov 5, 2026·31 sources"}

    notebook = parse_card(card)

    assert notebook == Notebook(
        title="Anatomy of a Binary Counter",
        url=f"https://notebook.google.com/notebook/{NOTEBOOK_ID}",
        created="Nov 5, 2026",
        source_count=31,
    )


def test_parse_card_handles_a_single_source():
    card = {"id": NOTEBOOK_ID, "title": "Pipeline Auditor", "subtitle": "Jun 10, 2026·1 source"}

    assert parse_card(card).source_count == 1


def test_parse_card_keeps_an_unrecognised_subtitle_without_a_count():
    card = {"id": NOTEBOOK_ID, "title": "Odd", "subtitle": "Shared with you"}

    notebook = parse_card(card)

    assert (notebook.created, notebook.source_count) == ("Shared with you", None)


def test_find_notebook_url_prefers_flag_then_env_then_default_file(tmp_path, monkeypatch):
    default_file = tmp_path / "notebook_url"
    default_file.write_text("https://default\n")
    monkeypatch.setattr(lecture, "DEFAULT_NOTEBOOK_FILE", default_file)
    monkeypatch.setenv("LECTRA_NOTEBOOK_URL", "https://env")

    assert lecture.find_notebook_url("https://flag") == "https://flag"
    assert lecture.find_notebook_url(None) == "https://env"
    monkeypatch.delenv("LECTRA_NOTEBOOK_URL")
    assert lecture.find_notebook_url(None) == "https://default"


def test_run_notebook_is_created_once_and_reused_on_resume(tmp_path, monkeypatch):
    created_titles = []

    def fake_create_notebook(title, headless):
        created_titles.append(title)
        return "https://notebook.google.com/notebook/new"

    monkeypatch.setattr(lecture, "create_notebook", fake_create_notebook)

    first = lecture.get_run_notebook(tmp_path / "build", "CS 101 Lecture 3", headless=True)
    second = lecture.get_run_notebook(tmp_path / "build", "CS 101 Lecture 3", headless=True)

    assert first == second == "https://notebook.google.com/notebook/new"
    assert created_titles == ["CS 101 Lecture 3"]


def test_save_default_notebook_writes_the_config_file(tmp_path, monkeypatch):
    default_file = tmp_path / "lectra" / "notebook_url"
    monkeypatch.setattr(notebooks, "DEFAULT_NOTEBOOK_FILE", default_file)

    notebooks.save_default_notebook("https://notebook.google.com/notebook/new")

    assert default_file.read_text().strip() == "https://notebook.google.com/notebook/new"
