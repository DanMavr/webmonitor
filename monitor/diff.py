"""
diff.py — compare two OCR text strings and return the meaningful delta.
"""

import difflib


def compute_diff(old_text: str, new_text: str) -> dict:
    """
    Compare old_text and new_text line by line.

    Returns:
        {
          "changed": bool,
          "added":   ["line1", "line2", ...],
          "removed": ["line3", ...],
          "summary": "2 added, 1 removed"
        }
    """
    old_lines = [l.strip() for l in old_text.splitlines() if l.strip()]
    new_lines = [l.strip() for l in new_text.splitlines() if l.strip()]

    added   = [l for l in new_lines if l not in set(old_lines)]
    removed = [l for l in old_lines if l not in set(new_lines)]

    changed = bool(added or removed)
    parts = []
    if added:
        parts.append(f"{len(added)} added")
    if removed:
        parts.append(f"{len(removed)} removed")

    return {
        "changed": changed,
        "added":   added,
        "removed": removed,
        "summary": ", ".join(parts) if parts else "no change",
    }
