from __future__ import annotations
from pathlib import Path
from typing import Dict, List
import json
import random
from .utils import ensure_dir

def _make_mcq_from_qa(qa: Dict, distractors: List[str]) -> Dict:
    correct = qa["a"]
    choices = [correct]
    # add distractors
    random.shuffle(distractors)
    for d in distractors:
        if d != correct and len(choices) < 4:
            choices.append(d)
    # if not enough, pad
    while len(choices) < 4:
        choices.append("I don't know.")
    random.shuffle(choices)
    return {
        "type": "mcq",
        "q": qa["q"],
        "choices": choices,
        "answer_index": choices.index(correct),
    }

def generate_quiz(spec: Dict, out_dir: Path, n_questions: int = 8) -> Path:
    """
    Output: quiz.json in out_dir
    Rules (MVP):
    - Prefer Q&A items as questions
    - Otherwise generate vocab meaning questions (EN->ZH)
    """
    ensure_dir(out_dir)

    questions: List[Dict] = []
    vocab = spec.get("vocab", [])
    qa_list = spec.get("qa", [])
    sentences = spec.get("sentences", [])

    # Build distractors from vocab zh or generic
    distractors = [v.get("zh", "").strip() for v in vocab if v.get("zh")]
    distractors = [d for d in distractors if d]

    # 1) Q&A → MCQ
    for qa in qa_list:
        if len(questions) >= n_questions:
            break
        if qa.get("q") and qa.get("a"):
            questions.append(_make_mcq_from_qa(qa, distractors))

    # 2) Vocab EN->ZH questions
    random.shuffle(vocab)
    for v in vocab:
        if len(questions) >= n_questions:
            break
        en = v.get("en", "").strip()
        zh = v.get("zh", "").strip()
        if not (en and zh):
            continue
        choices = [zh]
        pool = [x.get("zh", "").strip() for x in vocab if x.get("zh")]
        pool = [x for x in pool if x and x != zh]
        random.shuffle(pool)
        while len(choices) < 4 and pool:
            choices.append(pool.pop())
        while len(choices) < 4:
            choices.append("—")
        random.shuffle(choices)
        questions.append({
            "type": "mcq",
            "q": f"What is the meaning of: {en} ?",
            "choices": choices,
            "answer_index": choices.index(zh),
        })

    # 3) Sentence true/false (optional simple)
    for s in sentences[: max(0, n_questions - len(questions))]:
        if len(questions) >= n_questions:
            break
        s = s.strip()
        if not s:
            continue
        questions.append({
            "type": "tf",
            "q": f"True or False: {s}",
            "answer_bool": True
        })

    quiz = {
        "title": f"{spec.get('title', 'Quiz')}",
        "questions": questions
    }

    out_path = out_dir / "quiz.json"
    out_path.write_text(json.dumps(quiz, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path
