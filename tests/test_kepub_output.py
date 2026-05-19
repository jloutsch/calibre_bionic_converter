"""Tests for the kepub-first bionic pipeline.

The Kobo path runs kepubify BEFORE the bionic word-splitting so kepubify
emits ~1 koboSpan per sentence instead of ~2 per word (issue #28); a
chapter is then a single light spine document (no chunker, so no
mid-scene breaks / TOC off-by-one / per-fragment chapter stats). These
tests also cover the untrusted-input hardening (#22-#26).
"""

import os
import shutil
import sys
import zipfile
from unittest import mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import apply_bioread


# --- Untrusted-input safety (#22-#26) ----------------------------------

def test_cli_safe_path_rejects_flag_shaped_names(tmp_path):
    """Issue #22 (CWE-88): a leading-dash name must be refused, not run."""
    ok = tmp_path / "Book.epub"
    ok.write_bytes(b"x")
    safe = apply_bioread.cli_safe_path(str(ok))
    assert os.path.isabs(safe) and safe.endswith("Book.epub")

    with pytest.raises(ValueError):
        apply_bioread.cli_safe_path("-oh-no.epub")
    dashed = tmp_path / "-x.epub"
    dashed.write_bytes(b"x")
    with pytest.raises(ValueError):
        apply_bioread.cli_safe_path(str(dashed))


def test_safe_extract_zip_blocks_bombs_and_symlinks(tmp_path):
    """Issues #23/#25: caps + symlink-entry rejection on untrusted zips."""
    sym = tmp_path / "sym.zip"
    with zipfile.ZipFile(sym, "w") as zf:
        info = zipfile.ZipInfo("link")
        info.external_attr = (0o120777 << 16)  # S_IFLNK
        zf.writestr(info, "/etc/passwd")
    with zipfile.ZipFile(sym) as zf, pytest.raises(ValueError, match="symlink"):
        apply_bioread.safe_extract_zip(zf, str(tmp_path / "d1"))

    bomb = tmp_path / "bomb.zip"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("big.txt", b"\0" * (5 * 1024 * 1024))
    orig = apply_bioread.MAX_ZIP_TOTAL_BYTES
    apply_bioread.MAX_ZIP_TOTAL_BYTES = 1 * 1024 * 1024
    try:
        with zipfile.ZipFile(bomb) as zf, pytest.raises(ValueError, match="size limit"):
            apply_bioread.safe_extract_zip(zf, str(tmp_path / "d2"))
    finally:
        apply_bioread.MAX_ZIP_TOTAL_BYTES = orig

    many = tmp_path / "many.zip"
    with zipfile.ZipFile(many, "w") as zf:
        for i in range(3):
            zf.writestr(f"f{i}", "x")
    orig_n = apply_bioread.MAX_ZIP_ENTRIES
    apply_bioread.MAX_ZIP_ENTRIES = 2
    try:
        with zipfile.ZipFile(many) as zf, pytest.raises(ValueError, match="too many"):
            apply_bioread.safe_extract_zip(zf, str(tmp_path / "d3"))
    finally:
        apply_bioread.MAX_ZIP_ENTRIES = orig_n


# --- Container packaging ------------------------------------------------

def test_rezip_epub_mimetype_first_and_stored(tmp_path):
    """A repacked kepub must keep mimetype first and uncompressed."""
    src = tmp_path / "src"
    (src / "META-INF").mkdir(parents=True)
    (src / "mimetype").write_text("application/epub+zip")
    (src / "META-INF" / "container.xml").write_text("<container/>")
    (src / "c.xhtml").write_text("<html><body><p>hi</p></body></html>")

    out = tmp_path / "out.kepub.epub"
    apply_bioread.rezip_epub(str(src), str(out))

    with zipfile.ZipFile(out) as zf:
        first = zf.infolist()[0]
        assert first.filename == "mimetype"
        assert first.compress_type == zipfile.ZIP_STORED
        assert zf.read("mimetype") == b"application/epub+zip"
        assert zf.testzip() is None
        assert set(zf.namelist()) >= {"mimetype", "META-INF/container.xml",
                                      "c.xhtml"}


