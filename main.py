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

def filter_by_title(ebook_paths, search_term):
    """
    Filters ebooks by title using a case-insensitive search.

    Parameters:
        ebook_paths (list): List of ebook file paths.
        search_term (str): Search term to filter titles.

    Returns:
        list: List of ebook paths that match the search term.
    """
    filtered_books = []
    search_lower = search_term.lower()

    for book_path in ebook_paths:
        book_name = os.path.basename(book_path)
        if search_lower in book_name.lower():
            filtered_books.append(book_path)

    return filtered_books

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

        # Step 2: Filter by title (optional)
        filter_choice = input("\nWould you like to filter by title? (y/n): ").strip().lower()

        if filter_choice == 'y':
            search_term = input("Enter search term for book title: ").strip()
            ebook_paths = filter_by_title(ebook_paths, search_term)

            if not ebook_paths:
                print(f"No books found matching '{search_term}'. Exiting.")
                exit(0)

            print(f"\nFound {len(ebook_paths)} book(s) matching '{search_term}'.")

            # Ask if user wants to convert all matching books
            convert_all = input("Convert all matching books? (y/n): ").strip().lower()
            if convert_all == 'y':
                selected_books = ebook_paths
            else:
                selected_books = prompt_user_selection(ebook_paths)
        else:
            # Step 3: Ask the user which books to convert
            selected_books = prompt_user_selection(ebook_paths)

        if not selected_books:
            print("No books selected for conversion. Exiting.")
        else:
            print(f"\nYou selected {len(selected_books)} book(s) for conversion.")

            # Step 4: Apply Bionic Reading to the selected books
            apply_bionic_reading(selected_books, bionic_script_name)
