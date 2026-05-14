import os
import re
import subprocess
import sys
import time
from difflib import SequenceMatcher
from itertools import cycle
from threading import Thread

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    print(
        "Missing dependency: python-dotenv. Run "
        "'source venv/bin/activate && python -m pip install -r requirements.txt' "
        "from this repository, then run 'python main.py'."
    )
    sys.exit(1)

# Load environment variables from .env file
load_dotenv()

def find_ebooks_in_calibre_library(calibre_library_path, supported_formats=None):
    """
    Scans the Calibre library directory and retrieves all ebook files.

    Parameters:
        calibre_library_path (str): Path to the Calibre library folder.
        supported_formats (list, optional): List of supported file extensions to include (e.g., ['epub', 'mobi', 'pdf']).

    Returns:
        list: List of full file paths to the ebooks found.
    """
    if supported_formats is None:
        supported_formats = ['epub', 'mobi', 'azw3']  # Only formats supported by apply_bioread.py

    ebook_paths = []

    print("Scanning your Calibre library...")
    for root, dirs, files in os.walk(calibre_library_path):
        for file in files:
            if file.split('.')[-1].lower() in supported_formats:
                ebook_paths.append(os.path.join(root, file))

    return ebook_paths

def exclude_converted_books(ebook_paths):
    """
    Excludes books that have already been converted (contain '_fastread' or 'Fast Font' in filename).

    Parameters:
        ebook_paths (list): List of ebook file paths.

    Returns:
        list: List of ebook paths excluding already-converted books.
    """
    return [path for path in ebook_paths
            if '_fastread' not in os.path.basename(path).lower()
            and 'fast font' not in os.path.basename(path).lower()]

def deduplicate_by_format(ebook_paths, preferred_format='epub'):
    """
    Removes duplicate books, keeping only one format per book.
    Prefers the specified format if available.

    Parameters:
        ebook_paths (list): List of ebook file paths.
        preferred_format (str): Preferred format to keep (default: 'epub').

    Returns:
        list: List of ebook paths with duplicates removed.
    """
    books_by_title = {}

    for book_path in ebook_paths:
        book_name = os.path.basename(book_path)
        # Remove extension and _fastread suffix to get base title
        base_name = os.path.splitext(book_name)[0]
        base_name = base_name.replace('_fastread', '').replace(' - Fast Font', '')
        file_ext = os.path.splitext(book_name)[1].lower()

        if base_name not in books_by_title:
            books_by_title[base_name] = book_path
        else:
            # If we already have this book, prefer the preferred format
            current_ext = os.path.splitext(books_by_title[base_name])[1].lower()
            # Replace if new format is preferred, or if current format is not preferred and new one comes first
            if file_ext == f'.{preferred_format}':
                books_by_title[base_name] = book_path

    return list(books_by_title.values())

def normalize_title_text(text):
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()

def title_matches_search(book_name, search_term):
    normalized_title = normalize_title_text(os.path.splitext(book_name)[0])
    normalized_search = normalize_title_text(search_term)

    if not normalized_search:
        return False
    if normalized_search in normalized_title:
        return True

    search_words = normalized_search.split()
    title_words = normalized_title.split()

    # Match searches like "washington burning" against titles that contain both words.
    for search_word in search_words:
        if not any(
            search_word == title_word or SequenceMatcher(None, search_word, title_word).ratio() >= 0.84
            for title_word in title_words
        ):
            return False

    return True

def filter_by_title(ebook_paths, search_term):
    """
    Filters ebooks by title using a case-insensitive search.

    Parameters:
        ebook_paths (list): List of ebook file paths.
        search_term (str): Search term to filter titles.

    Returns:
        list: List of ebook paths that match the search term.
    """
    return [
        book_path
        for book_path in ebook_paths
        if title_matches_search(os.path.basename(book_path), search_term)
    ]

def parse_number_selection(selection, max_number):
    selected_indexes = []

    for part in selection.split(","):
        part = part.strip()
        if not part:
            continue

        if not part.isdigit():
            return None

        selected_number = int(part)
        if selected_number < 1 or selected_number > max_number:
            return None

        selected_index = selected_number - 1
        if selected_index not in selected_indexes:
            selected_indexes.append(selected_index)

    return selected_indexes

