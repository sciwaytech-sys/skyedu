from __future__ import annotations
import random
from typing import Any

PREP_SET_C = ["on", "in", "under", "behind", "in front of", "next to", "between"]

def build_items(
    rng: random.Random,
    scene: str,
    zones: dict[str, Any],
    n: int = 12,
    prep_set: list[str] | None = None,
) -> list[dict[str, Any]]:
    preps = prep_set or PREP_SET_C

    # zones.json defines "relations" per prep, mapping -> allowed zone ids
    # Example: relations["under"] = ["zone_under_table", ...]
    relations: dict[str, list[str]] = zones["relations"]
    objects: list[str] = zones["objects"]  # sprite ids e.g. ["ball","cat","book"...]
    if not objects:
        raise ValueError("zones.json: objects is empty")

    # Build tasks by sampling (prep, zone) pairs that exist in scene relations
    candidates: list[tuple[str, str]] = []
    for prep in preps:
        for zid in relations.get(prep, []):
            candidates.append((prep, zid))
    if not candidates:
        raise ValueError("No (prep, zone) candidates in this scene")

    rng.shuffle(candidates)
    items: list[dict[str, Any]] = []
    for i in range(min(n, len(candidates))):
        prep, zone_id = candidates[i]
        obj = rng.choice(objects)

        # Deterministic distractors: same taught set, never “absurd”
        distractors = [p for p in preps if p != prep]
        rng.shuffle(distractors)
        choices = [prep] + distractors[:3]
        rng.shuffle(choices)

        items.append(
            {
                "id": f"dd_{scene}_{i+1}",
                "type": "prepositions_dragdrop",
                "prompt": f"Put the {obj} {prep} the target.",
                "prompt_zh": f"把 {obj} 放到目标的 {prep} 位置。",
                "scene": scene,
                "object_id": obj,
                "correct_prep": prep,
                "zone_id": zone_id,
                "choices": choices,
                "scoring": {"correct": 1, "wrong": 0},
                "ui": {
                    "mode": "drag_to_zone",
                    "show_choices": True,
                    "attempts": 2,
                    "hint": True,
                },
            }
        )
    return items
