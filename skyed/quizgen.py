from __future__ import annotations

import hashlib
import json
import random
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .image_planner import ANTONYM_PAIRS, infer_pos_map
from .utils import ensure_dir, slugify as card_slugify


QUESTION_LIMIT_DEFAULT = 8


def _as_sentence_dicts(sentences: Any) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    if not isinstance(sentences, list):
        return out
    for s in sentences:
        if isinstance(s, str):
            t = s.strip()
            if t:
                out.append({"en": t, "zh": ""})
        elif isinstance(s, dict):
            en = str(s.get("en") or "").strip()
            zh = str(s.get("zh") or "").strip()
            if en or zh:
                out.append({"en": en, "zh": zh})
    return out


def _dedupe_preserve(items: Sequence[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in items:
        t = str(x or "").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _norm_word(w: str) -> str:
    return re.sub(r"\s+", " ", str(w or "").strip().lower())


def _seed_from_spec(spec: Dict[str, Any]) -> int:
    base = json.dumps(
        {
            "title": spec.get("title", ""),
            "vocab": spec.get("vocab", []),
            "sentences": spec.get("sentences", []),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return int(hashlib.sha1(base.encode("utf-8")).hexdigest()[:8], 16)


def _blank_word(sentence: str, word: str) -> Optional[str]:
    if not sentence or not word:
        return None
    pattern = re.compile(rf"\b{re.escape(word)}\b", flags=re.IGNORECASE)
    if not pattern.search(sentence):
        return None
    return pattern.sub("____", sentence, count=1)


class Entry(dict):
    @property
    def en(self) -> str:
        return str(self.get("en") or "").strip()

    @property
    def zh(self) -> str:
        return str(self.get("zh") or "").strip()

    @property
    def pos(self) -> str:
        return str(self.get("pos") or "noun").strip().lower() or "noun"

    @property
    def image(self) -> str:
        return str(self.get("img") or "").strip()


def _build_entries(spec: Dict[str, Any]) -> List[Entry]:
    pos_map = infer_pos_map(spec)
    entries: List[Entry] = []
    for raw in spec.get("vocab", []) or []:
        if not isinstance(raw, dict):
            continue
        en = str(raw.get("en") or "").strip()
        if not en:
            continue
        zh = str(raw.get("zh") or "").strip()
        pos = str(raw.get("pos") or pos_map.get(_norm_word(en), "noun")).strip().lower() or "noun"
        img = f"cards/{card_slugify(en)}.png"
        entries.append(Entry(en=en, zh=zh, pos=pos, img=img))
    return entries


def _choice_text(text: str, *, subtext: str = "") -> Dict[str, Any]:
    return {"text": text, "subtext": subtext}


def _choice_image(img: str, *, text: str = "", subtext: str = "") -> Dict[str, Any]:
    return {"img": img, "text": text, "subtext": subtext}


def _mcq(
    *,
    q: str,
    choices: List[Any],
    answer_index: int,
    kind: str,
    prompt_image: str = "",
    helper: str = "",
) -> Dict[str, Any]:
    return {
        "type": "mcq",
        "kind": kind,
        "q": q,
        "choices": choices,
        "answer_index": int(answer_index),
        **({"prompt_image": prompt_image} if prompt_image else {}),
        **({"helper": helper} if helper else {}),
    }


def _sample_others(rng: random.Random, items: Sequence[Entry], exclude_en: str, n: int) -> List[Entry]:
    pool = [x for x in items if _norm_word(x.en) != _norm_word(exclude_en)]
    rng.shuffle(pool)
    return pool[:n]


def _build_word_to_picture(entries: List[Entry], rng: random.Random) -> List[Dict[str, Any]]:
    usable = [e for e in entries if e.image]
    if len(usable) < 2:
        return []
    rng.shuffle(usable)
    out: List[Dict[str, Any]] = []
    for correct in usable:
        same_pos = [e for e in usable if e.pos == correct.pos and _norm_word(e.en) != _norm_word(correct.en)]
        distractors = same_pos[:]
        rng.shuffle(distractors)
        if len(distractors) < 3:
            extra = _sample_others(rng, usable, correct.en, 3)
            for x in extra:
                if all(_norm_word(x.en) != _norm_word(y.en) for y in distractors):
                    distractors.append(x)
        distractors = distractors[:3]
        if len(distractors) < 2:
            continue
        options = distractors + [correct]
        rng.shuffle(options)
        choices = [_choice_image(opt.image, text="", subtext=opt.en) for opt in options]
        out.append(
            _mcq(
                q=f"Tap the picture for: {correct.en}",
                choices=choices,
                answer_index=options.index(correct),
                kind="word_to_picture",
                helper="Look carefully at the picture cards.",
            )
        )
    return out


def _build_picture_to_word(entries: List[Entry], rng: random.Random) -> List[Dict[str, Any]]:
    usable = [e for e in entries if e.image]
    if len(usable) < 2:
        return []
    rng.shuffle(usable)
    out: List[Dict[str, Any]] = []
    for correct in usable:
        same_pos = [e for e in entries if e.pos == correct.pos and _norm_word(e.en) != _norm_word(correct.en)]
        distractors = same_pos[:]
        rng.shuffle(distractors)
        if len(distractors) < 3:
            extra = _sample_others(rng, entries, correct.en, 3)
            for x in extra:
                if all(_norm_word(x.en) != _norm_word(y.en) for y in distractors):
                    distractors.append(x)
        distractors = distractors[:3]
        if len(distractors) < 2:
            continue
        options = [x.en for x in distractors] + [correct.en]
        options = _dedupe_preserve(options)
        if len(options) < 3:
            continue
        rng.shuffle(options)
        out.append(
            _mcq(
                q="What is this?",
                prompt_image=correct.image,
                choices=[_choice_text(x) for x in options],
                answer_index=options.index(correct.en),
                kind="picture_to_word",
                helper="Choose the correct word.",
            )
        )
    return out


def _build_meaning_questions(entries: List[Entry], rng: random.Random) -> List[Dict[str, Any]]:
    zh_entries = [e for e in entries if e.zh]
    if len(zh_entries) < 2:
        return []
    rng.shuffle(zh_entries)
    out: List[Dict[str, Any]] = []
    for correct in zh_entries:
        same_pos = [e for e in zh_entries if e.pos == correct.pos and _norm_word(e.en) != _norm_word(correct.en)]
        distractors = same_pos or _sample_others(rng, zh_entries, correct.en, 3)
        distractors = distractors[:3]
        if len(distractors) < 2:
            continue
        options = [x.en for x in distractors] + [correct.en]
        options = _dedupe_preserve(options)
        rng.shuffle(options)
        out.append(
            _mcq(
                q=f"Which word matches: {correct.zh}",
                choices=[_choice_text(x) for x in options],
                answer_index=options.index(correct.en),
                kind="meaning_to_word",
                helper="Use the Chinese meaning to choose the English word.",
            )
        )
    return out


def _build_sentence_cloze(entries: List[Entry], spec: Dict[str, Any], rng: random.Random) -> List[Dict[str, Any]]:
    sentences = _as_sentence_dicts(spec.get("sentences", []))
    if not sentences:
        return []

    nouns = [e for e in entries if e.pos == "noun"]
    adjs = [e for e in entries if e.pos == "adjective"]
    verbs = [e for e in entries if e.pos == "verb"]
    out: List[Dict[str, Any]] = []

    for s in sentences:
        en = s.get("en", "")
        if not en:
            continue
        matched: Optional[Entry] = None
        for e in entries:
            if re.search(rf"\b{re.escape(e.en)}\b", en, flags=re.IGNORECASE):
                matched = e
                break
        if not matched:
            continue

        blanked = _blank_word(en, matched.en)
        if not blanked:
            continue

        if matched.pos == "adjective":
            pool = adjs or entries
        elif matched.pos == "verb":
            pool = verbs or entries
        else:
            pool = nouns or entries

        distractors = [e.en for e in pool if _norm_word(e.en) != _norm_word(matched.en)]
        distractors = _dedupe_preserve(distractors)
        rng.shuffle(distractors)
        options = distractors[:3] + [matched.en]
        options = _dedupe_preserve(options)
        if len(options) < 3:
            continue
        rng.shuffle(options)
        out.append(
            _mcq(
                q=f"Complete the sentence: {blanked}",
                choices=[_choice_text(x) for x in options],
                answer_index=options.index(matched.en),
                kind="sentence_cloze",
                helper="Choose the word that completes the sentence correctly.",
            )
        )
    return out


def _build_adjective_pair(entries: List[Entry], rng: random.Random) -> List[Dict[str, Any]]:
    adjs = [e for e in entries if e.pos == "adjective"]
    if not adjs:
        return []

    adj_map = {_norm_word(e.en): e for e in adjs}
    out: List[Dict[str, Any]] = []
    used = set()
    for e in adjs:
        key = _norm_word(e.en)
        if key in used:
            continue
        opp = ANTONYM_PAIRS.get(key)
        if not opp or opp not in adj_map:
            continue
        used.add(key)
        used.add(opp)
        other = adj_map[opp]
        # Use the adjective card images directly if available.
        if e.image and other.image:
            choices = [
                _choice_image(e.image, text=e.en),
                _choice_image(other.image, text=other.en),
            ]
            rng.shuffle(choices)
            correct_text = e.en
            out.append(
                _mcq(
                    q=f"Which picture shows: {correct_text}?",
                    choices=choices,
                    answer_index=next(i for i, c in enumerate(choices) if c.get("text") == correct_text),
                    kind="adjective_picture",
                    helper="Use the size / contrast clue.",
                )
            )
        out.append(
            _mcq(
                q=f"Choose the correct sentence for {e.en}.",
                choices=[_choice_text(f"It's {e.en}."), _choice_text(f"It's {other.en}.")],
                answer_index=0,
                kind="adjective_sentence",
                helper="Pick the sentence that matches the adjective.",
            )
        )
    return out


def generate_quiz(spec: Dict[str, Any], out_dir: Path, n_questions: int = QUESTION_LIMIT_DEFAULT) -> Path:
    """
    Generates a deterministic, lesson-aware practice set.

    Design goals:
      - logical beginner-level questions
      - no generic true/false filler
      - picture/word/sentence practice aligned to lesson content
      - current in-page practice stays different from checkpoint-3 tag_s
    """
    ensure_dir(out_dir)

    rng = random.Random(_seed_from_spec(spec))
    entries = _build_entries(spec)
    title = str(spec.get("title") or "Practice")

    buckets: List[List[Dict[str, Any]]] = [
        _build_picture_to_word(entries, rng),
        _build_word_to_picture(entries, rng),
        _build_sentence_cloze(entries, spec, rng),
        _build_meaning_questions(entries, rng),
        _build_adjective_pair(entries, rng),
    ]

    questions: List[Dict[str, Any]] = []
    target = max(1, int(n_questions or QUESTION_LIMIT_DEFAULT))
    # round-robin so each lesson gets mixed practice instead of one block type only
    while len(questions) < target and any(buckets):
        progressed = False
        for bucket in buckets:
            if len(questions) >= target:
                break
            if bucket:
                questions.append(bucket.pop(0))
                progressed = True
        if not progressed:
            break

    if not questions:
        vocab_words = [e.en for e in entries]
        if vocab_words:
            choices = _dedupe_preserve(vocab_words[:4])
            while len(choices) < 4:
                filler = f"option {len(choices)+1}"
                if filler not in choices:
                    choices.append(filler)
            answer = choices[0]
            rng.shuffle(choices)
            questions = [
                _mcq(
                    q="Choose one word from today’s lesson.",
                    choices=[_choice_text(x) for x in choices],
                    answer_index=choices.index(answer),
                    kind="fallback",
                )
            ]
        else:
            questions = [
                _mcq(
                    q="Ready to start practice?",
                    choices=[_choice_text("Yes"), _choice_text("No")],
                    answer_index=0,
                    kind="fallback",
                )
            ]

    quiz = {
        "title": title,
        "section_title": "Practice",
        "practice_family": "lesson_practice",
        "questions": questions,
    }

    out_path = Path(out_dir) / "quiz.json"
    out_path.write_text(json.dumps(quiz, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path
