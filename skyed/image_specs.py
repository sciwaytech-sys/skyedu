from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional

ANCHOR_PHRASES = [
    "ESL lesson illustration for children",
    "clear literal meaning",
    "single obvious concept",
    "school or home context",
    "safe for young learners",
    "clean educational illustration",
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
    "time": "time",
    "phrase": "phrase",
    "expression": "phrase",
}

SEMANTIC_DEFAULT_NEGATIVES = {
    "noun": ["abstract symbol", "logo", "typography only", "blur", "still life unless object is the exact noun"],
    "verb": ["isolated object", "static still life", "logo", "typography only", "abstract concept art"],
    "adjective": ["isolated stationery", "empty desk", "logo", "typography only", "abstract art"],
    "time": ["isolated object", "logo", "typography only", "abstract symbol"],
    "phrase": ["logo", "typography only", "abstract concept art"],
    "preposition": ["logo", "typography only", "abstract concept art"],
}

WORD_OVERRIDES = {
    "finish": {
        "scene": "A primary school child finishing homework at a desk, workbook and pencil visible, satisfied expression after completing the task",
        "include": ["child", "desk", "workbook", "pencil", "finished task", "school or home study scene"],
        "exclude": ["crumbs", "random floor mess", "food focus", "still life"],
    },
    "carry": {
        "scene": "A child carrying a schoolbag and books while walking to school, action clearly visible",
        "include": ["child", "schoolbag", "books", "walking", "school context"],
        "exclude": ["blanket", "cape", "fashion pose", "unclear object"],
    },
    "visit": {
        "scene": "A child visiting grandparents or friends at home, greeting scene, warm family context",
        "include": ["child", "family or friends", "home visit", "greeting action"],
        "exclude": ["child alone at table", "random still life", "no social interaction"],
    },
    "homework": {
        "scene": "A student doing homework at a desk with notebook, pencil, and schoolbook",
        "include": ["student", "desk", "notebook", "pencil", "schoolbook"],
        "exclude": ["decorative objects only", "unclear papers", "still life only"],
    },
    "bag": {
        "scene": "A child's schoolbag or backpack in a school setting, or a child carrying the backpack",
        "include": ["schoolbag", "backpack", "child or desk", "school context"],
        "exclude": ["handbag", "luxury bag", "fashion bag", "tote bag"],
    },
    "weekend": {
        "scene": "A family spending the weekend together, child-friendly home or park scene",
        "include": ["family", "child", "weekend activity", "home or park"],
        "exclude": ["camera", "isolated object", "logo", "calendar icon only"],
    },
    "tired": {
        "scene": "A tired child after school, yawning or resting with a schoolbag nearby",
        "include": ["child", "tired face", "yawning or resting", "after school context"],
        "exclude": ["stationery only", "vase", "random desk objects", "still life"],
    },
    "busy": {
        "scene": "A busy child doing several school tasks at a desk, books and homework visible",
        "include": ["child", "desk", "books", "homework", "active working"],
        "exclude": ["random crowd", "unclear action", "abstract activity"],
    },
    "happy": {
        "scene": "A happy smiling child in a school or homework context, clear joyful expression",
        "include": ["child", "smile", "joyful face", "school or homework context"],
        "exclude": ["stationery only", "objects only", "empty desk", "still life"],
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
    positive_prompt: str = ""
    negative_prompt: str = ""
    must_include: list[str] = field(default_factory=list)
    must_exclude: list[str] = field(default_factory=list)
    fallback_label: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


_HEADER_RE = re.compile(r"^\s*#\s*([A-Za-z ]+)\s*:\s*(.*?)\s*$")
_VOCAB_LINE_RE = re.compile(r"\s*([A-Za-z]+)\s*:\s*([^,]+)")


def normalize_pos(pos: str) -> str:
    pos = (pos or "").strip().lower()
    return POS_ALIASES.get(pos, pos or "noun")


def slugify(text: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower())
    return text.strip("-") or "item"


def infer_scene_type(pos: str, word: str) -> str:
    pos = normalize_pos(pos)
    if word in WORD_OVERRIDES:
        if pos == "verb":
            return "literal action scene"
        if pos == "adjective":
            return "emotion/state scene"
        if pos == "time":
            return "time-context family scene"
    return {
        "noun": "literal object or school scene",
        "verb": "literal action scene",
        "adjective": "emotion/state scene",
        "time": "time-context scene",
        "phrase": "literal phrase scene",
        "preposition": "position scene",
    }.get(pos, "literal educational scene")


def build_prompt_parts(item: VocabItem) -> tuple[list[str], list[str], list[str], str]:
    word = slugify(item.word).replace("-", " ")
    pos = normalize_pos(item.pos)
    include = []
    exclude = list(SEMANTIC_DEFAULT_NEGATIVES.get(pos, []))

    if word in WORD_OVERRIDES:
        override = WORD_OVERRIDES[word]
        scene = override["scene"]
        include.extend(override.get("include", []))
        exclude.extend(override.get("exclude", []))
        return ANCHOR_PHRASES.copy(), include, exclude, scene

    if pos == "noun":
        scene = (
            f"A clear educational illustration showing the noun '{item.word}' in a school or home learning context"
        )
        include.extend([item.word, "clear focus", "child-friendly context"])
    elif pos == "verb":
        scene = (
            f"A child clearly performing the action '{item.word}' in a school or home context, with the action easy to understand"
        )
        include.extend(["child", item.word, "clear action", "school or home context"])
    elif pos == "adjective":
        scene = (
            f"A child clearly showing the feeling or state '{item.word}' through facial expression or body language"
        )
        include.extend(["child", item.word, "clear expression", "easy to understand"])
    elif pos == "time":
        scene = (
            f"A child-friendly scene that clearly represents '{item.word}' as a daily life time expression"
        )
        include.extend([item.word, "daily life scene", "child-friendly"])
    elif pos == "preposition":
        scene = (
            f"Objects arranged to clearly demonstrate the preposition '{item.word}' in a simple ESL learning scene"
        )
        include.extend(["clear position", item.word, "simple objects"])
    else:
        scene = (
            f"A child-friendly educational illustration showing the meaning of '{item.word}' clearly and literally"
        )
        include.extend([item.word, "clear literal meaning"])

    if item.example_en:
        include.append(f"example context: {item.example_en}")
    if item.theme:
        include.append(f"lesson theme: {item.theme}")
    if item.zh:
        include.append(f"Chinese meaning: {item.zh}")

    return ANCHOR_PHRASES.copy(), include, exclude, scene


def build_image_spec(item: VocabItem) -> ImageSpec:
    anchors, include, exclude, scene = build_prompt_parts(item)
    parts = [scene]
    if item.theme:
        parts.append(f"Theme anchor: {item.theme}")
    if item.example_en:
        parts.append(f"Sentence anchor: {item.example_en}")
    if item.zh:
        parts.append(f"Chinese target meaning: {item.zh}")
    parts.extend(anchors)
    parts.append("Do not use symbolic or decorative substitutions")
    positive_prompt = ", ".join(parts)
    negative_prompt = ", ".join(dict.fromkeys(exclude))
    fallback_label = f"{item.word} · {item.zh}" if item.zh else item.word

    return ImageSpec(
        word=item.word,
        pos=normalize_pos(item.pos),
        zh=item.zh,
        theme=item.theme,
        example_en=item.example_en,
        scene_type=infer_scene_type(normalize_pos(item.pos), slugify(item.word)),
        positive_prompt=positive_prompt,
        negative_prompt=negative_prompt,
        must_include=list(dict.fromkeys(include)),
        must_exclude=list(dict.fromkeys(exclude)),
        fallback_label=fallback_label,
    )


def parse_homework_vocabulary(homework_text: str) -> list[VocabItem]:
    title = ""
    tags = ""
    example_map: dict[str, str] = {}
    vocab_text = ""
    in_sentences = False
    in_vocab = False

    for raw_line in homework_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        header_match = _HEADER_RE.match(line)
        if header_match:
            header = header_match.group(1).strip().lower()
            value = header_match.group(2).strip()
            if header == "title":
                title = value
            elif header == "tags":
                tags = value
            elif header == "vocabulary":
                vocab_text = value
                in_vocab = True
                in_sentences = False
            elif header == "sentences":
                in_sentences = True
                in_vocab = False
            else:
                in_vocab = False
                in_sentences = False
            continue

        if line.startswith("#"):
            in_vocab = False
            in_sentences = False
            continue

        if in_vocab:
            vocab_text = f"{vocab_text}, {line}" if vocab_text else line
        elif in_sentences and not re.search(r"[\u4e00-\u9fff]", line):
            normalized = re.sub(r"[^a-zA-Z' -]", " ", line).lower()
            for token in re.findall(r"[a-zA-Z][a-zA-Z'-]+", normalized):
                example_map.setdefault(token, line)

    items: list[VocabItem] = []
    for match in _VOCAB_LINE_RE.finditer(vocab_text):
        pos = normalize_pos(match.group(1))
        body = match.group(2).strip()
        if "-" in body:
            word, zh = body.split("-", 1)
        else:
            word, zh = body, ""
        word = word.strip()
        zh = zh.strip()
        items.append(
            VocabItem(
                word=word,
                pos=pos,
                zh=zh,
                example_en=example_map.get(word.lower(), ""),
                theme=tags,
                title=title,
            )
        )
    return items


def build_specs_from_homework_text(homework_text: str) -> list[ImageSpec]:
    return [build_image_spec(item) for item in parse_homework_vocabulary(homework_text)]


def build_specs_from_parsed_spec(spec: dict) -> list[ImageSpec]:
    title = str((spec or {}).get("title") or "").strip()
    tags_raw = (spec or {}).get("tags") or []
    if isinstance(tags_raw, list):
        theme = ", ".join(str(x).strip() for x in tags_raw if str(x).strip())
    else:
        theme = str(tags_raw).strip()

    example_map: dict[str, str] = {}
    for sent in (spec or {}).get("sentences", []) or []:
        if not isinstance(sent, dict):
            continue
        en_line = str(sent.get("en") or "").strip()
        if not en_line:
            continue
        normalized = re.sub(r"[^a-zA-Z' -]", " ", en_line).lower()
        for token in re.findall(r"[a-zA-Z][a-zA-Z'-]+", normalized):
            example_map.setdefault(token, en_line)

    out: list[ImageSpec] = []
    for raw in (spec or {}).get("vocab", []) or []:
        if not isinstance(raw, dict):
            continue
        word = str(raw.get("en") or "").strip()
        if not word:
            continue
        item = VocabItem(
            word=word,
            pos=normalize_pos(str(raw.get("pos") or "")) or "noun",
            zh=str(raw.get("zh") or "").strip(),
            example_en=example_map.get(word.lower(), ""),
            theme=theme,
            title=title,
        )
        out.append(build_image_spec(item))
    return out



def save_specs_json(specs: Iterable[ImageSpec], out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    data = [spec.to_dict() for spec in specs]
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


__all__ = [
    "VocabItem",
    "ImageSpec",
    "normalize_pos",
    "parse_homework_vocabulary",
    "build_image_spec",
    "build_specs_from_homework_text",
    "build_specs_from_parsed_spec",
    "save_specs_json",
]
