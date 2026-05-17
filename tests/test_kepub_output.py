"""Regression tests for the Kobo-safe (.kepub.epub) output guarantee.

Plain ``application/epub+zip`` output from this tool froze Kobo devices
(legacy Adobe RMSDK renderer hang -> sickel watchdog kills nickel). The
pipeline must now always emit ``.kepub.epub`` for epub inputs and never
leave the fragile plain epub behind. These tests fail against the
pre-fix code path.
"""

import os
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import apply_bioread


def _fake_process_htmlz(create_at):
    """Return a process_htmlz stub that just drops a plain epub on disk."""

    def _stub(input_file, output_file, original_format, font_path=None, font_dir=None):
        with open(output_file, "wb") as handle:
            handle.write(b"PK\x03\x04 plain epub bytes")
        assert output_file == create_at

    return _stub


def test_main_emits_kepub_and_removes_plain_epub(tmp_path):
    book = tmp_path / "Book.epub"
    book.write_bytes(b"PK\x03\x04 source")
    plain = tmp_path / "Book_fastread.epub"
    kepub = tmp_path / "Book_fastread.kepub.epub"

    def fake_run(cmd, check=False, **kwargs):
        assert cmd[0] == "kepubify"
        if "--version" in cmd:
            return mock.Mock(returncode=0, stdout="kepubify 4.0.4\n", stderr="")
        out_path = cmd[cmd.index("-o") + 1]
        with open(out_path, "wb") as handle:
            handle.write(b"PK\x03\x04 kepub bytes")
        return mock.Mock(returncode=0)

    with mock.patch.object(apply_bioread, "process_htmlz",
                           _fake_process_htmlz(str(plain))), \
         mock.patch.object(apply_bioread.shutil, "which", return_value="/usr/bin/kepubify"), \
         mock.patch.object(apply_bioread.subprocess, "run", side_effect=fake_run), \
         mock.patch.object(sys, "argv", ["apply_bioread.py", str(book)]):
        apply_bioread.main()

    assert kepub.is_file(), "pipeline must emit .kepub.epub for epub input"
    assert not plain.exists(), "fragile plain _fastread.epub must be removed"


def test_missing_kepubify_fails_loudly(tmp_path):
    book = tmp_path / "Book.epub"
    book.write_bytes(b"PK\x03\x04 source")
    plain = tmp_path / "Book_fastread.epub"

    with mock.patch.object(apply_bioread, "process_htmlz",
                           _fake_process_htmlz(str(plain))), \
         mock.patch.object(apply_bioread.shutil, "which", return_value=None), \
         mock.patch.object(sys, "argv", ["apply_bioread.py", str(book)]):
        with pytest.raises(SystemExit) as exc:
            apply_bioread.main()

    assert exc.value.code == 1, "missing kepubify must fail loudly, not ship plain epub"
    assert plain.exists(), "plain epub is kept so the failure is recoverable"


def test_non_epub_output_is_left_untouched(tmp_path):
    """mobi/azw3 don't use the kepub path; kepubify must not be invoked."""
    book = tmp_path / "Book.mobi"
    book.write_bytes(b"source")
    produced = tmp_path / "Book_fastread.mobi"

    with mock.patch.object(apply_bioread, "process_htmlz",
                           _fake_process_htmlz(str(produced))), \
         mock.patch.object(apply_bioread, "convert_to_kepub") as conv, \
         mock.patch.object(sys, "argv", ["apply_bioread.py", str(book)]):
        apply_bioread.main()

    conv.assert_not_called()
    assert produced.is_file()


def test_old_kepubify_major_is_rejected(tmp_path):
    """A kepubify older than the verified -o <file> contract must fail loudly."""
    book = tmp_path / "Book.epub"
    book.write_bytes(b"PK\x03\x04 source")
    plain = tmp_path / "Book_fastread.epub"

    def fake_run(cmd, check=False, **kwargs):
        assert "--version" in cmd, "must reject before invoking conversion"
        return mock.Mock(returncode=0, stdout="kepubify 3.1.6\n", stderr="")

    with mock.patch.object(apply_bioread, "process_htmlz",
                           _fake_process_htmlz(str(plain))), \
         mock.patch.object(apply_bioread.shutil, "which", return_value="/usr/bin/kepubify"), \
         mock.patch.object(apply_bioread.subprocess, "run", side_effect=fake_run), \
         mock.patch.object(sys, "argv", ["apply_bioread.py", str(book)]):
        with pytest.raises(SystemExit) as exc:
            apply_bioread.main()

    assert exc.value.code == 1
    assert plain.exists(), "plain epub kept so the failure is recoverable"

