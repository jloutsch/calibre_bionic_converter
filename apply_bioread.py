import sys
import os
import re
import zipfile
import tempfile
import subprocess
import shutil
import argparse

try:
    from bs4 import BeautifulSoup
except ModuleNotFoundError:
    print(
        "Missing dependency: beautifulsoup4. Run "
        "'source venv/bin/activate && python -m pip install -r requirements.txt' "
        "from this repository, then run 'python main.py'."
    )
    sys.exit(1)


def apply_bionic_reading_to_node(text_node, soup):
    word_pattern = re.compile(r"\b\w+\b", re.UNICODE)

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

        # Create a <strong> tag using the soup object
        bold_tag = soup.new_tag("strong")
        bold_tag.string = bold_part
        new_contents.append(bold_tag)

        # Append the normal part as text
        new_contents.append(normal_part)

        last_end = end

    if last_end < len(text):
        new_contents.append(text[last_end:])

    # Replace the original text node with the new contents
    text_node.replace_with(*new_contents)


def remove_line_height_declarations(css_text):
    return re.sub(r"(?i)\s*line-height\s*:\s*[^;}\"]+;?", "", css_text)


def remove_fixed_line_spacing(soup):
    for tag in soup.find_all(style=True):
        cleaned_style = remove_line_height_declarations(tag["style"]).strip()
        if cleaned_style:
            tag["style"] = cleaned_style
        else:
            del tag["style"]

    for style_tag in soup.find_all("style"):
        if style_tag.string:
            style_tag.string.replace_with(remove_line_height_declarations(style_tag.string))


def remove_fixed_line_spacing_from_css_file(css_path):
    with open(css_path, "r", encoding="utf-8") as f:
        css = f.read()

    cleaned_css = remove_line_height_declarations(css)
    if cleaned_css != css:
        with open(css_path, "w", encoding="utf-8") as f:
            f.write(cleaned_css)


def safe_extract_zip(zip_file, destination):
    destination_path = os.path.realpath(destination)

    for member in zip_file.infolist():
        member_path = os.path.realpath(os.path.join(destination, member.filename))
        if member_path != destination_path and not member_path.startswith(destination_path + os.sep):
            raise ValueError(f"Unsafe path in archive: {member.filename}")

    zip_file.extractall(destination)


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


def process_htmlz(input_file, output_file, original_format, font_path=None, font_dir=None):
    with tempfile.TemporaryDirectory() as tmpdir:
        # Convert ebook to HTMLZ format
        htmlz_file = os.path.join(tmpdir, "book.htmlz")
        cmd_convert_to_htmlz = ["ebook-convert", input_file, htmlz_file]
        subprocess.run(cmd_convert_to_htmlz, check=True)

        # Extract HTMLZ contents
        with zipfile.ZipFile(htmlz_file, "r") as zip_ref:
            safe_extract_zip(zip_ref, tmpdir)

        font_faces = []
        fonts_tmpdir = os.path.join(tmpdir, "fonts")

        # Copy a single font file if provided for backward-compatible CLI usage.
        if font_path and os.path.exists(font_path):
            os.makedirs(fonts_tmpdir, exist_ok=True)
            font_filename = os.path.basename(font_path)
            weight, style = infer_font_face(font_filename)
            safe_filename = safe_font_filename(1, font_filename)
            font_dest = os.path.join(fonts_tmpdir, safe_filename)
            shutil.copy(font_path, font_dest)
            font_faces.append({
                "filename": font_filename,
                "safe_filename": safe_filename,
                "weight": weight,
                "style": style,
            })

        # Copy all matching font faces if a directory is provided.
        for face in list_font_faces(font_dir):
            os.makedirs(fonts_tmpdir, exist_ok=True)
            shutil.copy(face["source_path"], os.path.join(fonts_tmpdir, face["safe_filename"]))
            font_faces.append({
                "filename": face["filename"],
                "safe_filename": face["safe_filename"],
                "weight": face["weight"],
                "style": face["style"],
            })

        # Apply Bionic Reading effect to all HTML files
        for root, dirs, files in os.walk(tmpdir):
            for file in files:
                file_lower = file.lower()
                if file_lower.endswith(".css"):
                    remove_fixed_line_spacing_from_css_file(os.path.join(root, file))
                elif file_lower.endswith((".html", ".htm", ".xhtml")):
                    html_path = os.path.join(root, file)
                    with open(html_path, "r", encoding="utf-8") as f:
                        soup = BeautifulSoup(f, "html.parser")

                    remove_fixed_line_spacing(soup)

                    # Add custom font CSS if fonts are provided
                    if font_faces:
                        font_url_prefix = os.path.relpath(fonts_tmpdir, os.path.dirname(html_path)).replace(os.sep, "/")
                        font_css = build_font_css(font_faces, font_url_prefix=font_url_prefix)
                        style_tag = soup.new_tag("style")
                        style_tag.string = font_css
                        if soup.head:
                            soup.head.append(style_tag)
                        else:
                            head = soup.new_tag("head")
                            head.append(style_tag)
                            if soup.html:
                                soup.html.insert(0, head)

                    # Process text nodes
                    for text_node in soup.find_all(string=True):
                        parent = text_node.parent.name if text_node.parent else None
                        if parent in ["style", "script", "[document]", "head", "title"]:
                            continue
                        if text_node.strip():
                            apply_bionic_reading_to_node(text_node, soup)
                    # Save modified HTML
                    with open(html_path, "w", encoding="utf-8") as f:
                        f.write(str(soup))

        # Rezip contents into HTMLZ
        with zipfile.ZipFile(htmlz_file, "w") as zip_ref:
            for folder_name, subfolders, filenames in os.walk(tmpdir):
                for filename in filenames:
                    file_path = os.path.join(folder_name, filename)
                    # Avoid including the HTMLZ file itself
                    if file_path == htmlz_file:
                        continue
                    arcname = os.path.relpath(file_path, tmpdir)
                    zip_ref.write(file_path, arcname=arcname)

        # Convert HTMLZ back to the original format
        cmd_convert_back = [
            "ebook-convert",
            htmlz_file,
            output_file,
            "--output-profile=default",
        ]
        subprocess.run(cmd_convert_back, check=True)


