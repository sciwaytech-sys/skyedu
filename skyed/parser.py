from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass
class VocabItem:
    en: str
    zh: str = ""
    pos: str = ""


@dataclass
class QAItem:
    q: str
    a: str


@dataclass
class MCQItem:
    q: str
    choices: List[str]
    answer: str


SECTION_ALIASES = {
    "title": ["title", "lesson", "homework", "name", "标题", "作业"],
    "tags": ["tags", "tag", "标签"],
    "vocab": ["vocabulary", "vocab", "words", "word bank", "theme words", "词汇", "单词"],
    "sentences": ["sentences", "sentence pattern", "useful sentences", "句型", "重点句型"],
    "qa": ["questions and answers", "q&a", "qa", "问答"],
    "reading_title": ["reading title", "passage title", "reading heading"],
    "reading_text": ["reading text", "reading", "passage"],
    "listening_title": ["listening title", "audio title"],
    "listening_text": ["listening text", "listening script", "audio script"],
    "comp_questions": ["comprehension questions", "reading questions", "listening questions", "mcq questions"],
}

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
    "pron": "pronoun",
    "pronoun": "pronoun",
    "phr": "phrase",
    "phrase": "phrase",
    "expression": "phrase",
    "expr": "phrase",
    "question": "question_word",
    "question_word": "question_word",
    "question word": "question_word",
    "qword": "question_word",
    "word": "",
    "det": "determiner",
    "determiner": "determiner",
    "num": "number",
    "number": "number",
    "time": "time",
}

QUESTION_WORDS = {"what", "who", "where", "when", "which", "whose", "why", "how"}
PREPOSITIONS = {
    "in", "on", "under", "behind", "in front of", "next to", "between", "near", "by", "at",
    "into", "out of", "over", "below", "beside", "inside", "outside", "around",
}
PRONOUNS = {"i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us", "them", "my", "your", "his", "its", "our", "their"}
DETERMINERS = {"a", "an", "the", "this", "that", "these", "those", "some", "any", "my", "your", "his", "her", "our", "their"}
NUMBER_WORDS = {
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty",
    "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth",
}

HEADER_ONLY_RE = re.compile(r"^\s*#\s*(.+?)\s*$")
ZH_PREFIX_RE = re.compile(r"^(?:ZH|CN|中文|Chinese)\s*:\s*(.*)$", re.IGNORECASE)
EN_PREFIX_RE = re.compile(r"^(?:EN|English)\s*:\s*(.*)$", re.IGNORECASE)
Q_RE = re.compile(r"^\s*Q\d*\s*[:：]\s*(.*)$", re.IGNORECASE)
A_RE = re.compile(r"^\s*A\d*\s*[:：]\s*(.*)$", re.IGNORECASE)
POS_LEADING_RE = re.compile(
    r"^(n|noun|v|verb|adj|adjective|adv|adverb|prep|preposition|pron|pronoun|phr|phrase|expression|expr|question word|question_word|qword|question|word|det|determiner|num|number|time)\s*:\s*(.+)$",
    flags=re.IGNORECASE,
)

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
    "who": "谁",
    "where": "哪里",
    "what": "什么",
    "when": "什么时候",
    "in": "在……里面",
    "on": "在……上面",
    "under": "在……下面",
    "behind": "在……后面",
}


def _norm_line(s: str) -> str:
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("＃", "#").replace("：", ":")
    s = s.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "").replace("\ufeff", "")
    s = re.sub(r"[ \t]+", " ", s).strip()
    return s