# --- Shared HTML transform ---------------------------------------------

def test_transform_html_tree_bionic_and_line_height(tmp_path):
    """Bionic bolding applied, fixed line-height stripped, text kept."""
    work = tmp_path / "w"
    work.mkdir()
    (work / "c.xhtml").write_text(
        "<html><head><style>p{line-height:2;color:red}</style></head>"
        "<body><p style='line-height:1.5'>hello world reading</p></body></html>")

    apply_bioread.transform_html_tree(str(work), [], str(work / "fonts"))

    html = (work / "c.xhtml").read_text()
    assert "<strong>" in html, "bionic bolding must be applied"
    assert "line-height" not in html, "fixed line spacing must be stripped"
    assert "color:red" in html, "other CSS must be preserved"
    import re
    assert "hello world reading" in re.sub(r"<[^>]+>", "", html)


def test_bionic_treats_contraction_as_one_word():
    """Issue #29: contractions/possessives are ONE word, one bold prefix."""
    from bs4 import BeautifulSoup

    for token, n_expected in [("don't", 1), ("I'm", 1), ("O'Brien", 1),
                              ("it’s", 1), ("rock'n'roll", 1)]:
        soup = BeautifulSoup(f"<p>{token}</p>", "html.parser")
        tn = soup.find("p").contents[0]
        apply_bioread.apply_bionic_reading_to_node(tn, soup)
        strongs = soup.find_all("strong")
        assert len(strongs) == n_expected, (
            f"{token!r}: expected {n_expected} <strong>, got "
            f"{[s.get_text() for s in strongs]} (apostrophe split it)")
        # Text is preserved exactly (no loss/reorder) and the bold part
        # is a real prefix of the token.
        assert soup.find("p").get_text() == token
        assert token.startswith(strongs[0].get_text())

    # A trailing possessive apostrophe stays punctuation, not a word char.
    soup = BeautifulSoup("<p>dogs' bones</p>", "html.parser")
    apply_bioread.apply_bionic_reading_to_node(
        soup.find("p").contents[0], soup)
    assert soup.find("p").get_text() == "dogs' bones"
    assert len(soup.find_all("strong")) == 2  # "dogs" and "bones"


def test_neutralize_dropcap_css_strips_injected_dropcap():
    """calibre's 5em floated :first-letter drop cap must be neutralized.

    It clips on the left when zoomed and the source book never had it;
    only float/font-size are dropped, other declarations (and benign
    font-family-only first-letter rules) are kept.
    """
    css = (".pcalibre:first-letter{float:left;font-size:5em;"
           "margin:0 0.05em 0 0;font-family:'X'}"
           ".itc-dc::first-letter{font-family:'ITC Benguiat Std'}"
           "p{font-size:1em;line-height:2}")
    out = apply_bioread.neutralize_dropcap_css(css)
    assert "float" not in out
    assert "font-size:5em" not in out
    dropcap_rule = out.split("}")[0]  # the .pcalibre:first-letter block
    assert "font-size" not in dropcap_rule, "drop-cap font-size must be gone"
    assert "margin:0 0.05em 0 0" in out, "non-dropcap decls kept"
    assert "font-family:'X'" in out
    assert "font-family:'ITC Benguiat Std'" in out, "benign rule untouched"
    # Body font-size outside a first-letter rule must NOT be stripped.
    assert "font-size:1em" in out
    # Combined cleaner also frees line spacing.
    assert "line-height" not in apply_bioread._clean_stylesheet(css)


