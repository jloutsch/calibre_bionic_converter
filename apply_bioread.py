import sys
import os
import re
import zipfile
import tempfile
import subprocess
import shutil
import argparse
import hashlib
import json

try:
    from bs4 import BeautifulSoup
except ModuleNotFoundError:
    print(
        "Missing dependency: beautifulsoup4. Run "
        "'source venv/bin/activate && python -m pip install -r requirements.txt' "
        "from this repository, then run 'python main.py'."
    )
    sys.exit(1)


# --- Untrusted-input safety ---------------------------------------------
# This tool processes arbitrary user-supplied ebooks (often downloaded) and
# shells out to calibre/kepubify, so every external path and archive entry
# is treated as hostile. See issues #22-#26.

# Decompression-bomb caps for archive extraction (issue #23, CWE-409). The
# robust, non-brittle defense is an absolute uncompressed-size cap plus an
# entry-count cap (a per-entry ratio check false-positived on legitimately
# compressible content).
MAX_ZIP_ENTRIES = 50_000
MAX_ZIP_TOTAL_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB uncompressed


def cli_safe_path(path):
    """Return an absolute path safe to pass as a subprocess positional.

    ``ebook-convert <file>`` parses an argv that looks like an option
    (e.g. ``-x.epub``) as a flag, not a positional -- an argument-
    injection primitive (issue #22, CWE-88). ``main.py`` auto-walks the
    Calibre library so the name is attacker-influenced. Absolutising
    guarantees a leading ``/``; the basename check rejects the hostile
    name loudly.
    """
    abspath = os.path.abspath(path)
    if os.path.basename(abspath).startswith("-"):
        raise ValueError("Refusing path whose name starts with '-': "
                         f"{os.path.basename(abspath)!r}")
    return abspath


def _zipinfo_is_symlink(info):
    """True if a ZipInfo entry is a Unix symlink (S_IFLNK in mode bits)."""
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def safe_extract_zip(zip_file, destination):
    """Extract an untrusted zip safely.

    Defends against zip-slip (CWE-22), symlink escape, and decompression
    bombs (CWE-409). Error messages use the basename only so a full local
    path is never leaked (issue #25).
    """
    destination_path = os.path.realpath(destination)
    infos = zip_file.infolist()

    if len(infos) > MAX_ZIP_ENTRIES:
        raise ValueError(
            f"Archive has too many entries ({len(infos)} > {MAX_ZIP_ENTRIES})"
        )

    total = 0
    for member in infos:
        leaf = os.path.basename(member.filename)
        member_path = os.path.realpath(os.path.join(destination, member.filename))
        if member_path != destination_path and not member_path.startswith(destination_path + os.sep):
            raise ValueError(f"Unsafe path in archive: {leaf!r}")
        if _zipinfo_is_symlink(member):
            raise ValueError(f"Refusing symlink entry in archive: {leaf!r}")
        total += member.file_size
        if total > MAX_ZIP_TOTAL_BYTES:
            raise ValueError("Archive exceeds uncompressed size limit (zip bomb?)")

    zip_file.extractall(destination)


def rezip_epub(srcdir, dest):
    """Zip ``srcdir`` back into a spec-valid epub/kepub.

    The ``mimetype`` entry MUST be first and stored uncompressed, or Kobo
    (and strict readers) reject the container.
    """
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        mimetype = os.path.join(srcdir, "mimetype")
        if os.path.isfile(mimetype):
            zf.write(mimetype, "mimetype", compress_type=zipfile.ZIP_STORED)
        for root, _dirs, files in sorted(os.walk(srcdir)):
            for filename in sorted(files):
                path = os.path.join(root, filename)
                arc = os.path.relpath(path, srcdir)
                if arc == "mimetype":
                    continue
                zf.write(path, arc)


# --- Bionic transform ---------------------------------------------------

