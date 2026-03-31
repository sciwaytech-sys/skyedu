from __future__ import annotations

import re
from pathlib import Path


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def slugify(text: str) -> str:
    """URL/WP slug: lowercase, hyphens. Used for WordPress page slugs."""
    text = (text or "").strip().lower()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s-]+", "-", text).strip("-")
    return text or "lesson"


def slugify_file(s: str) -> str:
    """Filename slug: lowercase, underscores. Used for audio/image filenames."""
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^a-z0-9_\-]+", "", s)
    return s or "item"


def safe_filename(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "_", text).strip("_")
    return text or "file"