def test_transform_html_tree_injects_font_css(tmp_path):
    """With font faces, an @font-face BionicFont block is added to head."""
    work = tmp_path / "w"
    (work / "fonts").mkdir(parents=True)
    (work / "c.xhtml").write_text("<html><head></head><body><p>x y</p></body></html>")
    faces = [{"filename": "F-Bold.ttf", "safe_filename": "font_1.ttf",
              "weight": "700", "style": "normal"}]

    apply_bioread.transform_html_tree(str(work), faces, str(work / "fonts"))

    html = (work / "c.xhtml").read_text()
    assert "@font-face" in html and "BionicFont" in html
    assert "font_1.ttf" in html


# --- kepubify guard -----------------------------------------------------

def test_require_kepubify_missing_fails_loudly(monkeypatch):
    monkeypatch.setattr(apply_bioread.shutil, "which", lambda _b: None)
    with pytest.raises(SystemExit) as exc:
        apply_bioread.require_kepubify()
    assert exc.value.code == 1


def test_require_kepubify_rejects_old_major(monkeypatch):
    monkeypatch.setattr(apply_bioread.shutil, "which", lambda _b: "/bin/kepubify")
    monkeypatch.setattr(apply_bioread, "_tool_version", lambda _c: "kepubify 3.1.6")
    with pytest.raises(SystemExit) as exc:
        apply_bioread.require_kepubify()
    assert exc.value.code == 1


def test_require_kepubify_accepts_supported(monkeypatch):
    monkeypatch.setattr(apply_bioread.shutil, "which", lambda _b: "/bin/kepubify")
    monkeypatch.setattr(apply_bioread, "_tool_version", lambda _c: "kepubify 4.0.4")
    apply_bioread.require_kepubify()  # must not raise/exit


def test_require_calibre_missing_fails_loudly(monkeypatch):
    monkeypatch.setattr(apply_bioread.shutil, "which", lambda _b: None)
    with pytest.raises(SystemExit) as exc:
        apply_bioread.require_calibre()
    assert exc.value.code == 1


def test_run_ebook_convert_fails_loudly_not_traceback(monkeypatch, capsys):
    """ebook-convert failure must exit(1) with a message, not a traceback
    (review finding: only kepubify was guarded before)."""
    def boom(cmd, check=False, **kw):
        raise apply_bioread.subprocess.CalledProcessError(7, cmd)
    monkeypatch.setattr(apply_bioread.subprocess, "run", boom)
    with pytest.raises(SystemExit) as exc:
        apply_bioread._run_ebook_convert(["a", "b"], "testing")
    assert exc.value.code == 1
    assert "ebook-convert failed" in capsys.readouterr().out

    # Missing binary (OSError) is also handled, not propagated.
    def missing(cmd, check=False, **kw):
        raise FileNotFoundError("ebook-convert")
    monkeypatch.setattr(apply_bioread.subprocess, "run", missing)
    with pytest.raises(SystemExit):
        apply_bioread._run_ebook_convert(["a"], "testing")


def test_neutralize_dropcap_css_is_not_redos(monkeypatch):
    """HIGH review finding: a brace-free run containing ':first-letter'
    with no following '{' must not blow up (catastrophic backtracking).
    """
    import time
    hostile = "a:first-letter" + ("x" * 80_000)  # no '{' anywhere
    start = time.time()
    out = apply_bioread.neutralize_dropcap_css(hostile)
    elapsed = time.time() - start
    assert elapsed < 1.0, f"ReDoS: took {elapsed:.2f}s on 80KB hostile CSS"
    assert out == hostile, "no rule (no braces) -> input unchanged"
    # And it still neutralizes a real drop-cap rule embedded in noise.
    css = hostile + ".p:first-letter{float:left;font-size:5em;color:red}"
    fixed = apply_bioread.neutralize_dropcap_css(css)
    assert "float" not in fixed and "font-size:5em" not in fixed
    assert "color:red" in fixed


