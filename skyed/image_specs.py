from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, List

from .image_semantics import TEXT_EXCLUSION_TOKENS, clean_visual_label, resolve_visual_plan
from .parser import parse_homework_text

ANCHOR_PHRASES = [
    "ESL lesson illustration for children",
    "clean educational illustration",
    "clear literal meaning",
    "plain or uncluttered background",
    "safe for young learners",
]

POS_ALIASES = {
    "n": "noun",
    "noun": "noun",
    "v": "verb",
    "verb": "verb",
    "adj": "adjective",
    "adjective": "adjective",
    "adv": "adverb",
    "adverb": "adverb",
    "prep": "preposition",
    "preposition": "preposition",
    "phrase": "phrase",
    "expression": "phrase",
    "pron": "pronoun",
    "pronoun": "pronoun",
    "question": "question_word",
    "question_word": "question_word",
    "question word": "question_word",
    "det": "determiner",
    "determiner": "determiner",
    "num": "number",
    "number": "number",
    "time": "time",
}

SEMANTIC_DEFAULT_NEGATIVES = {
    "noun": ["abstract symbol", "logo", "typography only", "blur"],
    "verb": ["isolated object", "static still life", "logo", "typography only", "abstract concept art"],
    "adjective": ["isolated stationery", "empty desk", "logo", "typography only", "abstract art"],
    "time": ["isolated object", "logo", "typography only", "abstract symbol"],
    "phrase": ["logo", "typography only", "abstract concept art"],
    "preposition": ["logo", "typography only", "abstract concept art", "floating text labels"],
    "pronoun": ["logo", "typography only", "floating text labels"],
    "question_word": ["speech bubble text", "logo", "floating text labels"],
    "number": ["written digits", "poster numbers", "text labels"],
    "determiner": ["logo", "typography only"],
}

WORD_OVERRIDES = {
    "finish": {
        "scene": "a primary school child finishing homework at a desk, workbook and pencil visible, satisfied expression after completing the task",
        "include": ["child", "desk", "workbook", "pencil", "finished task", "school or home study scene"],
        "exclude": ["crumbs", "random floor mess", "food focus", "still life"],
        "render_mode": "action_scene",
        "scene_type": "literal_action_scene",
    },
    "carry": {
        "scene": "a child carrying a schoolbag and books while walking to school, action clearly visible",
        "include": ["child", "schoolbag", "books", "walking", "school context"],
        "exclude": ["blanket", "cape", "fashion pose", "unclear object"],
        "render_mode": "action_scene",
        "scene_type": "literal_action_scene",
    },
    "visit": {
        "scene": "a child visiting grandparents or friends at home, greeting scene, warm family context",
        "include": ["child", "family or friends", "home visit", "greeting action"],
        "exclude": ["child alone at table", "random still life", "no social interaction"],
        "render_mode": "action_scene",
        "scene_type": "literal_action_scene",
    },
    "bag": {
        "scene": "a child's schoolbag or backpack in a school setting, or a child carrying the backpack",
        "include": ["schoolbag", "backpack", "child or desk", "school context"],
        "exclude": ["handbag", "luxury bag", "fashion bag", "tote bag"],
        "render_mode": "single_object",
        "scene_type": "literal_object_scene",
    },
}


@dataclass(slots=True)
class VocabItem:
    word: str
    pos: str
    zh: str = ""
    example_en: str = ""
    theme: str = ""
    title: str = ""


@dataclass(slots=True)
class ImageSpec:
    word: str
    pos: str
    zh: str = ""
    theme: str = ""
    example_en: str = ""
    scene_type: str = ""
    render_mode: str = "single_object"
    positive_prompt: str = ""
    negative_prompt: str = ""
    must_include: list[str] = field(default_factory=list)
    must_exclude: list[str] = field(default_factory=list)
    fallback_label: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_pos(pos: str) -> str:
    return POS_ALIASES.get((pos or "").strip().lower(), (pos or "noun").strip().lower() or "noun")


def slugify(text: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower())
    return text.strip("-") or "item"


def infer_scene_type(pos: str, word: str) -> str:
    item = VocabItem(word=word, pos=pos)
    return build_image_spec(item).scene_type


def _context_hint(example_en: str) -> str:
    ex = (example_en or "").strip()
    if not ex:
        return ""
    ex = re.sub(r"\s+", " ", ex)
    ex = ex.replace("'", "").replace('"', "")
    if len(ex) > 72:
        ex = ex[:72].rsplit(" ", 1)[0]
    return f"soft classroom context from lesson sentence: {ex}" if ex else ""


