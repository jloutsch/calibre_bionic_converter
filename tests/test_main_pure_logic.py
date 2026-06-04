"""Pure-logic tests for main.py helpers (no I/O prompts, no subprocess)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import main


# ---- parse_number_selection ----

def test_parse_single_number():
    assert main.parse_number_selection("3", 5) == [2]


def test_parse_comma_list_preserves_order():
    assert main.parse_number_selection("1,3,5", 5) == [0, 2, 4]


def test_parse_dedupes_repeats():
    assert main.parse_number_selection("2,2,3", 5) == [1, 2]


def test_parse_whitespace_around_numbers_ok():
    assert main.parse_number_selection(" 1 , 2 ", 5) == [0, 1]


def test_parse_rejects_zero():
    assert main.parse_number_selection("0", 5) is None


def test_parse_rejects_over_max():
    assert main.parse_number_selection("6", 5) is None


def test_parse_rejects_non_numeric():
    assert main.parse_number_selection("a", 5) is None
    assert main.parse_number_selection("1,b", 5) is None


def test_parse_rejects_negative():
    # "-1" isn't .isdigit() -> None.
    assert main.parse_number_selection("-1", 5) is None


def test_parse_empty_returns_empty_list():
    assert main.parse_number_selection("", 5) == []
    assert main.parse_number_selection(",,", 5) == []


# ---- exclude_converted_books ----

def test_exclude_fastread_suffix():
    paths = ["/lib/Book.epub", "/lib/Book_fastread.epub"]
    assert main.exclude_converted_books(paths) == ["/lib/Book.epub"]


def test_exclude_fast_font_marker_case_insensitive():
    paths = [
        "/lib/Book.epub",
        "/lib/Book - Fast Font.epub",
        "/lib/Other - fast font.epub",
    ]
    assert main.exclude_converted_books(paths) == ["/lib/Book.epub"]


def test_exclude_empty_list():
    assert main.exclude_converted_books([]) == []


def test_exclude_no_matches_returns_all():
    paths = ["/lib/A.epub", "/lib/B.mobi"]
    assert main.exclude_converted_books(paths) == paths


# ---- deduplicate_by_format ----

def test_dedup_prefers_epub_when_epub_comes_second():
    paths = [
        "/lib/Foundation.mobi",
        "/lib/Foundation.epub",
    ]
    assert main.deduplicate_by_format(paths, preferred_format="epub") == [
        "/lib/Foundation.epub"
    ]


def test_dedup_keeps_epub_when_epub_comes_first():
    paths = [
        "/lib/Foundation.epub",
        "/lib/Foundation.mobi",
    ]
    assert main.deduplicate_by_format(paths, preferred_format="epub") == [
        "/lib/Foundation.epub"
    ]


def test_dedup_normalizes_title_punctuation():
    # "I, Robot.epub" and "I Robot.mobi" share normalized title "i robot".
    paths = ["/lib/I, Robot.mobi", "/lib/I Robot.epub"]
    result = main.deduplicate_by_format(paths, preferred_format="epub")
    assert result == ["/lib/I Robot.epub"]


def test_dedup_strips_converted_markers_from_title():
    # Same logical book; one is already a fastread variant.
    paths = ["/lib/Foundation.mobi", "/lib/Foundation_fastread.epub"]
    result = main.deduplicate_by_format(paths, preferred_format="epub")
    # Both normalize to "foundation"; preferred-format epub wins on replace.
    assert result == ["/lib/Foundation_fastread.epub"]


def test_dedup_keeps_distinct_titles():
    paths = ["/lib/Dune.epub", "/lib/Foundation.epub"]
    out = main.deduplicate_by_format(paths)
    assert set(out) == set(paths)


# ---- find_ebooks_in_calibre_library ----

def test_find_ebooks_walks_recursively(tmp_path):
    (tmp_path / "Author A" / "Book One").mkdir(parents=True)
    (tmp_path / "Author A" / "Book One" / "Book One.epub").write_text("x")
    (tmp_path / "Author A" / "Book One" / "cover.jpg").write_text("x")
    (tmp_path / "Author B" / "Book Two").mkdir(parents=True)
    (tmp_path / "Author B" / "Book Two" / "Book Two.mobi").write_text("x")
    (tmp_path / "Author B" / "Book Two" / "Book Two.azw3").write_text("x")
    (tmp_path / "Author B" / "Book Two" / "notes.txt").write_text("x")

    found = main.find_ebooks_in_calibre_library(str(tmp_path))
    names = sorted(os.path.basename(p) for p in found)
    assert names == ["Book One.epub", "Book Two.azw3", "Book Two.mobi"]


def test_find_ebooks_respects_custom_formats(tmp_path):
    (tmp_path / "a.epub").write_text("x")
    (tmp_path / "b.mobi").write_text("x")
    found = main.find_ebooks_in_calibre_library(str(tmp_path),
                                                supported_formats=["epub"])
    assert [os.path.basename(p) for p in found] == ["a.epub"]


def test_find_ebooks_empty_dir(tmp_path):
    assert main.find_ebooks_in_calibre_library(str(tmp_path)) == []


def test_find_ebooks_extension_match_is_case_insensitive(tmp_path):
    (tmp_path / "Loud.EPUB").write_text("x")
    found = main.find_ebooks_in_calibre_library(str(tmp_path))
    assert [os.path.basename(p) for p in found] == ["Loud.EPUB"]


# ---- list_available_fonts ----

def test_list_fonts_returns_sorted_paths(tmp_path):
    (tmp_path / "B.ttf").write_text("x")
    (tmp_path / "A.otf").write_text("x")
    (tmp_path / "ignored.txt").write_text("x")
    fonts = main.list_available_fonts(str(tmp_path))
    assert [os.path.basename(f) for f in fonts] == ["A.otf", "B.ttf"]


def test_list_fonts_missing_dir():
    assert main.list_available_fonts("/no/such/dir/abc123") == []


def test_list_fonts_accepts_all_supported_extensions(tmp_path):
    for name in ("a.ttf", "b.otf", "c.woff", "d.woff2", "e.png"):
        (tmp_path / name).write_text("x")
    out = [os.path.basename(p) for p in main.list_available_fonts(str(tmp_path))]
    assert out == ["a.ttf", "b.otf", "c.woff", "d.woff2"]


# ---- infer_font_face (main.py version) ----

def test_infer_face_regular():
    assert main.infer_font_face("Atkinson_Regular.ttf") == "regular"


def test_infer_face_bold():
    assert main.infer_font_face("Atkinson-Bold.otf") == "bold"


def test_infer_face_italic():
    assert main.infer_font_face("Atkinson Italic.ttf") == "italic"
    assert main.infer_font_face("Atkinson-Oblique.ttf") == "italic"


def test_infer_face_bold_italic():
    assert main.infer_font_face("Atkinson_BoldItalic.ttf") == "bold italic"


# ---- select_font_faces ----

def test_select_one_per_face_prefers_woff2_then_otf(tmp_path):
    # Same face provided in multiple formats; woff2 wins.
    (tmp_path / "X-Regular.ttf").write_text("x")
    (tmp_path / "X-Regular.otf").write_text("x")
    (tmp_path / "X-Regular.woff2").write_text("x")
    (tmp_path / "X-Bold.otf").write_text("x")
    (tmp_path / "X-Bold.ttf").write_text("x")

    out = main.select_font_faces(str(tmp_path))
    names = [os.path.basename(p) for p in out]
    # Order: regular, bold (no italic/bold-italic present)
    assert names == ["X-Regular.woff2", "X-Bold.otf"]


def test_select_font_faces_full_family_order(tmp_path):
    for name in ("F-Regular.ttf", "F-Bold.ttf", "F-Italic.ttf",
                 "F-BoldItalic.ttf"):
        (tmp_path / name).write_text("x")
    out = [os.path.basename(p) for p in main.select_font_faces(str(tmp_path))]
    assert out == ["F-Regular.ttf", "F-Bold.ttf", "F-Italic.ttf",
                   "F-BoldItalic.ttf"]


def test_select_font_faces_empty_dir(tmp_path):
    assert main.select_font_faces(str(tmp_path)) == []
