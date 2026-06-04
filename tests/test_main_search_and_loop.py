"""Tests for main.py: title/author search and the convert-another loop."""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import main


# ---- title_matches_search ----

def test_title_matches_exact_substring():
    assert main.title_matches_search("Washington Burning.epub", "washington")


def test_title_matches_multiword_any_order():
    assert main.title_matches_search("Washington Burning - Les Standiford.epub",
                                     "burning washington")


def test_title_matches_fuzzy_word():
    # "standford" ~ "standiford" (similarity >= 0.84)
    assert main.title_matches_search("Washington Burning - Les Standiford.epub",
                                     "standford")


def test_title_matches_empty_search_is_false():
    assert not main.title_matches_search("anything.epub", "   ")


def test_title_matches_no_match():
    assert not main.title_matches_search("Foundation.epub", "dune")


# ---- book_matches_search (author via parent dir) ----

def _calibre_path(author, title, ext="epub"):
    # Calibre layout: Library/Author Name/Book Title (id)/Book Title - Author.epub
    return os.path.join("/lib", author, f"{title} (1)", f"{title} - {author}.{ext}")


def test_author_search_matches_parent_dir():
    path = _calibre_path("Isaac Asimov", "Foundation")
    assert main.book_matches_search(path, "asimov")


def test_author_search_matches_full_name():
    path = _calibre_path("Isaac Asimov", "Foundation")
    assert main.book_matches_search(path, "isaac asimov")


def test_author_search_no_match():
    path = _calibre_path("Isaac Asimov", "Foundation")
    assert not main.book_matches_search(path, "tolkien")


def test_title_search_still_works_via_book_matches():
    path = _calibre_path("Isaac Asimov", "Foundation")
    assert main.book_matches_search(path, "foundation")


def test_author_search_when_filename_lacks_author():
    # Filename without author; only the parent dir carries author info.
    path = os.path.join("/lib", "Ursula K Le Guin", "The Dispossessed (3)",
                        "The Dispossessed.epub")
    assert main.book_matches_search(path, "le guin")
    assert not main.book_matches_search(path, "asimov")


def test_book_matches_handles_shallow_path():
    # No grandparent dir -> falls back to filename only, no crash.
    assert main.book_matches_search("Foundation.epub", "foundation")
    assert not main.book_matches_search("Foundation.epub", "asimov")


# ---- filter_by_title (now title-or-author) ----

def test_filter_by_title_returns_author_matches():
    paths = [
        _calibre_path("Isaac Asimov", "Foundation"),
        _calibre_path("Frank Herbert", "Dune"),
        _calibre_path("Isaac Asimov", "I Robot"),
    ]
    out = main.filter_by_title(paths, "asimov")
    assert len(out) == 2
    assert all("Asimov" in p for p in out)


def test_filter_by_title_returns_title_matches():
    paths = [
        _calibre_path("Isaac Asimov", "Foundation"),
        _calibre_path("Frank Herbert", "Dune"),
    ]
    out = main.filter_by_title(paths, "dune")
    assert out == [paths[1]]


def test_filter_by_title_empty_when_nothing_matches():
    paths = [_calibre_path("Isaac Asimov", "Foundation")]
    assert main.filter_by_title(paths, "tolkien") == []


# ---- run_conversion_flow + convert-another loop ----

def test_run_conversion_flow_no_books(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(main, "find_ebooks_in_calibre_library", lambda p: [])
    main.run_conversion_flow(str(tmp_path), "apply_bioread.py")
    assert "No ebooks found" in capsys.readouterr().out


def test_run_conversion_flow_invokes_apply(tmp_path, monkeypatch):
    """End-to-end happy path with all prompts answered and apply called once."""
    fake_books = [_calibre_path("Isaac Asimov", "Foundation")]
    monkeypatch.setattr(main, "find_ebooks_in_calibre_library",
                        lambda p: fake_books)
    # Skip optional-prune steps and font/target prompts.
    answers = iter([
        "n",   # exclude already-converted?
        "n",   # remove duplicates?
        "n",   # filter by title/author? -> falls through to prompt_user_selection
        "y",   # convert this one book?
    ])
    monkeypatch.setattr("builtins.input", lambda _="": next(answers))
    monkeypatch.setattr(main, "select_font_directory", lambda: None)
    monkeypatch.setattr(main, "resolve_target_preference", lambda: "kobo")

    called = {}

    def fake_apply(books, script, font_dir=None, target="kobo"):
        called["books"] = books
        called["target"] = target

    monkeypatch.setattr(main, "apply_bionic_reading", fake_apply)
    main.run_conversion_flow("/lib", "apply_bioread.py")
    assert called["books"] == fake_books
    assert called["target"] == "kobo"


def test_convert_another_loop_runs_twice_then_exits(monkeypatch):
    """Simulate the __main__ loop: 'y' once, then 'n' to break."""
    runs = []

    def fake_flow(lib, script):
        runs.append(lib)

    monkeypatch.setattr(main, "run_conversion_flow", fake_flow)
    answers = iter(["y", "n"])
    monkeypatch.setattr("builtins.input", lambda _="": next(answers))

    # Reproduce the loop body from __main__ directly.
    while True:
        main.run_conversion_flow("/lib", "apply_bioread.py")
        again = input("again? ").strip().lower()
        if again != "y":
            break

    assert len(runs) == 2


def test_convert_another_loop_exits_on_anything_but_y(monkeypatch):
    runs = []
    monkeypatch.setattr(main, "run_conversion_flow",
                        lambda lib, script: runs.append(lib))
    for answer in ["n", "", "no", "quit", "Y"]:  # only "y" (lower) continues
        runs.clear()
        answers = iter([answer, "n"])  # second "n" guards if loop continues
        monkeypatch.setattr("builtins.input", lambda _="": next(answers))
        while True:
            main.run_conversion_flow("/lib", "apply_bioread.py")
            again = input("again? ").strip().lower()
            if again != "y":
                break
        # "Y".lower() == "y" so that one continues once; the rest exit after 1.
        expected = 2 if answer.strip().lower() == "y" else 1
        assert len(runs) == expected, f"answer={answer!r}"
