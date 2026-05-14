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

    return list(selected.values())


def build_font_css(font_faces, font_url_prefix="fonts"):
    if not font_faces:
        return ""

    css_blocks = []
    for face in font_faces:
        font_format = font_format_for_file(face["filename"])
        css_blocks.append(f"""
@font-face {{
    font-family: 'BionicFont';
    src: url('{font_url_prefix}/{face["filename"]}') format('{font_format}');
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
            zip_ref.extractall(tmpdir)

        font_faces = []
        fonts_tmpdir = os.path.join(tmpdir, "fonts")

        # Copy a single font file if provided for backward-compatible CLI usage.
        if font_path and os.path.exists(font_path):
            os.makedirs(fonts_tmpdir, exist_ok=True)
            font_filename = os.path.basename(font_path)
            weight, style = infer_font_face(font_filename)
            font_dest = os.path.join(fonts_tmpdir, font_filename)
            shutil.copy(font_path, font_dest)
            font_faces.append({
                "filename": font_filename,
                "weight": weight,
                "style": style,
            })

        # Copy all matching font faces if a directory is provided.
        for face in list_font_faces(font_dir):
            os.makedirs(fonts_tmpdir, exist_ok=True)
            shutil.copy(face["source_path"], os.path.join(fonts_tmpdir, face["filename"]))
            font_faces.append({
                "filename": face["filename"],
                "weight": face["weight"],
                "style": face["style"],
            })

        # Apply Bionic Reading effect to all HTML files
        for root, dirs, files in os.walk(tmpdir):
            for file in files:
                if file.endswith(".html") or file.endswith(".htm"):
                    html_path = os.path.join(root, file)
                    with open(html_path, "r", encoding="utf-8") as f:
                        soup = BeautifulSoup(f, "html.parser")

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
    print(f"Processed file saved as {output_file}")


if __name__ == "__main__":
    main()