def prompt_title_filter(ebook_paths):
    while True:
        search_term = input("Enter search term for book title (or press Enter to cancel): ").strip()

        if not search_term:
            print("Title search cancelled.")
            return []

        filtered_books = filter_by_title(ebook_paths, search_term)

        if not filtered_books:
            print(f"No books found matching '{search_term}'. Try another title.")
            continue

        print(f"\nThe following titles match '{search_term}':")
        for index, book_path in enumerate(filtered_books, 1):
            print(f"{index}. {os.path.basename(book_path)}")

        while True:
            selection = input(
                "\nSelect number(s) to convert, separated by commas; "
                "enter 'a' for all, 's' to search again, or press Enter to cancel: "
            ).strip().lower()

            if not selection:
                print("Title search cancelled.")
                return []

            if selection == 's':
                break

            if selection == 'a':
                return filtered_books

            selected_indexes = parse_number_selection(selection, len(filtered_books))
            if selected_indexes is not None and selected_indexes:
                return [filtered_books[index] for index in selected_indexes]

            print(f"Please enter a number between 1 and {len(filtered_books)}, comma-separated numbers, 'a', or 's'.")

def prompt_user_selection(ebook_paths):
    """
    Interactively asks the user whether to include each book for conversion.

    Parameters:
        ebook_paths (list): List of ebook file paths.

    Returns:
        list: List of ebook paths selected by the user.
    """
    selected_books = []

    print("\nPlease decide if you want each book converted:\n")
    for book_path in ebook_paths:
        book_name = os.path.basename(book_path)
        user_input = input(f"Would you like to convert '{book_name}'? (y/n): ").strip().lower()
        if user_input == 'y':
            selected_books.append(book_path)

    return selected_books

class LoadingAnimation:
    """
    A simple loading spinner class for indicating progress.
    """
    def __init__(self, message="Processing..."):
        self.message = message
        self.done = False

    def start(self):
        spinner = cycle(["|", "/", "-", "\\"])
        print(f"{self.message} ", end="", flush=True)
        while not self.done:
            print(next(spinner), end="\r", flush=True)
            time.sleep(0.1)

    def stop(self):
        self.done = True
        print(" " * len(self.message), end="\r", flush=True)

def list_available_fonts(fonts_dir="fonts"):
    """
    Lists all available font files in the fonts directory.

    Parameters:
        fonts_dir (str): Path to the fonts directory.

    Returns:
        list: List of font file paths, or empty list if no fonts found.
    """
    if not os.path.exists(fonts_dir):
        return []

    font_extensions = ['.ttf', '.otf', '.woff', '.woff2']
    fonts = []

    for file in os.listdir(fonts_dir):
        if any(file.lower().endswith(ext) for ext in font_extensions):
            fonts.append(os.path.join(fonts_dir, file))

    return sorted(fonts)

def infer_font_face(filename):
    name = os.path.splitext(filename)[0].lower().replace("-", "_").replace(" ", "_")
    is_bold = "bold" in name
    is_italic = "italic" in name or "oblique" in name

    if is_bold and is_italic:
        return "bold italic"
    if is_bold:
        return "bold"
    if is_italic:
        return "italic"
    return "regular"

def select_font_faces(fonts_dir="fonts"):
    """
    Selects one font file for each available face, ignoring duplicate file formats.

    Returns:
        list: Font file paths selected for embedding.
    """
    extension_rank = {'.woff2': 0, '.otf': 1, '.ttf': 2, '.woff': 3}
    selected = {}

    for font_path in list_available_fonts(fonts_dir):
        filename = os.path.basename(font_path)
        ext = os.path.splitext(filename)[1].lower()
        face = infer_font_face(filename)
        current = selected.get(face)
        if current is None or extension_rank.get(ext, 99) < extension_rank.get(os.path.splitext(current)[1].lower(), 99):
            selected[face] = font_path

    return [selected[face] for face in ["regular", "bold", "italic", "bold italic"] if face in selected]

