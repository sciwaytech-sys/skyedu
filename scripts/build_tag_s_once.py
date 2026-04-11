"""
scripts/build_tag_s_once.py
----------------------------
Generate tag_s matching-pairs bundles for every tag found in homework.txt.

Usage (from the project root):
    python -m scripts.build_tag_s_once
  or simply:
    python scripts/build_tag_s_once.py

The output directory is controlled by TAGS_OUT_DIR (see skyed/tag_s_env.py).
"""
from __future__ import annotations

try:
    from ._bootstrap import ensure_project_root_on_sys_path
except ImportError:  # direct script execution
    from _bootstrap import ensure_project_root_on_sys_path

PROJECT_ROOT = ensure_project_root_on_sys_path()


import re
from pathlib import Path

from dotenv import load_dotenv

from skyed.parser import parse_homework_text
from skyed.tag_gamegen import export_tag_s_matching_pairs
from skyed.tag_s_env import tag_s_output_root

load_dotenv(PROJECT_ROOT / ".env")

homework_path = PROJECT_ROOT / "homework.txt"
text = homework_path.read_text(encoding="utf-8", errors="ignore")
spec = parse_homework_text(text)

tags: list[str] = list(spec.get("tags") or [])
if not tags:
    for line in text.splitlines():
        m = re.match(r"^\s*#?\s*Tags?\s*[:：]\s*(.+)\s*$", line.strip(), flags=re.IGNORECASE)
        if m:
            tags = [x.strip() for x in re.split(r"[,，、]", m.group(1)) if x.strip()]
            break

out_root = tag_s_output_root(PROJECT_ROOT)
vocab = spec.get("vocab", []) or []

print("tags    =", tags)
print("out_root=", out_root)

for tag in tags:
    path = export_tag_s_matching_pairs(tag=tag, vocab=vocab, out_dir=out_root)
    print("exported:", path)
