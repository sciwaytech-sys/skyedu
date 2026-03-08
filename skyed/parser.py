from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass
class VocabItem:
    en: str
    zh: str = ""
    pos: str = ""  # noun|verb|adjective


@dataclass
class QAItem:
    q: str
    a: str


SECTION_ALIASES = {
    "title": ["name", "title", "lesson", "homework", "标题", "作业"],
    "vocab": ["vocabulary", "vocab", "words", "word bank", "theme words", "词汇", "单词"],
    "sentences": ["sentences", "sentence pattern", "useful sentences", "句型", "重点句型"],
    "qa": ["questions and answers", "q&a", "qa", "问答"],
    "tags": ["tags", "tag", "标签"],
}

ZH_FALLBACK = {
    "table": "桌子",
    "chair": "椅子",
    "lamp": "灯",
    "box": "盒子",
    "picture": "图片",
    "big": "大的",
    "small": "小的",
    "book": "书",
    "pen": "钢笔",
    "pencil": "铅笔",
    "bag": "书包",
    "desk": "课桌",
    "door": "门",
    "window": "窗户",
    "run": "跑",
    "jump": "跳",
    "read": "读",
    "write": "写",
}


def _norm_line(s: str) -> str:
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("＃", "#").replace("：", ":")
    s = s.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "").replace("\ufeff", "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _has_zh(s: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", s or ""))


def _split_vocab_tokens(s: str) -> List[str]:
    parts = [p.strip() for p in re.split(r"[,，、]", s or "")]
    return [p for p in parts if p]


def _norm_pos(p: str) -> str:
    p = (p or "").strip().lower()
    if p in ("n", "noun"):
        return "noun"
    if p in ("v", "verb"):
        return "verb"
    if p in ("adj", "adjective"):
        return "adjective"
    return ""


def _match_section(line: str, key: str) -> Tuple[bool, str]:
    aliases = SECTION_ALIASES[key]
    for alias in aliases:
        m = re.match(rf"^\s*#?\s*{re.escape(alias)}\s*[:：]?\s*(.*)$", line, flags=re.IGNORECASE)
        if m:
            return True, (m.group(1) or "").strip()
    return False, ""


def _parse_vocab_token(tok: str) -> VocabItem:
    t = (tok or "").strip()
    if not t:
        return VocabItem(en="", zh="")
    en_part = t
    zh_part = ""
    pos = ""

    m = re.match(r"^(n|noun|v|verb|adj|adjective)\s*:\s*(.+)$", en_part, flags=re.IGNORECASE)
    if m:
        pos = _norm_pos(m.group(1))
        en_part = (m.group(2) or "").strip()

    if not pos:
        m = re.match(r"^(.+?)/(n|noun|v|verb|adj|adjective)$", en_part, flags=re.IGNORECASE)
        if m:
            en_part = (m.group(1) or "").strip()
            pos = _norm_pos(m.group(2))

    if not pos:
        m = re.match(r"^(.+?)\s*[（(]\s*(n|noun|v|verb|adj|adjective)\s*[）)]\s*$", en_part, flags=re.IGNORECASE)
        if m:
            en_part = (m.group(1) or "").strip()
            pos = _norm_pos(m.group(2))

    if "-" in en_part:
        left, right = en_part.split("-", 1)
        if _has_zh(right):
            en_part, zh_part = left.strip(), right.strip()

    m = re.match(r"^(.*?)\s*[（(]\s*(.*?)\s*[）)]\s*$", en_part)
    if m and _has_zh(m.group(2) or ""):
        en_part = (m.group(1) or "").strip()
        zh_part = (m.group(2) or "").strip()

    return VocabItem(en=en_part.strip(), zh=zh_part.strip(), pos=pos)


def _infer_pos(en: str, sentences_en: List[str]) -> str:
    w = (en or "").strip().lower()
    if not w:
        return ""
    for s in sentences_en:
        t = (s or "").lower().replace("’", "'")
        if re.search(rf"\b(it\s*'?s|it\s+is|is|are|am)\s+{re.escape(w)}\b", t):
            return "adjective"
    for s in sentences_en:
        t = (s or "").lower().replace("’", "'")
        if re.search(rf"\b(can|to|will|want\s+to|like\s+to|likes\s+to)\s+{re.escape(w)}\b", t):
            return "verb"
        if re.search(rf"\b(i|we|you|they|he|she)\s+{re.escape(w)}\b", t):
            return "verb"
    return "noun"