def test_log_tool_versions_records_both(capsys):
    def fake_run(cmd, check=False, **kw):
        return mock.Mock(returncode=0, stdout=f"{cmd[0]} 9.9.9\n", stderr="")
    with mock.patch.object(apply_bioread.shutil, "which", return_value="/bin/x"), \
         mock.patch.object(apply_bioread.subprocess, "run", side_effect=fake_run):
        apply_bioread.log_tool_versions()
    out = capsys.readouterr().out
    assert "calibre ebook-convert: ebook-convert 9.9.9" in out
    assert "kepubify: kepubify 9.9.9" in out


# --- Pipeline ordering (the core #28 invariant) ------------------------

def _fake_pipeline(tmp_path, kepub_xhtml):
    """Return a fake subprocess.run: calibre makes plain.epub, kepubify
    wraps it into a kepub zip whose content xhtml is ``kepub_xhtml``."""
    calls = []

    def fake_run(cmd, check=False, **kw):
        calls.append(list(cmd))
        if cmd and cmd[0] == "ebook-convert":
            with open(cmd[2], "wb") as fh:
                fh.write(b"PK\x03\x04 plain epub")
        elif cmd and cmd[0] == "kepubify":
            out = cmd[cmd.index("-o") + 1]
            with zipfile.ZipFile(out, "w") as zf:
                zf.writestr(zipfile.ZipInfo("mimetype"),
                            "application/epub+zip",
                            compress_type=zipfile.ZIP_STORED)
                zf.writestr("META-INF/container.xml", "<container/>")
                zf.writestr("c.xhtml", kepub_xhtml)
        return mock.Mock(returncode=0, stdout="", stderr="")

    return fake_run, calls


def test_process_epub_to_kepub_kepubify_before_bionic(tmp_path):
    """kepubify must run on the plain epub, then bionic on its output.

    The kepub xhtml already has koboSpans (kepubify ran first); after the
    pipeline those koboSpans are intact and bionic <strong> sits INSIDE
    them -- never the other way round (issue #28).
    """
    book = tmp_path / "Book.epub"
    book.write_bytes(b"PK\x03\x04 src")
    out = tmp_path / "Book_fastread.kepub.epub"
    kepub_xhtml = ('<html><body><p>'
                   '<span class="koboSpan" id="kobo.1.1">Hello there reader</span>'
                   '</p></body></html>')
    fake_run, calls = _fake_pipeline(tmp_path, kepub_xhtml)

    with mock.patch.object(apply_bioread.shutil, "which",
                           return_value="/bin/kepubify"), \
         mock.patch.object(apply_bioread, "_tool_version",
                           return_value="kepubify 4.0.4"), \
         mock.patch.object(apply_bioread.subprocess, "run", side_effect=fake_run):
        apply_bioread.process_epub_to_kepub(str(book), str(out))

    order = [c[0] for c in calls]
    assert order == ["ebook-convert", "kepubify"], (
        f"kepubify must run after calibre, before bionic: {order}")
    assert calls[1][0] == "kepubify"
    assert calls[1][calls[1].index("-o") + 1] != str(out), \
        "kepubify writes a temp kepub, not the final output"

    assert out.is_file()
    with zipfile.ZipFile(out) as zf:
        assert zf.infolist()[0].filename == "mimetype"
        assert zf.infolist()[0].compress_type == zipfile.ZIP_STORED
        xhtml = zf.read("c.xhtml").decode()
    assert 'class="koboSpan"' in xhtml, "kepubify's koboSpans must survive"
    assert "<strong>" in xhtml, "bionic must be applied after kepubify"
    # bionic <strong> is INSIDE the koboSpan, not wrapping it.
    span_open = xhtml.index('<span class="koboSpan"')
    span_close = xhtml.index("</span>")
    assert span_open < xhtml.index("<strong>") < span_close


