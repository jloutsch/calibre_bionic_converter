"""Issue #19: Kobo vs generic output target + remembered preference."""

import os
import sys
import zipfile
from unittest import mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import apply_bioread


def test_main_target_generic_skips_kepubify(tmp_path, monkeypatch):
    """--target generic -> plain .epub, process_epub_generic, no kepub."""
    epub = tmp_path / "B.epub"
    epub.write_bytes(b"PK\x03\x04")
    seen = {}
    monkeypatch.setattr(apply_bioread, "log_tool_versions", lambda: None)
    monkeypatch.setattr(apply_bioread, "process_epub_to_kepub",
                        lambda i, o, **k: seen.update(kepub=o))
    monkeypatch.setattr(apply_bioread, "process_epub_generic",
                        lambda i, o, **k: seen.update(generic=o))
    monkeypatch.setattr(sys, "argv",
                        ["apply_bioread.py", str(epub), "--target", "generic"])
    apply_bioread.main()
    assert seen.get("generic", "").endswith("B_fastread.epub")
    assert not seen.get("generic", "").endswith(".kepub.epub")
    assert "kepub" not in seen

    seen.clear()
    monkeypatch.setattr(sys, "argv",
                        ["apply_bioread.py", str(epub)])  # default = kobo
    apply_bioread.main()
    assert seen.get("kepub", "").endswith("B_fastread.kepub.epub")
    assert "generic" not in seen


def test_process_epub_generic_is_valid_epub_no_kobospan(tmp_path):
    """Generic path: bionic-applied, valid mimetype-first epub, NO koboSpan
    (kepubify is never invoked)."""
    book = tmp_path / "Book.epub"
    book.write_bytes(b"PK\x03\x04 src")
    out = tmp_path / "Book_fastread.epub"
    calls = []

    def fake_run(cmd, check=False, **kw):
        calls.append(cmd[0])
        if cmd and cmd[0] == "ebook-convert":
            with zipfile.ZipFile(cmd[2], "w") as zf:
                zf.writestr(zipfile.ZipInfo("mimetype"),
                            "application/epub+zip",
                            compress_type=zipfile.ZIP_STORED)
                zf.writestr("META-INF/container.xml", "<container/>")
                zf.writestr("c.xhtml",
                            "<html><body><p>hello there reader</p></body></html>")
        return mock.Mock(returncode=0, stdout="", stderr="")

    with mock.patch.object(apply_bioread.shutil, "which",
                           return_value="/bin/ebook-convert"), \
         mock.patch.object(apply_bioread.subprocess, "run", side_effect=fake_run):
        apply_bioread.process_epub_generic(str(book), str(out))

    assert "kepubify" not in calls, "generic must NOT run kepubify"
    assert out.is_file()
    with zipfile.ZipFile(out) as zf:
        assert zf.infolist()[0].filename == "mimetype"
        assert zf.infolist()[0].compress_type == zipfile.ZIP_STORED
        xhtml = zf.read("c.xhtml").decode()
    assert "<strong>" in xhtml, "bionic must be applied"
    assert "koboSpan" not in xhtml, "generic epub must have no koboSpans"


def test_remembered_target_preference_round_trip(tmp_path, monkeypatch):
    """main.resolve_target_preference asks once, persists 0600, reuses."""
    import importlib
    main = importlib.import_module("main")
    cfg = tmp_path / ".config" / "calibre_bionic_converter" / "config.json"
    monkeypatch.setattr(main, "_CONFIG_DIR", str(cfg.parent))
    monkeypatch.setattr(main, "_CONFIG_PATH", str(cfg))

    asked = []
    monkeypatch.setattr("builtins.input",
                        lambda _p: asked.append(1) or "n")  # n -> generic
    assert main.resolve_target_preference() == "generic"
    assert cfg.is_file()
    assert (cfg.stat().st_mode & 0o777) == 0o600, "config must be 0600"
    assert len(asked) == 1

    # Second call: no prompt, stored value reused.
    monkeypatch.setattr("builtins.input",
                        lambda _p: (_ for _ in ()).throw(
                            AssertionError("must not ask again")))
    assert main.resolve_target_preference() == "generic"
