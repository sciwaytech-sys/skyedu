from __future__ import annotations
import random
from typing import Any

def build_items(
    rng: random.Random,
    taught_preps: list[str],
    objects: list[str],
    anchors: list[str],
    n: int = 10,
) -> list[dict[str, Any]]:
    """
    Text MCQ: sentence -> choose correct preposition
    Deterministic distractors: same taught set.
    """
    if len(taught_preps) < 3:
        raise ValueError("Need at least 3 prepositions for MCQ")

    items: list[dict[str, Any]] = []
    for i in range(n):
        prep = rng.choice(taught_preps)
        obj = rng.choice(objects)
        anchor = rng.choice(anchors)

        distractors = [p for p in taught_preps if p != prep]
        rng.shuffle(distractors)
        choices = [prep] + distractors[:3]
        rng.shuffle(choices)

        items.append(
            {
                "id": f"mcq_{i+1}",
                "type": "mcq_text",
                "prompt": f"Choose the best preposition: The {obj} is ___ the {anchor}.",
                "prompt_zh": f"选择正确的介词：{obj} 在 ___ {anchor}。",
                "answer": prep,
                "choices": choices,
                "scoring": {"correct": 1, "wrong": 0},
                "ui": {"attempts": 1, "hint": True},
            }
        )
    return items
