import os
import subprocess
import time
from itertools import cycle
from threading import Thread
from dotenv import load_dotenv

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
        supported_formats = ['epub', 'mobi', 'pdf', 'azw3', 'fb2']  # Default formats

    ebook_paths = []

    print("Scanning your Calibre library...")
    for root, dirs, files in os.walk(calibre_library_path):
        for file in files:
            if file.split('.')[-1].lower() in supported_formats:
                ebook_paths.append(os.path.join(root, file))

    return ebook_paths

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
            # Replace if new format is preferred
            if file_ext == f'.{preferred_format}':
                books_by_title[base_name] = book_path

    return list(books_by_title.values())

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

def apply_bionic_reading(ebook_paths, bionic_script_name="bionic_reader.py"):
    """
    Applies Bionic Reading typography to each ebook using the script from the repository.

    Parameters:
        ebook_paths (list): List of ebook file paths to process.
        bionic_script_name (str): Name of the script in the current repository that applies Bionic Reading.

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
                command = ["python", bionic_script_name, ebook_path]
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

        # Step 2: Remove duplicate formats (optional)
        dedup_choice = input("\nRemove duplicate formats (keep one per book, prefer epub)? (y/n): ").strip().lower()
        if dedup_choice == 'y':
            original_count = len(ebook_paths)
            ebook_paths = deduplicate_by_format(ebook_paths, preferred_format='epub')
            dedup_count = original_count - len(ebook_paths)
            print(f"Removed {dedup_count} duplicate(s). {len(ebook_paths)} unique book(s) remaining.")

        # Step 3: Ask the user which books to convert
        selected_books = prompt_user_selection(ebook_paths)

        if not selected_books:
            print("No books selected for conversion. Exiting.")
        else:
            print(f"\nYou selected {len(selected_books)} book(s) for conversion.")

            # Step 3: Apply Bionic Reading to the selected books
            apply_bionic_reading(selected_books, bionic_script_name)