def select_font_directory(fonts_dir="fonts"):
    """
    Prompts user to use all available fonts from the fonts directory.

    Returns:
        str or None: Path to fonts directory, or None if default formatting should be used.
    """
    fonts = list_available_fonts(fonts_dir)
    selected_fonts = select_font_faces(fonts_dir)

    if not fonts:
        print("\nNo fonts found in 'fonts/' directory.")
        use_default = input("Continue with default bold formatting? (y/n): ").strip().lower()
        if use_default != 'y':
            print("Exiting. Please add font files to the 'fonts/' directory.")
            exit(0)
        return None

    print(f"\nFound {len(fonts)} font file(s) in 'fonts/'.")
    print(f"The converter will embed {len(selected_fonts)} font face(s):")
    for font_path in selected_fonts:
        print(f"- {infer_font_face(os.path.basename(font_path))}: {os.path.basename(font_path)}")

    use_fonts = input("Embed these fonts as a family for converted books? (y/n): ").strip().lower()
    if use_fonts == 'y':
        return fonts_dir

    print("Using default bold formatting")
    return None

def apply_bionic_reading(ebook_paths, bionic_script_name="bionic_reader.py", font_dir=None):
    """
    Applies Bionic Reading typography to each ebook using the script from the repository.

    Parameters:
        ebook_paths (list): List of ebook file paths to process.
        bionic_script_name (str): Name of the script in the current repository that applies Bionic Reading.
        font_dir (str, optional): Path to custom font directory to embed.

    Returns:
        None
    """
    loader = LoadingAnimation("Converting your books now")
    loader_thread = Thread(target=loader.start)
    loader_thread.start()

    try:
        for ebook_path in ebook_paths:
            try:
                # Call the Bionic Reading script with the current ebook as input
                command = [sys.executable, bionic_script_name, ebook_path]
                if font_dir:
                    command.extend(["--font-dir", font_dir])
                subprocess.run(command, check=True)  # Runs the script and checks for errors
            except subprocess.CalledProcessError as e:
                print(f"Error processing {ebook_path}: {e}")
    finally:
        loader.stop()
        loader_thread.join()

    print("Conversion completed!")

if __name__ == "__main__":
    # Load Calibre library path from .env
    calibre_library_path = os.getenv("CALIBRE_LIBRARY_PATH")

    if not calibre_library_path:
        print("Error: CALIBRE_LIBRARY_PATH environment variable is not set in your .env file.")
        exit(1)

    # Script name for Bionic Reading (must exist in the same directory as this script)
    bionic_script_name = "apply_bioread.py"

    # Step 1: Find all ebooks in the Calibre library
    ebook_paths = find_ebooks_in_calibre_library(calibre_library_path)

    if not ebook_paths:
        print("No ebooks found in the specified Calibre library.")
    else:
        print(f"\nFound {len(ebook_paths)} ebooks in your library.")

        # Step 2: Exclude already-converted books (optional)
        exclude_choice = input("\nExclude already-converted books (with '_fastread' or 'Fast Font')? (y/n): ").strip().lower()
        if exclude_choice == 'y':
            original_count = len(ebook_paths)
            ebook_paths = exclude_converted_books(ebook_paths)
            excluded_count = original_count - len(ebook_paths)
            print(f"Excluded {excluded_count} already-converted book(s). {len(ebook_paths)} remaining.")

        # Step 3: Remove duplicate formats (optional)
        dedup_choice = input("\nRemove duplicate formats (keep one per book, prefer epub)? (y/n): ").strip().lower()
        if dedup_choice == 'y':
            original_count = len(ebook_paths)
            ebook_paths = deduplicate_by_format(ebook_paths, preferred_format='epub')
            dedup_count = original_count - len(ebook_paths)
            print(f"Removed {dedup_count} duplicate(s). {len(ebook_paths)} unique book(s) remaining.")

        # Step 4: Filter by title (optional)
        filter_choice = input("\nWould you like to filter by title? (y/n): ").strip().lower()

        if filter_choice == 'y':
            selected_books = prompt_title_filter(ebook_paths)
        else:
            # Step 5: Ask the user which books to convert
            selected_books = prompt_user_selection(ebook_paths)

        if not selected_books:
            print("No books selected for conversion. Exiting.")
        else:
            print(f"\nYou selected {len(selected_books)} book(s) for conversion.")

            # Step 6: Select font handling for conversion
            selected_font_dir = select_font_directory()

            # Step 7: Apply Bionic Reading to the selected books
            apply_bionic_reading(selected_books, bionic_script_name, font_dir=selected_font_dir)