def convert_to_kepub(epub_path):
    """
    Converts a plain ``.epub`` to Kobo's ``.kepub.epub`` format using kepubify.

    Plain ``application/epub+zip`` files are rendered on Kobo devices by the
    legacy Adobe RMSDK engine, which freezes on the heavy calibre + bionic
    markup this tool produces (observed: nickel UI thread hangs, the sickel
    watchdog kills and restarts the reader). Kobo's ``.kepub.epub`` files use
    the modern HTML renderer instead, which handles this markup without
    freezing. We wrap the external ``kepubify`` binary here so the rest of the
    pipeline never calls it directly.

    On success the intermediate plain epub is removed so the fragile format is
    never delivered to the device.

    Parameters:
        epub_path (str): Path to the plain ``.epub`` produced by the pipeline.

    Returns:
        str: Path to the produced ``.kepub.epub`` file.
    """
    if shutil.which("kepubify") is None:
        print(
            "Missing dependency: kepubify. The plain-epub output left at "
            f"'{epub_path}' freezes Kobo devices and must NOT be sideloaded.\n"
            "Install it and re-run: brew install kepubify"
        )
        sys.exit(1)

    base, _ = os.path.splitext(epub_path)
    kepub_path = f"{base}.kepub.epub"
    subprocess.run(["kepubify", "-o", kepub_path, epub_path], check=True)

    if not os.path.isfile(kepub_path):
        print(f"kepubify did not produce expected output at '{kepub_path}'.")
        sys.exit(1)

    # Drop the fragile intermediate so it can never reach the device.
    os.remove(epub_path)
    return kepub_path


def main():
    parser = argparse.ArgumentParser(description='Apply Bionic Reading formatting to ebooks')
    parser.add_argument('input_file', help='Path to the ebook file to process')
    parser.add_argument('--font', help='Path to custom font file to embed', default=None)
    parser.add_argument('--font-dir', help='Path to directory of font family files to embed', default=None)

    args = parser.parse_args()

    input_file = args.input_file
    font_path = args.font
    font_dir = args.font_dir

    if not os.path.isfile(input_file):
        print("File not found.")
        sys.exit(1)

    file_name, file_ext = os.path.splitext(input_file)
    supported_formats = [".epub", ".mobi", ".azw3"]
    if file_ext.lower() not in supported_formats:
        print("Supported input formats are .epub, .mobi, and .azw3.")
        sys.exit(1)

    output_file = f"{file_name}_fastread{file_ext}"
    process_htmlz(input_file, output_file, file_ext.lower()[1:], font_path=font_path, font_dir=font_dir)

    # Plain .epub freezes Kobo's legacy renderer; ship .kepub.epub instead.
    if file_ext.lower() == ".epub":
        output_file = convert_to_kepub(output_file)

    print(f"Processed file saved as {output_file}")


if __name__ == "__main__":
    main()
