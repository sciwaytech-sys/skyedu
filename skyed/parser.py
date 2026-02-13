from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import List, Dict, Optional


@dataclass
class VocabItem:
    en: str
    zh: str = ""


@dataclass
class QAItem:
    q: str
    a: str


def _norm_line(s: str) -> str:
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("＃", "#").replace("：", ":")
    s = s.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "").replace("\ufeff", "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _has_zh(s: str) -> bool:
    # CJK Unified Ideographs
    return bool(re.search(r"[\u4e00-\u9fff]", s or ""))


def _split_vocab_tokens(s: str) -> List[str]:
    parts = [p.strip() for p in (s or "").split(",")]
    return [p for p in parts if p]


def _parse_vocab_token(tok: str) -> VocabItem:
    t = (tok or "").strip()
    if not t:
        return VocabItem(en="", zh="")

    # table - 桌子
    if "-" in t:
        left, right = t.split("-", 1)
        en = left.strip()
        zh = right.strip()
        return VocabItem(en=en, zh=zh)

    # table(桌子) / table（桌子）
    m = re.match(r"^(.*?)\s*[（(]\s*(.*?)\s*[）)]\s*$", t)
    if m:
        en = (m.group(1) or "").strip()
        zh = (m.group(2) or "").strip()
        return VocabItem(en=en, zh=zh)

    return VocabItem(en=t, zh="")


def parse_homework_text(text: str) -> Dict:
    raw_lines = (text or "").splitlines()
    lines = [_norm_line(ln) for ln in raw_lines]

    title = "Homework"
    vocab: List[VocabItem] = []
    sentences: List[Dict[str, str]] = []  # [{"en":..., "zh":...}, ...]
    qa: List[QAItem] = []

    section: Optional[str] = None
    current_q: Optional[str] = None

    # sentence pairing state
    pending_en: Optional[str] = None

    re_name = re.compile(r"^\s*#?\s*Name\s+(.*)$", re.IGNORECASE)
    re_vocab = re.compile(r"^\s*#?\s*Vocabulary", re.IGNORECASE)
    re_sent = re.compile(r"^\s*#?\s*Sentences", re.IGNORECASE)
    re_qa_head = re.compile(r"^\s*#?\s*Questions\s+and\s+answers", re.IGNORECASE)

    re_q = re.compile(r"^\s*Q\d*\s*:\s*(.*)$", re.IGNORECASE)
    re_a = re.compile(r"^\s*A\d*\s*:\s*(.*)$", re.IGNORECASE)

    re_zh_prefix = re.compile(r"^(CN|ZH)\s*:\s*", re.IGNORECASE)

    def flush_pending_en() -> None:
        nonlocal pending_en
        if pending_en:
            sentences.append({"en": pending_en, "zh": ""})
            pending_en = None

    for ln in lines:
        if not ln:
            continue

        m = re_name.match(ln)
        if m:
            title = (m.group(1) or "").strip()
            section = None
            current_q = None
            flush_pending_en()
            continue

        if re_vocab.match(ln):
            section = "vocab"
            current_q = None
            flush_pending_en()
            if ":" in ln:
                after = ln.split(":", 1)[1].strip()
                for tok in _split_vocab_tokens(after):
                    it = _parse_vocab_token(tok)
                    if it.en:
                        vocab.append(it)
            continue

        if re_sent.match(ln):
            section = "sentences"
            current_q = None
            flush_pending_en()
            continue

        if re_qa_head.match(ln):
            section = "qa"
            current_q = None
            flush_pending_en()
            continue

        if section == "vocab":
            work = ln
            if ":" in work:
                work = work.split(":", 1)[1].strip()
            toks = _split_vocab_tokens(work) if "," in work else [work]
            for tok in toks:
                it = _parse_vocab_token(tok)
                if it.en:
                    vocab.append(it)
            continue

        if section == "sentences":
            # Support:
            #   EN line then ZH line
            #   CN: xxx / ZH: xxx
            if re_zh_prefix.match(ln):
                zh = re_zh_prefix.sub("", ln).strip()
                if pending_en:
                    sentences.append({"en": pending_en, "zh": zh})
                    pending_en = None
                else:
                    # ZH without EN -> store standalone
                    sentences.append({"en": "", "zh": zh})
                continue

            if _has_zh(ln):
                # If a Chinese line follows an EN sentence, pair them
                if pending_en:
                    sentences.append({"en": pending_en, "zh": ln})
                    pending_en = None
                else:
                    sentences.append({"en": "", "zh": ln})
                continue

            # Otherwise treat as EN sentence
            # If there was a pending EN with no ZH, flush it first
            if pending_en:
                sentences.append({"en": pending_en, "zh": ""})
            pending_en = ln
            continue

        if section == "qa":
            qm = re_q.match(ln)
            if qm:
                current_q = (qm.group(1) or "").strip()
                continue
            am = re_a.match(ln)
            if am and current_q:
                ans = (am.group(1) or "").strip()
                qa.append(QAItem(q=current_q, a=ans))
                current_q = None
            continue

    flush_pending_en()

    # dedup vocab preserve order
    seen = set()
    dedup_vocab: List[VocabItem] = []
    for v in vocab:
        key = (v.en.lower(), v.zh)
        if key in seen:
            continue
        seen.add(key)
        dedup_vocab.append(v)

    # normalize sentences list (remove empties)
    norm_sents: List[Dict[str, str]] = []
    for s in sentences:
        en = (s.get("en") or "").strip()
        zh = (s.get("zh") or "").strip()
        if not en and not zh:
            continue
        norm_sents.append({"en": en, "zh": zh})

    return {
        "title": title,
        "vocab": [{"en": v.en, "zh": v.zh} for v in dedup_vocab],
        "sentences": norm_sents,
        "qa": [{"q": x.q, "a": x.a} for x in qa],
    }
