"""Regression tests for the Kobo-safe (.kepub.epub) output guarantee.

Plain ``application/epub+zip`` output from this tool froze Kobo devices
(legacy Adobe RMSDK renderer hang -> sickel watchdog kills nickel). The
pipeline must now always emit ``.kepub.epub`` for epub inputs and never
leave the fragile plain epub behind. These tests fail against the
pre-fix code path.
"""

import os
import shutil
import sys
import zipfile
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
        if "--version" in cmd:
            # log_tool_versions() probes ebook-convert and kepubify.
            return mock.Mock(returncode=0, stdout=f"{cmd[0]} 4.0.4\n", stderr="")
        assert cmd[0] == "kepubify"
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
         mock.patch.object(apply_bioread, "log_tool_versions"), \
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


def test_log_tool_versions_records_both_tools(capsys):
    """Each run must record the calibre + kepubify versions it depended on."""
    def fake_run(cmd, check=False, **kwargs):
        return mock.Mock(returncode=0, stdout=f"{cmd[0]} 9.9.9\n", stderr="")

    with mock.patch.object(apply_bioread.shutil, "which", return_value="/bin/x"), \
         mock.patch.object(apply_bioread.subprocess, "run", side_effect=fake_run):
        apply_bioread.log_tool_versions()

    out = capsys.readouterr().out
    assert "calibre ebook-convert: ebook-convert 9.9.9" in out
    assert "kepubify: kepubify 9.9.9" in out


def test_log_tool_versions_missing_tool_is_not_fatal(capsys):
    with mock.patch.object(apply_bioread.shutil, "which", return_value=None):
        apply_bioread.log_tool_versions()  # must not raise

    out = capsys.readouterr().out
    assert "kepubify: not found" in out


def _write_minimal_epub(path):
    """Build the smallest spec-valid epub kepubify will accept."""
    with zipfile.ZipFile(path, "w") as zf:
        # mimetype must be first and stored (uncompressed).
        zf.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip",
                    compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml",
                    '<?xml version="1.0"?><container version="1.0" '
                    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                    '<rootfiles><rootfile full-path="content.opf" '
                    'media-type="application/oebps-package+xml"/></rootfiles></container>')
        zf.writestr("content.opf",
                    '<?xml version="1.0"?><package '
                    'xmlns="http://www.idpf.org/2007/opf" version="3.0" '
                    'unique-identifier="id"><metadata '
                    'xmlns:dc="http://purl.org/dc/elements/1.1/">'
                    '<dc:identifier id="id">x</dc:identifier><dc:title>t</dc:title>'
                    '<dc:language>en</dc:language></metadata><manifest>'
                    '<item id="c" href="c.xhtml" media-type="application/xhtml+xml"/>'
                    '</manifest><spine><itemref idref="c"/></spine></package>')
        zf.writestr("c.xhtml",
                    '<?xml version="1.0"?><html '
                    'xmlns="http://www.w3.org/1999/xhtml"><body><p>hi</p></body></html>')


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("kepubify") is None,
                    reason="kepubify not installed")
def test_real_kepubify_o_file_contract(tmp_path):
    """End-to-end check of the real `kepubify -o <file>` contract.

    The unit tests mock subprocess, so nothing else catches a kepubify CLI
    change (e.g. -o no longer accepting a full file path). This drives the
    real binary against a real epub.
    """
    plain = tmp_path / "Book_fastread.epub"
    _write_minimal_epub(plain)

    result = apply_bioread.convert_to_kepub(str(plain))

    expected = tmp_path / "Book_fastread.kepub.epub"
    assert result == str(expected)
    assert expected.is_file(), "kepubify -o <file> must produce exactly that path"
    assert not plain.exists(), "plain intermediate must be removed"
    with zipfile.ZipFile(expected) as zf:
        assert zf.testzip() is None, "produced kepub must be a valid zip"

