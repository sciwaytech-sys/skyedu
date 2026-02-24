from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .utils import ensure_dir


def _as_sentence_strings(sentences: Any) -> List[str]:
    """
    Accepts:
      - list[str]
      - list[{"en":..., "zh":...}]
    Returns a clean list of EN sentences (strings).
    """
    out: List[str] = []
    if not sentences:
        return out

    if isinstance(sentences, list):
        for s in sentences:
            if isinstance(s, str):
                t = s.strip()
                if t:
                    out.append(t)
            elif isinstance(s, dict):
                en = str(s.get("en") or "").strip()
                # Prefer EN-only to keep MCQ readable.
                if en:
                    out.append(en)
    return out


def _dedupe_preserve(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in items:
        if not x:
            continue
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def _make_mcq(q: str, correct: str, pool: List[str], n_choices: int = 4) -> Dict[str, Any]:
    correct = str(correct or "").strip() or "—"
    q = str(q or "").strip()

    # Remove duplicates and exclude correct from distractor pool
    pool2 = _dedupe_preserve([str(x).strip() for x in pool if str(x).strip() and str(x).strip() != correct])
    random.shuffle(pool2)

    choices = [correct]
    for x in pool2:
        if len(choices) >= n_choices:
            break
        choices.append(x)

    # Fill with unique placeholders if needed (avoid repeated '—')
    placeholder_idx = 1
    while len(choices) < n_choices:
        filler = f"— {placeholder_idx}"
        if filler not in choices:
            choices.append(filler)
        placeholder_idx += 1

    random.shuffle(choices)

    return {
        "type": "mcq",
        "q": q,
        "choices": choices,
        "answer_index": choices.index(correct),
    }


def _make_mcq_from_qa(qa: Dict[str, Any], distractors: List[str]) -> Dict[str, Any]:
    return _make_mcq(q=str(qa["q"]), correct=str(qa["a"]), pool=distractors, n_choices=4)


def _choose_correct_sentence_mcq(sentences: List[str]) -> List[Dict[str, Any]]:
    """
    For each sentence, build an MCQ where correct option is the original sentence,
    distractors are other sentences.
    """
    sents = [s.strip() for s in (sentences or []) if isinstance(s, str) and s.strip()]
    sents = _dedupe_preserve(sents)
    if len(sents) < 2:
        return []

    pool = sents[:]
    out: List[Dict[str, Any]] = []

    for s in sents:
        distract = [x for x in pool if x != s]
        random.shuffle(distract)

        # If not enough distractors, add placeholders
        while len(distract) < 3:
            distract.append(f"Sentence option {len(distract) + 1}")

        q = "Choose the correct sentence:"
        out.append(_make_mcq(q=q, correct=s, pool=distract, n_choices=4))

    return out


def _sentence_true_false(sentences: List[str]) -> List[Dict[str, Any]]:
    """
    Simple T/F:
      - True items from original sentences
      - Some False items created by swapping in a different sentence (if possible)
    """
    sents = [s.strip() for s in (sentences or []) if isinstance(s, str) and s.strip()]
    sents = _dedupe_preserve(sents)

    out: List[Dict[str, Any]] = []
    if not sents:
        return out

    # True items
    for s in sents:
        out.append({"type": "tf", "q": f"True or False: {s}", "answer_bool": True})

    # Optional false items (safe/simple: reuse other sentence but mark false to add variety)
    # This is pedagogically weak but better than all-true; frontend can still render it.
    if len(sents) >= 2:
        shuffled = sents[:]
        random.shuffle(shuffled)
        for i, s in enumerate(sents[: max(1, len(sents) // 2)]):
            alt = shuffled[i % len(shuffled)]
            if alt == s:
                # choose a different one
                for cand in shuffled:
                    if cand != s:
                        alt = cand
                        break
            if alt != s:
                out.append({"type": "tf", "q": f"True or False: {alt}", "answer_bool": False})

    return out


def generate_quiz(spec: Dict[str, Any], out_dir: Path, n_questions: int = 8) -> Path:
    """
    Output: quiz.json in out_dir

    Pools (in priority):
      1) Q&A MCQ
      2) Vocab EN->ZH meaning MCQ (requires zh)
      3) Choose-correct-sentence MCQ (uses EN)
      4) Sentence True/False (uses EN)
    """
    ensure_dir(out_dir)

    vocab = spec.get("vocab", []) or []
    qa_list = spec.get("qa", []) or []
    sentences_raw = spec.get("sentences", []) or []

    # normalize
    sentences_en = _as_sentence_strings(sentences_raw)

    vocab_en = [str(v.get("en") or "").strip() for v in vocab if (v.get("en") or "").strip()]
    vocab_zh = [str(v.get("zh") or "").strip() for v in vocab if (v.get("zh") or "").strip()]
    vocab_zh = _dedupe_preserve(vocab_zh)

    distractors = vocab_zh[:] if vocab_zh else ["选项A", "选项B", "选项C"]

    pools: List[Dict[str, Any]] = []

    # 1) Q&A
    for qa in qa_list:
        q = str(qa.get("q") or "").strip()
        a = str(qa.get("a") or "").strip()
        if q and a:
            pools.append(_make_mcq_from_qa({"q": q, "a": a}, distractors))

    # 2) Vocab meaning EN->ZH
    vocab_pairs: List[Tuple[str, str]] = []
    for v in vocab:
        en = str(v.get("en") or "").strip()
        zh = str(v.get("zh") or "").strip()
        if en and zh:
            vocab_pairs.append((en, zh))

    random.shuffle(vocab_pairs)
    for en, zh in vocab_pairs:
        pools.append(
            _make_mcq(
                q=f"What is the meaning of: {en} ?",
                correct=zh,
                pool=vocab_zh,
                n_choices=4,
            )
        )

    # 3) Sentence MCQ (choose correct sentence)
    pools.extend(_choose_correct_sentence_mcq(sentences_en))

    # 4) Sentence T/F
    pools.extend(_sentence_true_false(sentences_en))

    # If still empty, produce a minimal fallback question instead of blank quiz.json
    if not pools:
        if vocab_en:
            pools.append(
                _make_mcq(
                    q=f"Which word did we learn today?",
                    correct=vocab_en[0],
                    pool=vocab_en[1:] + ["apple", "book", "cat"],
                    n_choices=4,
                )
            )
        else:
            pools.append(
                {
                    "type": "mcq",
                    "q": "Ready to start practice?",
                    "choices": ["Yes", "No", "Maybe", "Later"],
                    "answer_index": 0,
                }
            )

    random.shuffle(pools)
    questions = pools[: max(0, int(n_questions))]

    quiz = {
        "title": f"{spec.get('title', 'Quiz')}",
        "questions": questions,
    }

    out_path = Path(out_dir) / "quiz.json"
    out_path.write_text(json.dumps(quiz, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path
