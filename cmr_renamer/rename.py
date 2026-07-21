"""Filename generation and renaming logic for CMR Renamer.

Contains functions to clean extracted text, build new filenames, and perform the rename operation.
"""

import os
import re


def clean_name(text: str, max_length: int = 60, remove_leading_zeros: bool = True) -> str:
    """
    Clean extracted text for use in a filename.

    Args:
        text: Raw OCR text
        max_length: Maximum length of the cleaned string
        remove_leading_zeros: Whether to strip leading zeros from numeric strings

    Returns:
        Cleaned string suitable for filename
    """
    # Remove special characters, keep alphanumeric, spaces, dots, hyphens
    clean = re.sub(r'[^\w\s.-]', '', text).replace('\n', ' ').strip()

    # Limit length
    if len(clean) > max_length:
        clean = clean[:max_length]

    # Remove leading zeros if configured
    if remove_leading_zeros:
        clean = re.sub(r'^0+', '', clean)

    return clean


def build_new_name(text1: str, text2: str, max_length: int = 60,
                   remove_leading_zeros: bool = True) -> str:
    """
    Build a new filename base from two text components.

    Args:
        text1: First text component (e.g., document number)
        text2: Second text component (e.g., company name)
        max_length: Maximum length for each component
        remove_leading_zeros: Whether to strip leading zeros

    Returns:
        Combined filename base (without extension)
    """
    part1 = clean_name(text1, max_length, remove_leading_zeros)
    part2 = clean_name(text2, max_length, remove_leading_zeros)
    return f"{part1} {part2}".strip()


def rename_pdf(pdf_path: str, text1: str, text2: str,
               max_length: int = 60, remove_leading_zeros: bool = True) -> str:
    """
    Rename a PDF file based on extracted text.

    Args:
        pdf_path: Full path to the PDF file to rename
        text1: First text component (e.g., document number)
        text2: Second text component (e.g., company name)
        max_length: Maximum length for each filename component
        remove_leading_zeros: Whether to strip leading zeros from numbers

    Returns:
        New full path of the renamed file

    Raises:
        OSError: If rename fails
    """
    # Build base name
    base_name = build_new_name(text1, text2, max_length, remove_leading_zeros)
    new_filename = base_name + ".pdf"
    directory = os.path.dirname(pdf_path)
    new_path = os.path.join(directory, new_filename)

    # Handle filename collisions
    if not os.path.exists(new_path):
        os.rename(pdf_path, new_path)
        return new_path

    # If file exists, add a counter
    counter = 1
    while os.path.exists(new_path):
        new_filename = f"{base_name} ({counter}).pdf"
        new_path = os.path.join(directory, new_filename)
        counter += 1

    os.rename(pdf_path, new_path)
    return new_path