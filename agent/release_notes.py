"""Versioned, plain-language instructions shown when an agent starts."""

import json
from pathlib import Path
from urllib.parse import unquote, urlsplit


def validate_notes(notes):
    if not isinstance(notes, dict):
        raise ValueError("Release notes must be an object.")
    for field, limit in (("summary", 300), ("instructions", 3000)):
        if not isinstance(notes.get(field), str) or not 1 <= len(notes[field].strip()) <= limit:
            raise ValueError(f"Release notes need {field} (1–{limit} characters).")
    links = notes.get("links", [])
    if not isinstance(links, list) or len(links) > 8:
        raise ValueError("Release notes support up to eight links.")
    for link in links:
        if not isinstance(link, dict) or not isinstance(link.get("label"), str) or not 1 <= len(link["label"].strip()) <= 80:
            raise ValueError("Each release link needs a label.")
        path = link.get("path")
        if not isinstance(path, str) or not 1 <= len(path) <= 500:
            raise ValueError("Each release link needs a local path.")
        decoded = unquote(path)
        if (not decoded.startswith("/") or decoded.startswith("//") or "\\" in decoded
                or any(character.isspace() or ord(character) < 32 for character in decoded)
                or urlsplit(decoded).netloc or urlsplit(decoded).scheme):
            raise ValueError("Release links must stay on the Veyra website.")
    return {"summary": notes["summary"].strip(), "instructions": notes["instructions"].strip(),
            "links": [{"label": link["label"].strip(), "path": link["path"]} for link in links]}


def load_notes(root):
    path = Path(root) / "agent/release_notes.json"
    if not path.is_file():
        return None
    if path.stat().st_size > 16000:
        raise ValueError("Release notes are too large.")
    return validate_notes(json.loads(path.read_text(encoding="utf-8")))
