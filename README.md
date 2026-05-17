# Calibre Bionic Converter

This script scans your Calibre library for eBooks, allows you to select which books to convert, and applies Bionic Reading typography to the selected books. The Bionic Reading conversion is handled by a script from the repository [arcanite24/libre-bioread](https://github.com/arcanite24/libre-bioread). Ensure that the `apply_bioread.py` script is downloaded and placed in the same directory as this script.

---

## Features
1. Scans your Calibre library to find supported eBook formats (`.epub`, `.mobi`, `.azw3`).
2. Interactively prompts you to select which books to convert.
3. Applies Bionic Reading typography to the selected books using the `apply_bioread.py` script.
4. Optionally embeds the font family from `fonts/`, using regular, bold, italic, and bold italic faces where appropriate.
5. Removes fixed `line-height` styling so ereader line spacing controls can still work.
6. Provides a visual loading spinner during the conversion process.
7. Outputs Kobo-safe `.kepub.epub` for epub inputs (via `kepubify`). Plain `.epub` output freezes Kobo's legacy renderer on this tool's markup, so the fragile intermediate is converted and removed automatically.

---

## Prerequisites

1. **Python 3.8 or higher** installed on your system.
2. **Virtual environment**: Use the project virtual environment so dependencies are installed separately from Homebrew or macOS Python:
   ```bash
   source venv/bin/activate
   ```
   If the `venv/` directory does not exist, create it first:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
3. **Dependencies**: Install required Python libraries using:
   ```bash
   python -m pip install -r requirements.txt
   ```
   **`kepubify`** (required for epub conversion): produces the Kobo-safe
   `.kepub.epub`. Without it, epub conversions fail loudly rather than ship
   the plain epub that freezes Kobo devices.
   ```bash
   brew install kepubify
   ```
4. Calibre Library Path: Ensure your .env file includes the following:
   ```bash
   CALIBRE_LIBRARY_PATH=/path/to/your/calibre/library
   ```
Replace the path with the actual location of your Calibre library.

## Usage

1. **Prepare Your Environment**:
   - Ensure Python 3.8 or higher is installed on your system.
   - Activate the local virtual environment:
     ```bash
     source venv/bin/activate
     ```
   - Set your Calibre library path in a `.env` file with the following format:
     ```
     CALIBRE_LIBRARY_PATH=/path/to/your/calibre/library
     ```
     Replace the path with the actual location of your Calibre library.
   - Install dependencies with:
     ```bash
     python -m pip install -r requirements.txt
     ```

2. **Run the Script**:
   Execute the script using:
   ```bash
   python main.py
   ```
3. Follow the prompts: 
    - The script will scan your Calibre library for eBooks and display a count of the files found.
    - If font files exist in `fonts/`, choose whether to embed them as a family. You do not need to select an individual font file.
    - Title search matches words anywhere in the title, so `washington burning` can match `Washington Is Burning`.
    - If title search finds no matches, you can enter another search instead of exiting.
    - If title search finds matches, choose matching titles by number, such as `1` or `1,3`.
    - For each book, it will ask:
   ```sql
   Would you like to convert 'example_book.epub'? (y/n):
 Select y to convert or n to skip.
 4. Conversion: 
    -The selected books will be processed, and you’ll see a loading spinner during the conversion process. Once completed, a message will confirm the conversion.

### Supported Formats
By default, the script supports the following eBook formats: `.epub`, `.mobi`, `.azw3`.

You can customize this list in the find_ebooks_in_calibre_library function by modifying the supported_formats parameter.

## Credits

This script integrates the Bionic Reading conversion functionality from [arcanite24/libre-bioread](https://github.com/arcanite24/libre-bioread). Full credit for the Bionic Reading typography application goes to the original author of that repository.