def apply_bionic_reading_to_node(text_node, soup):
    # A word may contain internal apostrophes (straight ' or curly U+2019
    # / U+2018): contractions and possessives are ONE word. Plain \b\w+\b
    # split "don't" -> "don" + "t" and bolded each separately, mangling
    # every contraction on every page (issue #29). Require a word char on
    # both sides of any apostrophe so a trailing quote stays punctuation.
    word_pattern = re.compile(r"\w+(?:['’‘]\w+)*", re.UNICODE)

    new_contents = []
    last_end = 0
    text = text_node.string

    for match in word_pattern.finditer(text):
        start, end = match.span()
        word = match.group()

        if start > last_end:
            new_contents.append(text[last_end:start])

        length = len(word)
        bold_chars = max(1, int(round(length * 0.5)))
        bold_part = word[:bold_chars]
        normal_part = word[bold_chars:]

        bold_tag = soup.new_tag("strong")
        bold_tag.string = bold_part
        new_contents.append(bold_tag)
        new_contents.append(normal_part)

        last_end = end

    if last_end < len(text):
        new_contents.append(text[last_end:])

    text_node.replace_with(*new_contents)


def remove_line_height_declarations(css_text):
    return re.sub(r"(?i)\s*line-height\s*:\s*[^;}\"]+;?", "", css_text)


def neutralize_dropcap_css(css_text):
    """Strip calibre's injected oversized floated drop cap.

    calibre's EPUB output adds e.g.
    ``.pcalibre:first-letter{float:left;font-size:5em;...}`` -- a drop
    cap the source book never had. With almost no left bearing it
    overflows and Kobo clips its left edge when the reader zooms. The
    source's own ``::first-letter`` rules only set font-family (benign),
    so dropping ``float`` and ``font-size`` from every first-letter rule
    restores the original (no big initial) without touching anything
    else.
    """
    # Linear scan, NOT one big regex. Any pattern with an unbounded
    # [^{}]* *before* a literal "{" is O(n^2) on attacker CSS that has
    # ":first-letter" but no following brace (catastrophic backtracking,
    # CWE-1333, reachable from untrusted ebook CSS). Instead anchor on
    # the literal brace block "{...}" -- the engine scans for "{" in
    # O(n) with no backtracking -- and inspect the preceding selector
    # text per block.
    if not re.search(r"first-letter", css_text, re.I):
        return css_text  # fast path: nothing to do

    decl_kill = re.compile(r"(?i)\s*(?:float|font-size)\s*:\s*[^;}]+;?")
    is_first_letter = re.compile(r":{1,2}first-letter", re.I)

    out = []
    pos = 0
    for block in re.finditer(r"\{([^{}]*)\}", css_text):
        selector = css_text[pos:block.start()]
        body = block.group(1)
        if is_first_letter.search(selector):
            body = decl_kill.sub("", body)
        out.append(selector)
        out.append("{" + body + "}")
        pos = block.end()
    out.append(css_text[pos:])
    return "".join(out)


def _clean_stylesheet(css_text):
    """All CSS-text fixes: free up line spacing + kill the drop cap."""
    return neutralize_dropcap_css(remove_line_height_declarations(css_text))


def _ensure_head(soup):
    """Return the document <head>, creating <html>/<head> if absent."""
    if soup.head:
        return soup.head
    head = soup.new_tag("head")
    if soup.html:
        soup.html.insert(0, head)
    else:
        soup.append(head)
    return head


def remove_fixed_line_spacing(soup):
    for tag in soup.find_all(style=True):
        cleaned_style = remove_line_height_declarations(tag["style"]).strip()
        if cleaned_style:
            tag["style"] = cleaned_style
        else:
            del tag["style"]

    for style_tag in soup.find_all("style"):
        if style_tag.string:
            style_tag.string.replace_with(_clean_stylesheet(style_tag.string))


def remove_fixed_line_spacing_from_css_file(css_path):
    with open(css_path, "r", encoding="utf-8") as f:
        css = f.read()

    cleaned_css = _clean_stylesheet(css)
    if cleaned_css != css:
        with open(css_path, "w", encoding="utf-8") as f:
            f.write(cleaned_css)


# --- Font embedding -----------------------------------------------------

def font_format_for_file(filename):
    font_ext = os.path.splitext(filename)[1].lower()
    return {
        ".ttf": "truetype",
        ".otf": "opentype",
        ".woff": "woff",
        ".woff2": "woff2",
    }.get(font_ext, "truetype")


def infer_font_face(filename):
    name = os.path.splitext(filename)[0].lower().replace("-", "_").replace(" ", "_")
    is_bold = "bold" in name
    is_italic = "italic" in name or "oblique" in name

    if is_bold and is_italic:
        return "700", "italic"
    if is_bold:
        return "700", "normal"
    if is_italic:
        return "400", "italic"
    return "400", "normal"