def build_prompt_parts(item: VocabItem) -> tuple[list[str], list[str], list[str], str, str, str]:
    clean = clean_visual_label(item.word)
    pos = normalize_pos(item.pos)
    override = WORD_OVERRIDES.get(clean.lower())
    if override:
        include = list(dict.fromkeys(list(override.get("include", [])) + [clean]))
        exclude = list(dict.fromkeys(list(override.get("exclude", [])) + list(TEXT_EXCLUSION_TOKENS) + list(SEMANTIC_DEFAULT_NEGATIVES.get(pos, []))))
        return ANCHOR_PHRASES.copy(), include, exclude, override["scene"], override.get("render_mode", "single_object"), override.get("scene_type", "literal_educational_scene")

    plan = resolve_visual_plan(clean, pos, item.zh, item.example_en)
    include = list(dict.fromkeys(plan.include + [clean]))
    exclude = list(dict.fromkeys(plan.exclude + list(SEMANTIC_DEFAULT_NEGATIVES.get(pos, []))))
    return ANCHOR_PHRASES.copy(), include, exclude, plan.scene, plan.render_mode, plan.scene_type


def build_image_spec(item: VocabItem) -> ImageSpec:
    clean = clean_visual_label(item.word)
    normalized_pos = normalize_pos(item.pos)
    anchors, include, exclude, scene, render_mode, scene_type = build_prompt_parts(item)

    parts = [
        scene,
        "show the target meaning directly",
        "avoid symbolic or decorative substitutions",
        "no text, no letters, no numbers, no chinese characters, no english words, no labels, no signboards, no watermark",
    ]
    context_hint = _context_hint(item.example_en)
    if context_hint:
        parts.append(context_hint)
    parts.extend(anchors)

    positive_prompt = ", ".join([p for p in parts if p])
    negative_prompt = ", ".join(dict.fromkeys([x for x in exclude if x]))
    fallback_label = f"{clean} · {item.zh}" if item.zh else clean

    return ImageSpec(
        word=clean,
        pos=normalized_pos,
        zh=item.zh,
        theme=item.theme,
        example_en=item.example_en,
        scene_type=scene_type,
        render_mode=render_mode,
        positive_prompt=positive_prompt,
        negative_prompt=negative_prompt,
        must_include=list(dict.fromkeys(include)),
        must_exclude=list(dict.fromkeys(exclude)),
        fallback_label=fallback_label,
    )


def parse_homework_vocabulary(homework_text: str) -> list[VocabItem]:
    spec = parse_homework_text(homework_text)
    title = str(spec.get("title") or "")
    theme = ", ".join([str(t).strip() for t in (spec.get("tags") or []) if str(t).strip()])
    example_map: dict[str, str] = {}
    for sent in spec.get("sentences", []) or []:
        if not isinstance(sent, dict):
            continue
        en_line = str(sent.get("en") or "").strip()
        if not en_line:
            continue
        normalized = re.sub(r"[^a-zA-Z' -]", " ", en_line).lower()
        for token in re.findall(r"[a-zA-Z][a-zA-Z'-]+", normalized):
            example_map.setdefault(token, en_line)

    items: list[VocabItem] = []
    for raw in spec.get("vocab", []) or []:
        if not isinstance(raw, dict):
            continue
        word = str(raw.get("en") or "").strip()
        if not word:
            continue
        items.append(
            VocabItem(
                word=word,
                pos=normalize_pos(str(raw.get("pos") or "noun")),
                zh=str(raw.get("zh") or "").strip(),
                example_en=example_map.get(clean_visual_label(word).lower(), ""),
                theme=theme,
                title=title,
            )
        )
    return items


def build_specs_from_homework_text(homework_text: str) -> list[ImageSpec]:
    return [build_image_spec(item) for item in parse_homework_vocabulary(homework_text)]


def build_specs_from_parsed_spec(spec: dict) -> list[ImageSpec]:
    title = str(spec.get("title") or "")
    theme = ", ".join([str(t).strip() for t in (spec.get("tags") or []) if str(t).strip()])
    example_map: dict[str, str] = {}
    for sent in spec.get("sentences", []) or []:
        if not isinstance(sent, dict):
            continue
        en_line = str(sent.get("en") or "").strip()
        if not en_line:
            continue
        normalized = re.sub(r"[^a-zA-Z' -]", " ", en_line).lower()
        for token in re.findall(r"[a-zA-Z][a-zA-Z'-]+", normalized):
            example_map.setdefault(token, en_line)

    items: List[ImageSpec] = []
    for raw in spec.get("vocab", []) or []:
        if not isinstance(raw, dict):
            continue
        word = str(raw.get("en") or "").strip()
        if not word:
            continue
        item = VocabItem(
            word=word,
            pos=normalize_pos(str(raw.get("pos") or "noun")),
            zh=str(raw.get("zh") or "").strip(),
            example_en=example_map.get(clean_visual_label(word).lower(), ""),
            theme=theme,
            title=title,
        )
        items.append(build_image_spec(item))
    return items


def save_specs_json(specs: Iterable[ImageSpec], out_path: str | Path) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([s.to_dict() for s in specs], ensure_ascii=False, indent=2), encoding="utf-8")
    return out


__all__ = [
    "VocabItem",
    "ImageSpec",
    "normalize_pos",
    "slugify",
    "infer_scene_type",
    "parse_homework_vocabulary",
    "build_image_spec",
    "build_specs_from_homework_text",
    "build_specs_from_parsed_spec",
    "save_specs_json",
]
