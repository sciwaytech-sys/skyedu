from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict

@dataclass
class VocabItem:
    en: str
    zh: str

@dataclass
class QAItem:
    q: str
    a: str

def parse_homework_text(text: str) -> Dict:
    """
    Supported format:

    TITLE: ...
    VOCAB:
    Table - 桌子
    Lamp - 灯
    SENTENCES:
    The lamp is on the table.
    Q&A:
    Q: Where is the rug?
    A: It is under the table.
    """
    lines = [ln.strip() for ln in text.splitlines()]
    title = "Homework"
    section = None

    vocab: List[VocabItem] = []
    sentences: List[str] = []
    qa: List[QAItem] = []

    current_q = None

    for ln in lines:
        if not ln:
            continue

        if ln.upper().startswith("TITLE:"):
            title = ln.split(":", 1)[1].strip()
            continue

        upper = ln.upper()
        if upper == "VOCAB:":
            section = "vocab"; continue
        if upper == "SENTENCES:":
            section = "sentences"; continue
        if upper in ("Q&A:", "QA:"):
            section = "qa"; continue

        if section == "vocab":
            # Expect: English - Chinese
            if "-" in ln:
                left, right = ln.split("-", 1)
                en = left.strip()
                zh = right.strip()
                if en:
                    vocab.append(VocabItem(en=en, zh=zh))
            continue

        if section == "sentences":
            sentences.append(ln)
            continue

        if section == "qa":
            if ln.startswith("Q:"):
                current_q = ln[2:].strip()
            elif ln.startswith("A:") and current_q:
                a = ln[2:].strip()
                qa.append(QAItem(q=current_q, a=a))
                current_q = None
            continue

    spec = {
        "title": title,
        "vocab": [{"en": v.en, "zh": v.zh} for v in vocab],
        "sentences": sentences,
        "qa": [{"q": x.q, "a": x.a} for x in qa],
    }
    return spec
