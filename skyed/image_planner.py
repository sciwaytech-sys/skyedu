from __future__ import annotations

"""Image planner (Lite)

Problem we solve *now*:
- Your vocab list may include nouns, verbs, adjectives.
- The old pipeline treated everything as a "single object" noun, producing garbage for words like:
  - big, small (adjectives)
  - jump, run (verbs)

We implement a minimal, automation-first planner:
1) infer POS (noun/verb/adjective) mostly from provided sentences (if present)
2) choose a render_mode
3) compile a *subject phrase* suitable for prompt_templates

We intentionally keep this "lite":
- no complex ontology
- no months-long schema work
- deterministic rules + small built-in maps
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .image_semantics import PREPOSITION_SCENES, clean_visual_label, normalized_visual_key, is_comparative, is_superlative, comparative_base, superlative_base


@dataclass(frozen=True)
class PlannedItem:
    en: str
    zh: str
    pos: str  # noun|verb|adjective
    render_mode: str  # single_object|action_scene|contrast_pair|attribute_scene|icon_card
    subject: str  # phrase injected into prompt templates
    fallback_mode: str = "icon_card"


# Common adjective opposites to support contrast_pair quickly.
ANTONYM_PAIRS: Dict[str, str] = {
    "big": "small",
    "small": "big",
    "hot": "cold",
    "cold": "hot",
    "happy": "sad",
    "sad": "happy",
    "fast": "slow",
    "slow": "fast",
    "tall": "short",
    "short": "tall",
    "old": "new",
    "new": "old",
    "clean": "dirty",
    "dirty": "clean",
    "full": "empty",
    "empty": "full",
    "open": "closed",
    "closed": "open",
}

# Verbs that usually need an object for better images
TRANSITIVE_DEFAULT_OBJECT: Dict[str, str] = {
    "eat": "an apple",
    "drink": "a cup of water",
    "open": "a door",
    "close": "a door",
    "read": "a book",
    "write": "in a notebook",
    "draw": "a picture",
    "kick": "a ball",
    "throw": "a ball",
    "catch": "a ball",
    "wash": "hands",
    "clean": "a table",
}


def _tokenize(s: str) -> List[str]:
    return re.findall(r"[A-Za-z']+", s or "")


def _norm_word(w: str) -> str:
    return (w or "").strip().lower()


def _infer_pos_from_sentences(word: str, sentences: List[str]) -> Optional[str]:
    """Infer pos from simple sentence patterns.

    This is automation-first: if the user already provides sentences, we use them.
    """
    w = _norm_word(word)
    if not w:
        return None

    # Patterns for adjective: "It's big." / "It is small." / "The box is big." etc.
    adj_patterns = [
        rf"\b(it\s*'?s|it\s+is|is|are|am)\s+{re.escape(w)}\b",
    ]

    # Patterns for verb: "I can jump" / "We can run" / "to jump" etc.
    verb_patterns = [
        rf"\b(can|to|will|want\s+to|like\s+to|likes\s+to)\s+{re.escape(w)}\b",
        rf"\b(i|we|you|they|he|she)\s+{re.escape(w)}\b",  # weak but useful
    ]

    for s in sentences:
        t = (s or "").strip().lower()
        # normalize typographic apostrophes so "it’s" matches "it's"
        t = t.replace("’", "'").replace("`", "'")
        if not t:
            continue
        for p in adj_patterns:
            if re.search(p, t):
                return "adjective"

    for s in sentences:
        t = (s or "").strip().lower()
        t = t.replace("’", "'").replace("`", "'")
        if not t:
            continue
        for p in verb_patterns:
            if re.search(p, t):
                return "verb"

    return None


def infer_pos_map(spec: Dict[str, Any]) -> Dict[str, str]:
    """Return mapping: vocab_en_lower -> pos."""
    vocab = spec.get("vocab", []) or []
    sentences_raw = spec.get("sentences", []) or []

    sentences_en: List[str] = []
    for s in sentences_raw:
        if isinstance(s, dict):
            en = str(s.get("en") or "").strip()
            if en:
                sentences_en.append(en)
        elif isinstance(s, str):
            t = s.strip()
            if t:
                sentences_en.append(t)

    pos_map: Dict[str, str] = {}

    # pass 1: explicit pos from parser (if present)
    for v in vocab:
        if not isinstance(v, dict):
            continue
        en = str(v.get("en") or "").strip()
        pos = str(v.get("pos") or "").strip().lower()
        if en and pos in ("noun", "verb", "adjective"):
            pos_map[_norm_word(en)] = pos

    # pass 2: infer from sentences
    for v in vocab:
        if not isinstance(v, dict):
            continue
        en = str(v.get("en") or "").strip()
        key = _norm_word(en)
        if not en or key in pos_map:
            continue
        inferred = _infer_pos_from_sentences(en, sentences_en)
        if inferred:
            pos_map[key] = inferred

    # default: noun
    for v in vocab:
        if not isinstance(v, dict):
            continue
        en = str(v.get("en") or "").strip()
        key = _norm_word(en)
        if en and key not in pos_map:
            pos_map[key] = "noun"

    return pos_map


def _default_anchor_noun(vocab_words: List[str], pos_map: Dict[str, str]) -> str:
    for w in vocab_words:
        if pos_map.get(_norm_word(w), "noun") == "noun":
            return w
    return "ball"


def _to_gerund(v: str) -> str:
    """Very small gerund helper (good enough for flashcards)."""
    w = (v or "").strip()
    lw = w.lower()

    if lw.endswith("ie") and len(lw) > 2:
        return w[:-2] + "ying"
    if lw.endswith("e") and len(lw) > 2 and not lw.endswith("ee"):
        return w[:-1] + "ing"
    if lw.endswith("c"):
        return w + "king"
    # Double final consonant for short CVC words: run -> running, sit -> sitting
    # (very small heuristic, good enough for common classroom verbs)
    if len(lw) >= 3:
        vowels = set("aeiou")
        last3 = lw[-3:]
        if (
            last3[0] not in vowels
            and last3[1] in vowels
            and last3[2] not in vowels
            and last3[2] not in ("w", "x", "y")
            and len(lw) <= 5
        ):
            return w + w[-1] + "ing"
    return w + "ing"


def plan_item(en: str, zh: str, pos: str, *, vocab_words: List[str], pos_map: Dict[str, str]) -> PlannedItem:
    """Plan render_mode + subject phrase."""
    w = clean_visual_label(en)
    lw = normalized_visual_key(w)
    pos = (pos or "noun").strip().lower()

    if pos == "preposition" or lw in PREPOSITION_SCENES:
        return PlannedItem(
            en=w,
            zh=zh,
            pos="preposition",
            render_mode="relation_scene",
            subject=PREPOSITION_SCENES.get(lw, f"objects arranged to clearly show the relation {w}"),
            fallback_mode="icon_card",
        )

    # comparative / superlative adjectives need forced contrast scenes
    if is_superlative(w):
        base = superlative_base(w)
        anchor = "ball" if base in ("big", "small") else _default_anchor_noun(vocab_words, pos_map)
        return PlannedItem(
            en=w,
            zh=zh,
            pos="adjective",
            render_mode="contrast_pair",
            subject=f"three {anchor}s side by side, one clearly the {w}",
            fallback_mode="icon_card",
        )
    if is_comparative(w):
        base = comparative_base(w)
        anchor = "ball" if base in ("big", "small") else _default_anchor_noun(vocab_words, pos_map)
        return PlannedItem(
            en=w,
            zh=zh,
            pos="adjective",
            render_mode="contrast_pair",
            subject=f"two {anchor}s side by side, one clearly {w} than the other",
            fallback_mode="icon_card",
        )

    # ---------- NOUN ----------
    if pos == "noun":
        return PlannedItem(
            en=w,
            zh=zh,
            pos="noun",
            render_mode="single_object",
            subject=w,
        )

    # ---------- VERB ----------
    if pos == "verb":
        obj = TRANSITIVE_DEFAULT_OBJECT.get(lw)
        if obj:
            # "a child eating an apple"
            subj_phrase = f"a child {_to_gerund(w)} {obj}"
        else:
            subj_phrase = f"a child {_to_gerund(w)}"
        return PlannedItem(
            en=w,
            zh=zh,
            pos="verb",
            render_mode="action_scene",
            subject=subj_phrase,
        )

    # ---------- ADJECTIVE ----------
    if pos == "adjective":
        opp = ANTONYM_PAIRS.get(lw)
        has_opp_in_vocab = bool(opp and any(_norm_word(x) == _norm_word(opp) for x in vocab_words))

        if has_opp_in_vocab:
            # Force stronger visual contrast for classroom adjectives like big/small.
            anchor = "ball" if lw in ("big", "small") else _default_anchor_noun(vocab_words, pos_map)
            if lw == "big":
                subj_phrase = f"two {anchor}s side by side with dramatic size contrast: one very large {anchor} and one clearly much smaller {anchor}"
            elif lw == "small":
                subj_phrase = f"two {anchor}s side by side with dramatic size contrast: one tiny {anchor} and one clearly much larger {anchor}"
            else:
                subj_phrase = f"two {anchor}s side by side with clear contrast: one {lw}, one {opp}"
            return PlannedItem(
                en=w,
                zh=zh,
                pos="adjective",
                render_mode="contrast_pair",
                subject=subj_phrase,
            )

        # Attribute scene: adjective applied to an anchor noun.
        anchor = _default_anchor_noun(vocab_words, pos_map)
        subj_phrase = f"a {lw} {anchor}"
        return PlannedItem(
            en=w,
            zh=zh,
            pos="adjective",
            render_mode="attribute_scene",
            subject=subj_phrase,
        )

    # Fallback
    return PlannedItem(
        en=w,
        zh=zh,
        pos="noun",
        render_mode="single_object",
        subject=w,
    )


def build_image_plans(spec: Dict[str, Any]) -> List[PlannedItem]:
    """Build planned items in the same order as vocab."""
    vocab = spec.get("vocab", []) or []
    vocab_words: List[str] = []
    for v in vocab:
        if isinstance(v, dict):
            en = str(v.get("en") or "").strip()
            if en:
                vocab_words.append(en)
        else:
            t = str(v).strip()
            if t:
                vocab_words.append(t)

    pos_map = infer_pos_map(spec)

    plans: List[PlannedItem] = []
    for v in vocab:
        if isinstance(v, dict):
            en = str(v.get("en") or "").strip()
            zh = str(v.get("zh") or "").strip()
            pos = str(v.get("pos") or "").strip().lower() or pos_map.get(_norm_word(en), "noun")
        else:
            en = str(v).strip()
            zh = ""
            pos = pos_map.get(_norm_word(en), "noun")
        if not en:
            continue
        plans.append(plan_item(en, zh, pos, vocab_words=vocab_words, pos_map=pos_map))

    return plans
