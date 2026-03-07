from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List


def _safe_tag(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.replace(" ", "-")
    return "".join(ch for ch in s if ch.isalnum() or ch in "-_").strip("-_")


def export_tag_s_matching_pairs(
    *,
    tag: str,
    vocab: List[Dict[str, Any]],
    out_dir: Path,
    game_id: str = "matching_pairs_v1",
    title: str = "",
) -> Path:
    """
    Exports a tag_s game folder:
      out_dir/<tag>/<game_id>/
        index.html
        runtime.js
        styles.css
        renderers/matching_pairs.js
        game.json

    The first renderer is EN<->ZH matching pairs from vocab.
    """
    tag_norm = _safe_tag(tag)
    if not tag_norm:
        raise ValueError("tag is empty")

    game_root = Path(out_dir) / tag_norm / game_id
    game_root.mkdir(parents=True, exist_ok=True)

    # Copy runtime pack
    runtime_src = Path("templates") / "tag_runtime"
    if not runtime_src.exists():
        raise RuntimeError("Missing templates/tag_runtime/. Create runtime files first.")

    # copytree into existing folder safely
    for item in runtime_src.rglob("*"):
        rel = item.relative_to(runtime_src)
        dst = game_root / rel
        if item.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dst)

    # Build pairs from vocab
    items = []
    for i, v in enumerate(vocab):
        en = str(v.get("en") or "").strip()
        zh = str(v.get("zh") or "").strip()
        if not en or not zh:
            continue
        items.append({"id": f"p{i+1}", "a": en, "b": zh})

    if not items:
        # minimal fallback
        items = [{"id": "p1", "a": "apple", "b": "苹果"}]

    game = {
        "meta": {
            "schema": "tag_s.v1",
            "tag": tag_norm,
            "game_id": game_id,
            "title": title or f"{tag_norm} — Matching Pairs",
            "renderer": "matching_pairs",
            "variant": "en_zh",
        },
        "items": items,
    }

    (game_root / "game.json").write_text(json.dumps(game, ensure_ascii=False, indent=2), encoding="utf-8")
    return game_root