from __future__ import annotations
import random

INTENTS = {
    "location_where": {
        "q": [
            "Where is the {obj}?",
            "Where is the {obj} now?",
        ],
        "q_zh": [
            "{obj} 在哪里？",
            "现在 {obj} 在哪里？",
        ],
        "a_templates": [
            "It's {prep} the {anchor}.",
            "The {obj} is {prep} the {anchor}.",
        ],
        "a_templates_zh": [
            "它在 {prep} {anchor}。",
            "{obj} 在 {prep} {anchor}。",
        ],
    }
}

def build_items(
    rng: random.Random,
    taught_preps: list[str],
    objects: list[str],
    anchors: list[str],
    n: int = 8,
) -> list[dict]:
    """
    Q->best A with intent locking: no absurd answers.
    """
    intent = INTENTS["location_where"]
    items = []
    for i in range(n):
        obj = rng.choice(objects)
        anchor = rng.choice(anchors)
        prep = rng.choice(taught_preps)

        q = rng.choice(intent["q"]).format(obj=obj)
        q_zh = rng.choice(intent["q_zh"]).format(obj=obj)

        correct = rng.choice(intent["a_templates"]).format(obj=obj, prep=prep, anchor=anchor)
        correct_zh = rng.choice(intent["a_templates_zh"]).format(obj=obj, prep=prep, anchor=anchor)

        # distractors: same intent, wrong prep/anchor/object but still grammatical
        distractors = []
        for _ in range(10):
            d_obj = rng.choice(objects)
            d_anchor = rng.choice(anchors)
            d_prep = rng.choice([p for p in taught_preps if p != prep] or taught_preps)
            d = rng.choice(intent["a_templates"]).format(obj=d_obj, prep=d_prep, anchor=d_anchor)
            if d != correct and d not in distractors:
                distractors.append(d)
            if len(distractors) >= 3:
                break

        choices = [correct] + distractors[:3]
        rng.shuffle(choices)

        items.append(
            {
                "id": f"qa_{i+1}",
                "type": "qa_dialog",
                "prompt": q,
                "prompt_zh": q_zh,
                "answer": correct,
                "answer_zh": correct_zh,
                "choices": choices,
                "scoring": {"correct": 1, "wrong": 0},
                "ui": {"attempts": 1, "hint": True},
            }
        )
    return items
