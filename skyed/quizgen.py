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
                # If EN is missing but ZH exists, you can choose to skip or include ZH.
                # For now we prefer EN-only to keep MCQ readable.
                if en:
                    out.append(en)
    return out


def _make_mcq(q: str, correct: str, pool: List[str], n_choices: int = 4) -> Dict[str, Any]:
    choices = [correct]
    pool2 = [x for x in pool if x and x != correct]
    random.shuffle(pool2)
    for x in pool2:
        if len(choices) >= n_choices:
            break
        choices.append(x)
    while len(choices) < n_choices:
        choices.append("—")
    random.shuffle(choices)
    return {
        "type": "mcq",
        "q": q,
        "choices": choices,
        "answer_index": choices.index(correct),
    }


def _make_mcq_from_qa(qa: Dict[str, Any], distractors: List[str]) -> Dict[str, Any]:
    return _make_mcq(q=str(qa["q"]), correct=str(qa["a"]), pool=distractors, n_choices=4)


def _choose_correct_sentence_mcq(sentences: List[str], vocab_en: List[str]) -> List[Dict[str, Any]]:
    """
    For each sentence, build an MCQ where correct option is the original sentence,
    distractors are other sentences or lightly perturbed variants.
    """
    sents = [s.strip() for s in (sentences or []) if isinstance(s, str) and s.strip()]
    if len(sents) < 2:
        return []

    pool = sents[:]
    out: List[Dict[str, Any]] = []

    for s in sents:
        distract = [x for x in pool if x != s]
        random.shuffle(distract)
        # If not enough distractors, add generic
        while len(distract) < 3:
            distract.append("I see a ____.")
        q = "Choose the correct sentence:"
        out.append(_make_mcq(q=q, correct=s, pool=distract, n_choices=4))

    return out


def _sentence_true_false(sentences: List[str]) -> List[Dict[str, Any]]:
    """
    Very simple: True/False items with True only (MVP).
    """
    sents = [s.strip() for s in (sentences or []) if isinstance(s, str) and s.strip()]
    out: List[Dict[str, Any]] = []
    for s in sents:
        out.append({"type": "tf", "q": f"True or False: {s}", "answer_bool": True})
    return out


def generate_quiz(spec: Dict[str, Any], out_dir: Path, n_questions: int = 8) -> Path:
    """
    Output: quiz.json in out_dir

    Pools (in priority):
      1) Q&A MCQ (always works)
      2) Vocab EN->ZH meaning MCQ (requires zh)
      3) Choose-correct-sentence MCQ (works with sentence dicts too; uses EN)
      4) Sentence True/False (works with sentence dicts too; uses EN)
    """
    ensure_dir(out_dir)

    vocab = spec.get("vocab", []) or []
    qa_list = spec.get("qa", []) or []
    sentences_raw = spec.get("sentences", []) or []

    # normalize
    sentences_en = _as_sentence_strings(sentences_raw)

    vocab_en = [str(v.get("en") or "").strip() for v in vocab if (v.get("en") or "").strip()]
    vocab_zh = [str(v.get("zh") or "").strip() for v in vocab if (v.get("zh") or "").strip()]

    distractors = [z for z in vocab_zh if z] or ["—", "—", "—"]

    pools: List[Dict[str, Any]] = []

    # 1) Q&A
    for qa in qa_list:
        if qa.get("q") and qa.get("a"):
            pools.append(_make_mcq_from_qa(qa, distractors))

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
    pools.extend(_choose_correct_sentence_mcq(sentences_en, vocab_en))

    # 4) Sentence T/F
    pools.extend(_sentence_true_false(sentences_en))

    # choose n_questions
    random.shuffle(pools)
    questions = pools[: max(0, int(n_questions))]

    quiz = {
        "title": f"{spec.get('title', 'Quiz')}",
        "questions": questions,
    }

    out_path = Path(out_dir) / "quiz.json"
    out_path.write_text(json.dumps(quiz, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path