def safe_font_filename(index, filename):
    ext = os.path.splitext(filename)[1].lower()
    return f"font_{index}{ext}"


def list_font_faces(font_dir):
    if not font_dir or not os.path.isdir(font_dir):
        return []

    font_extensions = {".ttf", ".otf", ".woff", ".woff2"}
    extension_rank = {".woff2": 0, ".otf": 1, ".ttf": 2, ".woff": 3}
    candidates = []

    for filename in os.listdir(font_dir):
        ext = os.path.splitext(filename)[1].lower()
        if ext in font_extensions:
            weight, style = infer_font_face(filename)
            candidates.append({
                "source_path": os.path.join(font_dir, filename),
                "filename": filename,
                "weight": weight,
                "style": style,
                "rank": extension_rank.get(ext, 99),
            })

    selected = {}
    for candidate in sorted(candidates, key=lambda item: (item["weight"], item["style"], item["rank"], item["filename"])):
        key = (candidate["weight"], candidate["style"])
        if key not in selected:
            selected[key] = candidate

    selected_faces = list(selected.values())
    for index, face in enumerate(selected_faces, 1):
        face["safe_filename"] = safe_font_filename(index, face["filename"])

    return selected_faces


def build_font_css(font_faces, font_url_prefix="fonts"):
    if not font_faces:
        return ""

    css_blocks = []
    for face in font_faces:
        font_filename = face["safe_filename"]
        font_format = font_format_for_file(font_filename)
        css_blocks.append(f"""
@font-face {{
    font-family: 'BionicFont';
    src: url('{font_url_prefix}/{font_filename}') format('{font_format}');
    font-weight: {face["weight"]};
    font-style: {face["style"]};
}}
""".strip())

    css_blocks.append("""
html, body {
    font-family: 'BionicFont', sans-serif;
}
strong, b {
    font-family: 'BionicFont', sans-serif;
    font-weight: 700;
}
em, i {
    font-family: 'BionicFont', sans-serif;
    font-style: italic;
}
strong em, strong i, b em, b i, em strong, i strong, em b, i b {
    font-family: 'BionicFont', sans-serif;
    font-weight: 700;
    font-style: italic;
}
""".strip())

    return "\n\n".join(css_blocks)


def _collect_font_faces(work_dir, font_path, font_dir):
    """Copy custom fonts into ``work_dir/fonts`` and return their faces."""
    faces = []
    fonts_dir = os.path.join(work_dir, "fonts")

    if font_path and os.path.exists(font_path):
        os.makedirs(fonts_dir, exist_ok=True)
        filename = os.path.basename(font_path)
        weight, style = infer_font_face(filename)
        safe_filename = safe_font_filename(1, filename)
        shutil.copy(font_path, os.path.join(fonts_dir, safe_filename))
        faces.append({"filename": filename, "safe_filename": safe_filename,
                      "weight": weight, "style": style})

    for face in list_font_faces(font_dir):
        os.makedirs(fonts_dir, exist_ok=True)
        shutil.copy(face["source_path"],
                    os.path.join(fonts_dir, face["safe_filename"]))
        faces.append({"filename": face["filename"],
                      "safe_filename": face["safe_filename"],
                      "weight": face["weight"], "style": face["style"]})
    return faces, fonts_dir


def transform_html_tree(root_dir, font_faces, fonts_dir):
    """Apply line-spacing strip, font CSS, and the bionic effect in place.

    Operates on whatever HTML/XHTML/CSS is under ``root_dir`` -- a calibre
    HTMLZ extraction or a kepubify-produced kepub. No chunking and no
    page-break markup: the kepub-first pipeline keeps each chapter a
    single, light spine document (issue #28), so the chunker is gone.
    """
    for root, _dirs, files in os.walk(root_dir):
        for filename in files:
            low = filename.lower()
            path = os.path.join(root, filename)
            if low.endswith(".css"):
                remove_fixed_line_spacing_from_css_file(path)
            elif low.endswith((".html", ".htm", ".xhtml")):
                with open(path, "r", encoding="utf-8") as f:
                    soup = BeautifulSoup(f, "html.parser")

                remove_fixed_line_spacing(soup)

                if font_faces:
                    prefix = os.path.relpath(
                        fonts_dir, os.path.dirname(path)).replace(os.sep, "/")
                    style_tag = soup.new_tag("style")
                    style_tag.string = build_font_css(
                        font_faces, font_url_prefix=prefix)
                    _ensure_head(soup).append(style_tag)

                for text_node in soup.find_all(string=True):
                    parent = text_node.parent.name if text_node.parent else None
                    if parent in ["style", "script", "[document]", "head", "title"]:
                        continue
                    if text_node.strip():
                        apply_bionic_reading_to_node(text_node, soup)

                with open(path, "w", encoding="utf-8") as f:
                    f.write(str(soup))


