from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List


def _safe_tag(s: str) -> str:
    s = (s or "").strip().lower().replace(" ", "-")
    return "".join(ch for ch in s if ch.isalnum() or ch in "-_").strip("-_")


def export_tag_s_matching_pairs(
    *,
    tag: str,
    vocab: List[Dict[str, Any]],
    out_dir: Path,
    game_id: str = "matching_pairs_v1",
    title: str = "",
) -> Path:
    tag_norm = _safe_tag(tag)
    if not tag_norm:
        raise ValueError("tag is empty")

    game_root = Path(out_dir) / tag_norm / game_id
    game_root.mkdir(parents=True, exist_ok=True)

    project_root = Path(__file__).resolve().parent.parent
    runtime_src = project_root / "templates" / "tag_runtime"
    if not runtime_src.exists():
        raise RuntimeError(f"Missing runtime template folder: {runtime_src}")

    for item in runtime_src.rglob("*"):
        rel = item.relative_to(runtime_src)
        if rel.name == "styes.css":
            rel = rel.with_name("styles.css")
        dst = game_root / rel
        if item.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dst)

    items = []
    for i, v in enumerate(vocab):
        en = str(v.get("en") or "").strip()
        zh = str(v.get("zh") or "").strip()
        if not en or not zh:
            continue
        items.append({"id": f"p{i+1}", "a": en, "b": zh})

    if not items:
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
