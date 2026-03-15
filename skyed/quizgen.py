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
THEME_ALIASES = {
    "app": "sky",
    "sky": "sky",
    "sky_tiles": "sky_tiles",
    "strict": "strict_dark",
    "strict_dark": "strict_dark",
    "fun": "fun_mission",
    "fun_mission": "fun_mission",
}


def normalize_theme_variant(theme_variant: str) -> str:
    return THEME_ALIASES.get(str(theme_variant or "sky").strip().lower(), "sky")


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
    pattern = re.compile(rf"{re.escape(word)}", flags=re.IGNORECASE)
    if not pattern.search(sentence):
        return None
    return pattern.sub("____", sentence, count=1)


def _sentence_audio_stem(base_text: str) -> str:
    t = (base_text or "").strip()
    short = card_slugify(t[:60] if len(t) > 60 else t)
    h = hashlib.sha1(t.encode("utf-8")).hexdigest()[:10]
    return f"{short}_{h}" if short else h


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

    @property
    def audio_en(self) -> str:
        return str(self.get("audio_en") or f"audio/en/{card_slugify(self.en)}.mp3").strip()

    @property
    def audio_zh(self) -> str:
        return str(self.get("audio_zh") or f"audio/zh/{card_slugify(self.en)}.mp3").strip()


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
        img = str(raw.get("img") or f"cards/{card_slugify(en)}.png")
        audio_en = str(raw.get("audio_en") or f"audio/en/{card_slugify(en)}.mp3")
        audio_zh = str(raw.get("audio_zh") or f"audio/zh/{card_slugify(en)}.mp3")
        entries.append(Entry(en=en, zh=zh, pos=pos, img=img, audio_en=audio_en, audio_zh=audio_zh))
    return entries


def _choice_text(text: str, *, subtext: str = "", audio: str = "") -> Dict[str, Any]:
    obj = {"text": text, "subtext": subtext}
    if audio:
        obj["audio"] = audio
    return obj


def _choice_image(img: str, *, text: str = "", subtext: str = "", audio: str = "") -> Dict[str, Any]:
    obj = {"img": img, "text": text, "subtext": subtext}
    if audio:
        obj["audio"] = audio
    return obj