def _backfill_vocab(vocab: List[VocabItem], sentences: List[Dict[str, str]]) -> None:
    sentences_en = []
    for s in sentences:
        en = (s.get("en") or "").strip()
        if en:
            sentences_en.append(en)
    for item in vocab:
        if not item.pos:
            item.pos = _infer_pos(item.en, sentences_en)
        if not item.zh:
            item.zh = ZH_FALLBACK.get(item.en.strip().lower(), "")


def parse_homework_text(text: str) -> Dict:
    raw_lines = (text or "").splitlines()
    lines = [_norm_line(ln) for ln in raw_lines]

    title = "Homework"
    tags: List[str] = []
    vocab: List[VocabItem] = []
    sentences: List[Dict[str, str]] = []
    qa: List[QAItem] = []

    section: Optional[str] = None
    current_q: Optional[str] = None
    pending_en: Optional[str] = None

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

        matched, after = _match_section(ln, "title")
        if matched:
            if after:
                title = after
            section = None
            current_q = None
            flush_pending_en()
            continue

        matched, after = _match_section(ln, "tags")
        if matched:
            tags = [x.strip() for x in re.split(r"[,，、]", after) if x.strip()]
            section = None
            continue

        matched, after = _match_section(ln, "vocab")
        if matched:
            section = "vocab"
            current_q = None
            flush_pending_en()
            if after:
                for tok in _split_vocab_tokens(after):
                    it = _parse_vocab_token(tok)
                    if it.en:
                        vocab.append(it)
            continue

        matched, after = _match_section(ln, "sentences")
        if matched:
            section = "sentences"
            current_q = None
            flush_pending_en()
            if after:
                pending_en = after
            continue

        matched, after = _match_section(ln, "qa")
        if matched:
            section = "qa"
            current_q = None
            flush_pending_en()
            continue

        if section == "vocab":
            work = ln
            if ":" in work and not re.match(r"^(n|noun|v|verb|adj|adjective)\s*:", work, flags=re.IGNORECASE):
                work = work.split(":", 1)[1].strip()
            toks = _split_vocab_tokens(work) if re.search(r"[,，、]", work) else [work]
            for tok in toks:
                it = _parse_vocab_token(tok)
                if it.en:
                    vocab.append(it)
            continue

        if section == "sentences":
            if re_zh_prefix.match(ln):
                zh = re_zh_prefix.sub("", ln).strip()
                if pending_en:
                    sentences.append({"en": pending_en, "zh": zh})
                    pending_en = None
                else:
                    sentences.append({"en": "", "zh": zh})
                continue
            if _has_zh(ln):
                if pending_en:
                    sentences.append({"en": pending_en, "zh": ln})
                    pending_en = None
                else:
                    sentences.append({"en": "", "zh": ln})
                continue
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
                qa.append(QAItem(q=current_q, a=(am.group(1) or "").strip()))
                current_q = None
            continue

    flush_pending_en()

    seen = set()
    dedup_vocab: List[VocabItem] = []
    for v in vocab:
        key = (v.en.lower(), v.zh, v.pos)
        if key in seen:
            continue
        seen.add(key)
        dedup_vocab.append(v)

    norm_sents: List[Dict[str, str]] = []
    for s in sentences:
        en = (s.get("en") or "").strip()
        zh = (s.get("zh") or "").strip()
        if en or zh:
            norm_sents.append({"en": en, "zh": zh})

    _backfill_vocab(dedup_vocab, norm_sents)

    return {
        "title": title,
        "tags": tags,
        "vocab": [{"en": v.en, "zh": v.zh, **({"pos": v.pos} if v.pos else {})} for v in dedup_vocab],
        "sentences": norm_sents,
        "qa": [{"q": x.q, "a": x.a} for x in qa],
    }