def test_process_epub_to_kepub_atomic_and_symlink_refused(tmp_path):
    """Issue #24: never write the final kepub through a planted symlink."""
    book = tmp_path / "Book.epub"
    book.write_bytes(b"PK\x03\x04 src")
    victim = tmp_path / "victim"
    victim.write_text("precious")
    out = tmp_path / "Book_fastread.kepub.epub"
    out.symlink_to(victim)

    fake_run, _ = _fake_pipeline(tmp_path, "<html><body><p>a b c</p></body></html>")
    with mock.patch.object(apply_bioread.shutil, "which",
                           return_value="/bin/kepubify"), \
         mock.patch.object(apply_bioread, "_tool_version",
                           return_value="kepubify 4.0.4"), \
         mock.patch.object(apply_bioread.subprocess, "run", side_effect=fake_run):
        with pytest.raises(SystemExit) as exc:
            apply_bioread.process_epub_to_kepub(str(book), str(out))
    assert exc.value.code == 1
    assert victim.read_text() == "precious", "symlink target must be untouched"


def test_main_routes_epub_to_kepub_and_mobi_to_htmlz(tmp_path, monkeypatch):
    """epub -> .kepub.epub via the kepub path; mobi -> process_htmlz."""
    epub = tmp_path / "B.epub"
    epub.write_bytes(b"PK\x03\x04")
    seen = {}
    monkeypatch.setattr(apply_bioread, "log_tool_versions", lambda: None)
    monkeypatch.setattr(apply_bioread, "process_epub_to_kepub",
                        lambda i, o, **k: seen.update(kepub=o))
    monkeypatch.setattr(apply_bioread, "process_htmlz",
                        lambda i, o, f, **k: seen.update(htmlz=o))
    monkeypatch.setattr(sys, "argv", ["apply_bioread.py", str(epub)])
    apply_bioread.main()
    assert seen.get("kepub", "").endswith("B_fastread.kepub.epub")
    assert "htmlz" not in seen

    seen.clear()
    mobi = tmp_path / "B.mobi"
    mobi.write_bytes(b"x")
    monkeypatch.setattr(sys, "argv", ["apply_bioread.py", str(mobi)])
    apply_bioread.main()
    assert seen.get("htmlz", "").endswith("B_fastread.mobi")
    assert "kepub" not in seen


# --- Real-tool integration (the #28 invariant end-to-end) --------------

def _minimal_epub(path, body):
    with zipfile.ZipFile(path, "w") as zf:
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
                    'xmlns="http://www.w3.org/1999/xhtml"><body>' + body
                    + '</body></html>')


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.skipif(shutil.which("ebook-convert") is None
                    or shutil.which("kepubify") is None,
                    reason="calibre/kepubify not installed")
def test_real_kepub_first_keeps_kobospans_per_sentence(tmp_path):
    """End-to-end #28: kepubify-before-bionic keeps koboSpans ~per
    sentence, NOT ~2 per word. Proves the node-density win on real tools.
    """
    src = tmp_path / "Big.epub"
    sentence = "the quick brown fox jumps over the lazy dog. "
    paras = "".join(f"<p>{sentence * 5}</p>" for _ in range(40))
    _minimal_epub(src, paras)
    words = paras.count(" ")  # rough word count

    out = tmp_path / "Big_fastread.kepub.epub"
    apply_bioread.process_epub_to_kepub(str(src), str(out))

    with zipfile.ZipFile(out) as zf:
        assert zf.infolist()[0].filename == "mimetype"
        assert zf.testzip() is None
        html = "".join(zf.read(n).decode("utf-8", "replace")
                        for n in zf.namelist()
                        if n.lower().endswith((".xhtml", ".html", ".htm")))

    kobospans = html.count("koboSpan")
    strongs = html.count("<strong")
    assert strongs > 100, "bionic must have been applied"
    assert kobospans > 0, "kepubify must have run"
    # The whole point of #28: koboSpans stay close to sentence-count, far
    # below word-count. Pre-fix this ratio was ~2 koboSpans per word.
    assert kobospans < words, (
        f"koboSpans ({kobospans}) must be far below word count ({words}); "
        "kepubify-before-bionic regression"
    )