def _mcq(
    *,
    q: str,
    choices: List[Any],
    answer_index: int,
    kind: str,
    prompt_image: str = "",
    prompt_audio: str = "",
    helper: str = "",
    action_label: str = "",
) -> Dict[str, Any]:
    obj = {
        "type": "mcq",
        "kind": kind,
        "q": q,
        "choices": choices,
        "answer_index": int(answer_index),
    }
    if prompt_image:
        obj["prompt_image"] = prompt_image
    if prompt_audio:
        obj["prompt_audio"] = prompt_audio
    if helper:
        obj["helper"] = helper
    if action_label:
        obj["action_label"] = action_label
    return obj


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
        choices = [_choice_image(opt.image, text="", subtext=opt.en, audio=opt.audio_en) for opt in options]
        out.append(
            _mcq(
                q=f"Tap the picture for: {correct.en}",
                choices=choices,
                answer_index=options.index(correct),
                kind="word_to_picture",
                prompt_audio=correct.audio_en,
                helper="Look at the pictures and tap the correct one.",
                action_label="Look and choose",
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
                prompt_audio=correct.audio_en,
                choices=[_choice_text(x) for x in options],
                answer_index=options.index(correct.en),
                kind="picture_to_word",
                helper="Look at the picture and choose the correct word.",
                action_label="Look and choose",
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
                choices=[_choice_text(x, audio=(next((e.audio_en for e in entries if _norm_word(e.en)==_norm_word(x)), ""))) for x in options],
                answer_index=options.index(correct.en),
                kind="meaning_to_word",
                prompt_audio=correct.audio_en,
                helper="Use the Chinese meaning to choose the English word.",
                action_label="Meaning to word",
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
    used_words = set()

    ranked = []
    for s in sentences:
        en = s.get("en", "")
        if not en:
            continue
        hits = [e for e in entries if re.search(rf"{re.escape(e.en)}", en, flags=re.IGNORECASE)]
        if not hits:
            continue
        ranked.append((0 if len(hits) == 1 else len(hits), en, hits))

    ranked.sort(key=lambda x: (x[0], len(x[1])))

    for _, en, hits in ranked:
        matched: Optional[Entry] = None
        for e in hits:
            key = _norm_word(e.en)
            if key not in used_words:
                matched = e
                used_words.add(key)
                break
        if not matched:
            matched = hits[0]

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
                prompt_audio=f"audio/en/sent_{_sentence_audio_stem(en)}.mp3",
                helper="Choose the best word to finish the sentence.",
                action_label="Finish the sentence",
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
        if e.image and other.image:
            choices = [
                _choice_image(e.image, text=e.en, audio=e.audio_en),
                _choice_image(other.image, text=other.en, audio=other.audio_en),
            ]
            rng.shuffle(choices)
            correct_text = e.en
            out.append(
                _mcq(
                    q=f"Which picture shows: {correct_text}?",
                    choices=choices,
                    answer_index=next(i for i, c in enumerate(choices) if c.get("text") == correct_text),
                    kind="adjective_picture",
                    prompt_audio=e.audio_en,
                    helper="Use the size or contrast clue.",
                    action_label="Look and choose",
                )
            )
        out.append(
            _mcq(
                q=f"Choose the correct sentence for {e.en}.",
                choices=[_choice_text(f"It's {e.en}."), _choice_text(f"It's {other.en}.")],
                answer_index=0,
                kind="adjective_sentence",
                prompt_audio=e.audio_en,
                helper="Pick the sentence that matches the adjective.",
                action_label="Read and choose",
            )
        )
    return out


def _build_listen_to_word(entries: List[Entry], rng: random.Random) -> List[Dict[str, Any]]:
    usable = [e for e in entries if e.audio_en]
    if len(usable) < 2:
        return []
    rng.shuffle(usable)
    out: List[Dict[str, Any]] = []
    for correct in usable:
        distractors = _sample_others(rng, usable, correct.en, 3)
        if len(distractors) < 2:
            continue
        options = _dedupe_preserve([x.en for x in distractors] + [correct.en])
        rng.shuffle(options)
        out.append(
            _mcq(
                q="Listen. Which word did you hear?",
                choices=[_choice_text(x) for x in options],
                answer_index=options.index(correct.en),
                kind="listen_to_word",
                prompt_audio=correct.audio_en,
                helper="Play the audio and choose the matching word.",
                action_label="Listen and choose",
            )
        )
    return out


def _build_listen_to_picture(entries: List[Entry], rng: random.Random) -> List[Dict[str, Any]]:
    usable = [e for e in entries if e.audio_en and e.image]
    if len(usable) < 2:
        return []
    rng.shuffle(usable)
    out: List[Dict[str, Any]] = []
    for correct in usable:
        distractors = _sample_others(rng, usable, correct.en, 3)
        distractors = [d for d in distractors if d.image]
        if len(distractors) < 2:
            continue
        options = distractors[:3] + [correct]
        rng.shuffle(options)
        out.append(
            _mcq(
                q="Listen and tap the correct picture.",
                choices=[_choice_image(x.image, text="", subtext=x.en, audio=x.audio_en) for x in options],
                answer_index=options.index(correct),
                kind="listen_to_picture",
                prompt_audio=correct.audio_en,
                helper="Play the word, then tap the matching picture.",
                action_label="Listen and choose",
            )
        )
    return out


def _pick_from_bucket(bucket: List[Dict[str, Any]], count: int) -> List[Dict[str, Any]]:
    return bucket[:max(0, count)]


def _extend_with_fallback(base: List[Dict[str, Any]], target: int, *extras: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = list(base)
    seen = {(q.get("kind"), q.get("q")) for q in out if isinstance(q, dict)}
    for bucket in extras:
        for q in bucket:
            key = (q.get("kind"), q.get("q")) if isinstance(q, dict) else None
            if key in seen:
                continue
            out.append(q)
            if key:
                seen.add(key)
            if len(out) >= target:
                return out[:target]
    return out[:target]


def _build_theme_quiz(entries: List[Entry], spec: Dict[str, Any], rng: random.Random, theme_variant: str, n_questions: int) -> Dict[str, Any]:
    picture = _build_picture_to_word(entries, rng)
    wordpic = _build_word_to_picture(entries, rng)
    listen_word = _build_listen_to_word(entries, rng)
    listen_pic = _build_listen_to_picture(entries, rng)
    meaning = _build_meaning_questions(entries, rng)
    cloze = _build_sentence_cloze(entries, spec, rng)
    adj = _build_adjective_pair(entries, rng)

    title = str(spec.get("title") or "Practice")
    target = max(1, int(n_questions or QUESTION_LIMIT_DEFAULT))

    if theme_variant == "sky_tiles":
        # Pre-reader mode: audio first, picture choice only.
        questions = _pick_from_bucket(listen_pic, min(5, target))
        return {
            "title": title,
            "section_title": "Listen & Tap",
            "subtitle": f"{len(questions)} rounds · listen and tap the picture",
            "practice_family": "kid_tiles",
            "renderer_mode": "kid_single",
            "questions": questions,
        }

    if theme_variant == "strict_dark":
        questions = _pick_from_bucket(meaning, 4) + _pick_from_bucket(cloze, 4)
        questions = _extend_with_fallback(questions, target, adj, listen_word, picture, wordpic)
        return {
            "title": title,
            "section_title": "Study Check",
            "subtitle": f"{len(questions)} questions · read and choose",
            "practice_family": "strict_study",
            "renderer_mode": "single",
            "questions": questions,
        }

    if theme_variant == "fun_mission":
        questions = _pick_from_bucket(wordpic, 2) + _pick_from_bucket(listen_pic, 2) + _pick_from_bucket(listen_word, 2) + _pick_from_bucket(cloze, 2)
        questions = _extend_with_fallback(questions, target, picture, meaning, adj)
        return {
            "title": title,
            "section_title": "Mission Check",
            "subtitle": f"{len(questions)} checkpoints · earn your stars",
            "practice_family": "fun_mission",
            "renderer_mode": "single",
            "questions": questions,
        }

    # sky
    questions = _pick_from_bucket(meaning, 2) + _pick_from_bucket(picture, 2) + _pick_from_bucket(listen_word, 2) + _pick_from_bucket(cloze, 2)
    questions = _extend_with_fallback(questions, target, wordpic, adj, listen_pic)
    return {
        "title": title,
        "section_title": "Practice",
        "subtitle": f"{len(questions)} questions · look, listen, and choose",
        "practice_family": "lesson_practice",
        "renderer_mode": "list",
        "questions": questions,
    }


def generate_quiz(spec: Dict[str, Any], out_dir: Path, n_questions: int = QUESTION_LIMIT_DEFAULT, theme_variant: str = "sky") -> Path:
    """
    Generates a deterministic, lesson-aware practice set.

    Theme-aware families:
      - sky: balanced standard homework
      - sky_tiles: child-first, shorter, image/audio-led
      - strict_dark: text-first study check
      - fun_mission: guided checkpoint style
    """
    ensure_dir(out_dir)
    rng = random.Random(_seed_from_spec(spec))
    entries = _build_entries(spec)
    theme_variant = normalize_theme_variant(theme_variant)

    quiz = _build_theme_quiz(entries, spec, rng, theme_variant, n_questions)

    if not quiz.get("questions"):
        vocab_words = [e.en for e in entries]
        if vocab_words:
            choices = _dedupe_preserve(vocab_words[:4])
            while len(choices) < 4:
                filler = f"option {len(choices)+1}"
                if filler not in choices:
                    choices.append(filler)
            answer = choices[0]
            rng.shuffle(choices)
            quiz = {
                "title": str(spec.get("title") or "Practice"),
                "section_title": "Practice",
                "subtitle": "1 question · quick check",
                "practice_family": "fallback",
                "renderer_mode": "list",
                "questions": [
                    _mcq(
                        q="Choose one word from today’s lesson.",
                        choices=[_choice_text(x) for x in choices],
                        answer_index=choices.index(answer),
                        kind="fallback",
                        action_label="Choose",
                    )
                ],
            }
        else:
            quiz = {
                "title": str(spec.get("title") or "Practice"),
                "section_title": "Practice",
                "subtitle": "1 question · quick check",
                "practice_family": "fallback",
                "renderer_mode": "list",
                "questions": [
                    _mcq(
                        q="Ready to start practice?",
                        choices=[_choice_text("Yes"), _choice_text("No")],
                        answer_index=0,
                        kind="fallback",
                        action_label="Choose",
                    )
                ],
            }

    out_path = Path(out_dir) / "quiz.json"
    out_path.write_text(json.dumps(quiz, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path
