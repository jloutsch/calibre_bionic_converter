"""Issue #20: input caching -- skip re-converting an unchanged input."""

import os
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import apply_bioread


def test_cache_key_stable_and_setting_sensitive(tmp_path, monkeypatch):
    src = tmp_path / "B.epub"
    src.write_bytes(b"hello book bytes")
    monkeypatch.setattr(apply_bioread.shutil, "which", lambda _b: None)
    k1 = apply_bioread._conversion_cache_key(str(src), "kobo", None, None)
    k2 = apply_bioread._conversion_cache_key(str(src), "kobo", None, None)
    assert k1 == k2, "same input+settings -> same key"
    assert apply_bioread._conversion_cache_key(str(src), "generic", None, None) != k1, \
        "target change must change the key"
    src.write_bytes(b"different bytes now")
    assert apply_bioread._conversion_cache_key(str(src), "kobo", None, None) != k1, \
        "input content change must change the key"


def test_main_skips_reconversion_when_fresh(tmp_path, monkeypatch, capsys):
    book = tmp_path / "B.epub"
    book.write_bytes(b"PK\x03\x04 src")
    out = tmp_path / "B_fastread.kepub.epub"
    calls = []

    def stub(i, o, **k):
        calls.append(o)
        open(o, "wb").write(b"PK out")  # produce the output so cache can see it

    monkeypatch.setattr(apply_bioread, "log_tool_versions", lambda: None)
    monkeypatch.setattr(apply_bioread.shutil, "which", lambda _b: None)
    monkeypatch.setattr(apply_bioread, "_tool_version", lambda _c: "x 1")
    monkeypatch.setattr(apply_bioread, "process_epub_to_kepub", stub)
    monkeypatch.setattr(sys, "argv", ["apply_bioread.py", str(book)])

    apply_bioread.main()                       # 1st run: converts
    assert len(calls) == 1
    assert os.path.isfile(str(out) + ".cbccache")

    apply_bioread.main()                       # 2nd run: cache hit, skip
    assert len(calls) == 1, "must not re-convert an unchanged input"
    assert "Up to date, skipping" in capsys.readouterr().out

    book.write_bytes(b"PK\x03\x04 CHANGED")    # input changed -> re-convert
    apply_bioread.main()
    assert len(calls) == 2, "changed input must invalidate the cache"


def test_cache_version_bump_invalidates(tmp_path, monkeypatch):
    book = tmp_path / "B.epub"
    book.write_bytes(b"data")
    monkeypatch.setattr(apply_bioread.shutil, "which", lambda _b: None)
    k_old = apply_bioread._conversion_cache_key(str(book), "kobo", None, None)
    monkeypatch.setattr(apply_bioread, "_CACHE_VERSION", "different-version")
    k_new = apply_bioread._conversion_cache_key(str(book), "kobo", None, None)
    assert k_old != k_new, "a pipeline-output version bump must invalidate cache"