# --- External tool guards ----------------------------------------------

# The single-file `-o <file>` output contract this tool relies on was
# verified on kepubify 4.x. Guard against older majors.
MIN_KEPUBIFY_MAJOR = 4


def _tool_version(command):
    """Return the first line of ``<command> --version``, or None."""
    try:
        result = subprocess.run(
            list(command) + ["--version"],
            capture_output=True, text=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    output = (result.stdout or result.stderr or "").strip()
    return output.splitlines()[0] if output else None


def _kepubify_major(version_line):
    """Extract the integer major version from a ``kepubify X.Y.Z`` line."""
    if not version_line:
        return None
    match = re.search(r"(\d+)\.\d+", version_line)
    return int(match.group(1)) if match else None


def log_tool_versions():
    """Print the external converter versions a run depends on (CACE)."""
    for label, binary in (("calibre ebook-convert", "ebook-convert"),
                           ("kepubify", "kepubify")):
        version = _tool_version([binary]) if shutil.which(binary) else None
        print(f"[tool] {label}: {version or 'not found'}")


# Bump whenever the pipeline's OUTPUT for a given input could change
# (transform logic, kepubify-before-bionic, dropcap/apostrophe rules).
# Stale-cache guard: a build with a different value invalidates old
# cached outputs (CACE).
_CACHE_VERSION = "kepub-first-2026-05-18"


def _conversion_cache_key(input_file, target, font_path, font_dir):
    """Stable key over everything that affects the produced book."""
    h = hashlib.sha256()
    with open(input_file, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    meta = {
        "v": _CACHE_VERSION,
        "input_sha256": h.hexdigest(),
        "target": target,
        "font_path": os.path.basename(font_path) if font_path else None,
        "font_dir": sorted(os.listdir(font_dir))
        if font_dir and os.path.isdir(font_dir) else None,
        "calibre": _tool_version(["ebook-convert"])
        if shutil.which("ebook-convert") else None,
        "kepubify": _tool_version(["kepubify"])
        if shutil.which("kepubify") else None,
    }
    return json.dumps(meta, sort_keys=True)


def _cache_sidecar(output_file):
    return f"{output_file}.cbccache"


def cache_is_fresh(output_file, key):
    """True if a prior identical conversion's output is still on disk."""
    if not os.path.isfile(output_file):
        return False
    try:
        with open(_cache_sidecar(output_file), "r", encoding="utf-8") as fh:
            return fh.read() == key
    except OSError:
        return False


def write_cache(output_file, key):
    try:
        with open(_cache_sidecar(output_file), "w", encoding="utf-8") as fh:
            fh.write(key)
    except OSError:
        pass  # caching is best-effort; never fail a good conversion over it


def require_kepubify():
    """Exit loudly unless a kepubify new enough for our contract exists."""
    if shutil.which("kepubify") is None:
        print(
            "Missing dependency: kepubify. A plain epub of this markup "
            "freezes Kobo devices.\nInstall it and re-run: brew install kepubify"
        )
        sys.exit(1)
    version_line = _tool_version(["kepubify"])
    major = _kepubify_major(version_line)
    if major is not None and major < MIN_KEPUBIFY_MAJOR:
        print(
            f"kepubify '{version_line}' is too old. This tool relies on the "
            f"-o <file> output contract verified on {MIN_KEPUBIFY_MAJOR}.x. "
            "Upgrade and re-run: brew upgrade kepubify"
        )
        sys.exit(1)


def require_calibre():
    """Exit loudly if calibre's ebook-convert is unavailable."""
    if shutil.which("ebook-convert") is None:
        print(
            "Missing dependency: calibre's ebook-convert. "
            "Install calibre and re-run: brew install --cask calibre"
        )
        sys.exit(1)


def _run_ebook_convert(args, what):
    """Run ebook-convert, failing loudly instead of a raw traceback."""
    try:
        subprocess.run(["ebook-convert", *args], check=True)
    except (subprocess.CalledProcessError, OSError) as error:
        code = getattr(error, "returncode", "n/a")
        print(
            f"ebook-convert failed ({code}) while {what}. The source may be "
            "corrupt or DRM'd; no output was written."
        )
        sys.exit(1)


def _atomic_replace_guarded(tmp_path, final_path):
    """os.replace(tmp -> final), refusing to clobber through a symlink."""
    if os.path.islink(final_path):
        if os.path.lexists(tmp_path):
            os.unlink(tmp_path)
        print(f"Refusing to write through symlink: "
              f"'{os.path.basename(final_path)}'.")
        sys.exit(1)
    os.replace(tmp_path, final_path)


def process_epub_to_kepub(input_file, output_file, font_path=None, font_dir=None):
    """epub -> Kobo .kepub.epub, kepubify BEFORE bionic (issue #28).

    Kepubify on un-fragmented text emits ~1 koboSpan per sentence. Doing
    it before the bionic word-splitting (instead of after) collapses the
    koboSpan count ~20x, so a whole chapter is light enough to render fast
    as ONE spine document -- no chunker, so no mid-scene breaks, no TOC
    off-by-one, and Kobo's per-chapter stats are correct.
    """
    require_calibre()
    require_kepubify()
    safe_input = cli_safe_path(input_file)  # issue #22 (CWE-88)

    with tempfile.TemporaryDirectory() as tmpdir:
        plain_epub = os.path.join(tmpdir, "plain.epub")
        _run_ebook_convert([safe_input, plain_epub], "normalizing the source")

        kepub = os.path.join(tmpdir, "book.kepub.epub")
        try:
            subprocess.run(["kepubify", "-o", kepub, plain_epub], check=True)
        except subprocess.CalledProcessError as error:
            print(
                f"kepubify failed (exit {error.returncode}) on "
                f"'{os.path.basename(safe_input)}'. No output was written."
            )
            sys.exit(1)
        if not os.path.isfile(kepub):
            print("kepubify did not produce the expected output.")
            sys.exit(1)

        work = os.path.join(tmpdir, "work")
        os.makedirs(work)
        with zipfile.ZipFile(kepub, "r") as zf:
            safe_extract_zip(zf, work)

        font_faces, fonts_dir = _collect_font_faces(work, font_path, font_dir)
        transform_html_tree(work, font_faces, fonts_dir)

        out_final = os.path.abspath(output_file)
        out_tmp = os.path.join(
            os.path.dirname(out_final) or ".",
            f".{os.path.basename(out_final)}.part")
        if os.path.lexists(out_tmp):
            os.unlink(out_tmp)
        rezip_epub(work, out_tmp)
        _atomic_replace_guarded(out_tmp, out_final)


def process_epub_generic(input_file, output_file, font_path=None, font_dir=None):
    """epub -> bionic .epub for NON-Kobo readers (issue #19 generic).

    Same as the Kobo path but WITHOUT kepubify: no koboSpans (inert dead
    weight off Kobo) and a normal .epub for Kindle/Boox/Apple Books etc.
    """
    require_calibre()
    safe_input = cli_safe_path(input_file)  # issue #22 (CWE-88)

    with tempfile.TemporaryDirectory() as tmpdir:
        plain_epub = os.path.join(tmpdir, "plain.epub")
        _run_ebook_convert([safe_input, plain_epub], "normalizing the source")

        work = os.path.join(tmpdir, "work")
        os.makedirs(work)
        with zipfile.ZipFile(plain_epub, "r") as zf:
            safe_extract_zip(zf, work)

        font_faces, fonts_dir = _collect_font_faces(work, font_path, font_dir)
        transform_html_tree(work, font_faces, fonts_dir)

        out_final = os.path.abspath(output_file)
        out_tmp = os.path.join(
            os.path.dirname(out_final) or ".",
            f".{os.path.basename(out_final)}.part")
        if os.path.lexists(out_tmp):
            os.unlink(out_tmp)
        rezip_epub(work, out_tmp)
        _atomic_replace_guarded(out_tmp, out_final)


def process_htmlz(input_file, output_file, original_format, font_path=None, font_dir=None):
    """Non-kepub formats (.mobi/.azw3): bionic via a calibre HTMLZ round
    trip, same output format. No chunker (it was Kobo/kepub-specific) and
    no kepubify (these formats do not go through Kobo's kepub renderer).
    """
    require_calibre()
    safe_input = cli_safe_path(input_file)  # issue #22 (CWE-88)
    with tempfile.TemporaryDirectory() as tmpdir:
        htmlz_file = os.path.join(tmpdir, "book.htmlz")
        _run_ebook_convert([safe_input, htmlz_file], "converting to HTMLZ")

        work = os.path.join(tmpdir, "work")
        os.makedirs(work)
        with zipfile.ZipFile(htmlz_file, "r") as zip_ref:
            safe_extract_zip(zip_ref, work)

        font_faces, fonts_dir = _collect_font_faces(work, font_path, font_dir)
        transform_html_tree(work, font_faces, fonts_dir)

        with zipfile.ZipFile(htmlz_file, "w", zipfile.ZIP_DEFLATED) as zip_ref:
            for folder, _subs, filenames in os.walk(work):
                for filename in filenames:
                    path = os.path.join(folder, filename)
                    zip_ref.write(path, os.path.relpath(path, work))

        out_final = os.path.abspath(output_file)
        out_dir = os.path.dirname(out_final) or "."
        ext = os.path.splitext(out_final)[1]
        out_tmp = os.path.join(out_dir, f".{os.path.basename(out_final)}.part{ext}")
        if os.path.lexists(out_tmp):
            os.unlink(out_tmp)
        _run_ebook_convert(
            [htmlz_file, out_tmp, "--output-profile=default"],
            "writing the output ebook")
        _atomic_replace_guarded(out_tmp, out_final)


def main():
    parser = argparse.ArgumentParser(description='Apply Bionic Reading formatting to ebooks')
    parser.add_argument('input_file', help='Path to the ebook file to process')
    parser.add_argument('--font', help='Path to custom font file to embed', default=None)
    parser.add_argument('--font-dir', help='Path to directory of font family files to embed', default=None)
    parser.add_argument('--target', choices=['kobo', 'generic'], default='kobo',
                        help="kobo: .kepub.epub for Kobo (default). generic: "
                             "plain .epub for Kindle/Boox/Apple Books (no "
                             "kepubify). The interactive 'mainly for Kobo?' "
                             "ask + remembered preference lives in main.py; "
                             "this core stays non-interactive (issue #19).")

    args = parser.parse_args()

    # Absolutise + reject flag-shaped names before anything reaches a
    # subprocess (issue #22, CWE-88).
    try:
        input_file = cli_safe_path(args.input_file)
    except ValueError as err:
        print(err)
        sys.exit(1)
    font_path = args.font
    font_dir = args.font_dir

    if os.path.islink(input_file):
        print("Refusing to read through a symlink.")
        sys.exit(1)
    if not os.path.isfile(input_file):
        print("File not found.")
        sys.exit(1)

    file_name, file_ext = os.path.splitext(input_file)
    file_ext = file_ext.lower()
    supported_formats = [".epub", ".mobi", ".azw3"]
    if file_ext not in supported_formats:
        print("Supported input formats are .epub, .mobi, and .azw3.")
        sys.exit(1)

    log_tool_versions()

    if file_ext == ".epub" and args.target == "kobo":
        output_file = f"{file_name}_fastread.kepub.epub"
        run = lambda: process_epub_to_kepub(  # noqa: E731
            input_file, output_file, font_path=font_path, font_dir=font_dir)
    elif file_ext == ".epub":
        output_file = f"{file_name}_fastread.epub"  # generic, no kepubify (#19)
        run = lambda: process_epub_generic(  # noqa: E731
            input_file, output_file, font_path=font_path, font_dir=font_dir)
    else:
        output_file = f"{file_name}_fastread{file_ext}"
        run = lambda: process_htmlz(  # noqa: E731
            input_file, output_file, file_ext[1:],
            font_path=font_path, font_dir=font_dir)

    # Input caching (#20): skip re-converting an unchanged input under the
    # same tools/settings -- conversion is minutes; this is the felt cost.
    key = _conversion_cache_key(input_file, args.target, font_path, font_dir)
    if cache_is_fresh(output_file, key):
        print(f"Up to date, skipping: {output_file} "
              "(delete it or its .cbccache to force re-conversion)")
        return

    run()
    write_cache(output_file, key)
    print(f"Processed file saved as {output_file}")


if __name__ == "__main__":
    main()
