"""Whitelist-only sanitizer must not let `..` or any path separator survive into a
filesystem path. Run with: `python -m pytest tests/`."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main import safe_topic, safe_folder_name, OUTPUT_ROOT  # noqa: E402


@pytest.mark.parametrize("dangerous", [
    "..",
    "../../etc/passwd",
    "..\\..\\Windows\\System32",
    "/etc/passwd",
    "C:\\Windows\\System32",
    "../app.py",
    "..%2F..%2Fapp.py",
    "foo/../bar",
    ".",
    "./.env",
    "\x00",
    "  ..  ",
])
def test_traversal_inputs_are_neutralized(dangerous):
    """Whatever the user submits, `safe_topic(...)` must not contain a path
    separator, must not contain `..`, and must produce a path that resolves to
    somewhere strictly inside OUTPUT_ROOT."""
    safe = safe_topic(dangerous)
    assert ".." not in safe
    assert "/" not in safe and "\\" not in safe
    assert ":" not in safe
    assert safe == safe.lower()

    resolved = (OUTPUT_ROOT / safe).resolve()
    assert str(resolved).startswith(str(OUTPUT_ROOT.resolve()) + os.sep) \
        or resolved == OUTPUT_ROOT.resolve()


def test_empty_input_falls_back_to_placeholder():
    assert safe_topic("") == "unknown_topic"
    assert safe_topic("   ") == "unknown_topic"
    assert safe_topic("!!!") == "unknown_topic"


def test_normal_topic_is_lowercased_and_underscored():
    assert safe_topic("Black Holes") == "black_holes"
    assert safe_topic("Photosynthesis") == "photosynthesis"
    assert safe_topic("DNA & RNA") == "dna_rna"


def test_unicode_dropped_devanagari_does_not_break():
    """Devanagari isn't useful in a filesystem path on most systems; we drop it
    rather than risk encoding bugs. The function must not crash and must produce
    a valid placeholder when Unicode is the only content."""
    result = safe_topic("कृष्णविवर")  # Marathi for "black hole"
    assert result == "unknown_topic"

    # Mixed Unicode + ASCII should keep the ASCII portion.
    assert safe_topic("Black कृष्णविवर Holes") == "black_holes"


# --- safe_folder_name tests ---

def test_folder_name_combines_topic_and_genre():
    assert safe_folder_name("Black Holes", "Rap") == "black_holes_rap"
    assert safe_folder_name("Photosynthesis", "Lavani") == "photosynthesis_lavani"
    assert safe_folder_name("DNA & RNA", "Abhang") == "dna_rna_abhang"


def test_folder_name_without_genre_falls_back_to_topic():
    assert safe_folder_name("Black Holes") == "black_holes"
    assert safe_folder_name("Black Holes", None) == "black_holes"
    assert safe_folder_name("Black Holes", "") == "black_holes"


def test_folder_name_with_garbage_genre_falls_back():
    """If genre sanitizes to 'unknown_topic', don't append it."""
    assert safe_folder_name("Black Holes", "!!!") == "black_holes"
    assert safe_folder_name("Black Holes", "कृष्णविवर") == "black_holes"