def _has_zh(s: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", s or ""))


def _match_section(line: str, key: str) -> Tuple[bool, str]:
    aliases = SECTION_ALIASES[key]
    for alias in aliases:
        m = re.match(rf"^\s*#?\s*{re.escape(alias)}\s*[:：]?\s*(.*)$", line, flags=re.IGNORECASE)
        if m:
            return True, (m.group(1) or "").strip()
    return False, ""


def _normalize_pos(raw: str) -> str:
    return POS_ALIASES.get((raw or "").strip().lower(), "")


def _split_vocab_tokens(s: str) -> List[str]:
    if not s:
        return []
    parts = [p.strip() for p in re.split(r"[,，、]", s or "")]
    return [p for p in parts if p]


def _is_number_label(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    if re.fullmatch(r"\d+", t):
        return True
    return t in NUMBER_WORDS


def _infer_pos(en: str, sentences_en: List[str]) -> str:
    w = (en or "").strip().lower()
    if not w:
        return ""
    if w in QUESTION_WORDS:
        return "question_word"
    if w in PREPOSITIONS:
        return "preposition"
    if w in PRONOUNS:
        return "pronoun"
    if w in DETERMINERS:
        return "determiner"
    if _is_number_label(w):
        return "number"
    if " " in w:
        if any(p in w for p in PREPOSITIONS) and len(w.split()) <= 4:
            return "phrase"
    for s in sentences_en:
        t = (s or "").lower().replace("’", "'")
        if re.search(rf"\b(it\s*'?s|it\s+is|is|are|am|looks|feels)\s+{re.escape(w)}\b", t):
            return "adjective"
    for s in sentences_en:
        t = (s or "").lower().replace("’", "'")
        if re.search(rf"\b(can|to|will|want\s+to|like\s+to|likes\s+to|must|should)\s+{re.escape(w)}\b", t):
            return "verb"
        if re.search(rf"\b(i|we|you|they|he|she)\s+{re.escape(w)}\b", t):
            return "verb"
    return "phrase" if " " in w else "noun"


def _parse_vocab_token(tok: str) -> VocabItem:
    t = (tok or "").strip().strip(";。")
    if not t:
        return VocabItem(en="", zh="", pos="")

    pos = ""
    work = t
    m = POS_LEADING_RE.match(work)
    if m:
        pos = _normalize_pos(m.group(1))
        work = (m.group(2) or "").strip()

    zh = ""
    if "-" in work:
        left, right = work.split("-", 1)
        if _has_zh(right):
            work, zh = left.strip(), right.strip()
    if not zh:
        m = re.match(r"^(.*?)\s*[（(]\s*(.*?)\s*[）)]\s*$", work)
        if m and _has_zh(m.group(2) or ""):
            work, zh = (m.group(1) or "").strip(), (m.group(2) or "").strip()

    return VocabItem(en=work.strip(), zh=zh.strip(), pos=pos)


def _parse_comp_question_line(line: str) -> Optional[MCQItem]:
    txt = (line or "").strip()
    if not txt:
        return None
    txt = re.sub(r"^\d+[.)]\s*", "", txt)
    parts = [p.strip() for p in txt.split("/") if p.strip()]
    if len(parts) < 6:
        return None
    return MCQItem(q=parts[0], choices=parts[1:5], answer=parts[-1])


def _append_sentence(sentences: List[Dict[str, str]], en: str, zh: str) -> None:
    en = (en or "").strip()
    zh = (zh or "").strip()
    if en or zh:
        sentences.append({"en": en, "zh": zh})


def _backfill_vocab(vocab: List[VocabItem], sentences: List[Dict[str, str]]) -> None:
    sentences_en = [(s.get("en") or "").strip() for s in sentences if (s.get("en") or "").strip()]
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
    reading_title = ""
    reading_lines: List[str] = []
    listening_title = ""
    listening_lines: List[str] = []
    comp_questions: List[MCQItem] = []

    section: Optional[str] = None
    current_q: Optional[str] = None
    pending_en: Optional[str] = None
    pending_header_value: Optional[str] = None

    def flush_pending_en() -> None:
        nonlocal pending_en
        if pending_en:
            _append_sentence(sentences, pending_en, "")
            pending_en = None

    for ln in lines:
        if not ln:
            continue

        header_only = HEADER_ONLY_RE.match(ln)
        if header_only:
            header_name = header_only.group(1).strip().lower()
            matched_header = False
            for key, aliases in SECTION_ALIASES.items():
                if header_name in aliases:
                    section = key
                    pending_header_value = key if key in {"title", "tags", "reading_title", "listening_title"} else None
                    current_q = None
                    if key != "sentences":
                        flush_pending_en()
                    matched_header = True
                    break
            if matched_header:
                continue

        if pending_header_value:
            if pending_header_value == "title":
                title = ln
            elif pending_header_value == "tags":
                tags = [x.strip() for x in re.split(r"[,，、]", ln) if x.strip()]
            elif pending_header_value == "reading_title":
                reading_title = ln
            elif pending_header_value == "listening_title":
                listening_title = ln
            pending_header_value = None
            continue

        for key in SECTION_ALIASES.keys():
            matched, after = _match_section(ln, key)
            if not matched:
                continue
            section = key
            current_q = None
            if key != "sentences":
                flush_pending_en()
            if key == "title":
                if after:
                    title = after
                else:
                    pending_header_value = "title"
            elif key == "tags":
                if after:
                    tags = [x.strip() for x in re.split(r"[,，、]", after) if x.strip()]
                else:
                    pending_header_value = "tags"
            elif key == "vocab" and after:
                for tok in _split_vocab_tokens(after):
                    item = _parse_vocab_token(tok)
                    if item.en:
                        vocab.append(item)
            elif key == "sentences" and after:
                pending_en = after
            elif key == "reading_title":
                if after:
                    reading_title = after
                else:
                    pending_header_value = "reading_title"
            elif key == "reading_text" and after:
                reading_lines.append(after)
            elif key == "listening_title":
                if after:
                    listening_title = after
                else:
                    pending_header_value = "listening_title"
            elif key == "listening_text" and after:
                listening_lines.append(after)
            elif key == "comp_questions" and after:
                item = _parse_comp_question_line(after)
                if item:
                    comp_questions.append(item)
            break
        else:
            if section == "vocab":
                toks = _split_vocab_tokens(ln) if re.search(r"[,，、]", ln) else [ln]
                for tok in toks:
                    item = _parse_vocab_token(tok)
                    if item.en:
                        vocab.append(item)
                continue

            if section == "sentences":
                m_en = EN_PREFIX_RE.match(ln)
                if m_en:
                    if pending_en:
                        _append_sentence(sentences, pending_en, "")
                    pending_en = (m_en.group(1) or "").strip()
                    continue
                m_zh = ZH_PREFIX_RE.match(ln)
                if m_zh:
                    zh = (m_zh.group(1) or "").strip()
                    if pending_en:
                        _append_sentence(sentences, pending_en, zh)
                        pending_en = None
                    else:
                        _append_sentence(sentences, "", zh)
                    continue
                if _has_zh(ln):
                    if pending_en:
                        _append_sentence(sentences, pending_en, ln)
                        pending_en = None
                    else:
                        _append_sentence(sentences, "", ln)
                    continue
                if pending_en:
                    _append_sentence(sentences, pending_en, "")
                pending_en = ln
                continue

            if section == "qa":
                qm = Q_RE.match(ln)
                if qm:
                    current_q = (qm.group(1) or "").strip()
                    continue
                am = A_RE.match(ln)
                if am and current_q:
                    qa.append(QAItem(q=current_q, a=(am.group(1) or "").strip()))
                    current_q = None
                continue

            if section == "reading_text":
                reading_lines.append(ln)
                continue
            if section == "listening_text":
                listening_lines.append(ln)
                continue
            if section == "comp_questions":
                item = _parse_comp_question_line(ln)
                if item:
                    comp_questions.append(item)
                continue

    flush_pending_en()

    dedup_vocab: List[VocabItem] = []
    seen_vocab = set()
    for v in vocab:
        key = (v.en.strip().lower(), v.zh.strip(), v.pos.strip())
        if not v.en or key in seen_vocab:
            continue
        seen_vocab.add(key)
        dedup_vocab.append(v)

    norm_sents: List[Dict[str, str]] = []
    seen_sent = set()
    for s in sentences:
        en = (s.get("en") or "").strip()
        zh = (s.get("zh") or "").strip()
        key = (en, zh)
        if (en or zh) and key not in seen_sent:
            seen_sent.add(key)
            norm_sents.append({"en": en, "zh": zh})

    _backfill_vocab(dedup_vocab, norm_sents)

    return {
        "title": title,
        "tags": tags,
        "vocab": [{"en": v.en, "zh": v.zh, **({"pos": v.pos} if v.pos else {})} for v in dedup_vocab],
        "sentences": norm_sents,
        "qa": [{"q": x.q, "a": x.a} for x in qa],
        "reading_block": {"title": reading_title, "text": "\n".join(reading_lines).strip()},
        "listening_block": {"title": listening_title, "text": "\n".join(listening_lines).strip()},
        "comprehension_questions": [{"q": x.q, "choices": x.choices, "answer": x.answer} for x in comp_questions],
    }
