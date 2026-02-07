# skyed/tts_edge.py
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

import edge_tts

DEFAULT_VOICE_EN = "en-US-JennyNeural"
DEFAULT_VOICE_ZH = "zh-CN-XiaoxiaoNeural"


def _ensure_parent(out_path: Path) -> None:
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)


def _slugify(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"\s+", "_", t)
    t = re.sub(r"[^a-z0-9_\-]+", "", t)
    return t or "item"


def _normalize_vocab(spec: Dict[str, Any]) -> List[Tuple[str, str]]:
    """
    Accept multiple shapes:
      spec["vocab"] / spec["vocabulary"] / spec["words"]:
        - list of {"en": "...", "zh": "..."}
        - list of ("en","zh")
        - list of "word" (zh empty)
    """
    items = spec.get("vocab") or spec.get("vocabulary") or spec.get("words") or []
    out: List[Tuple[str, str]] = []
    if not isinstance(items, list):
        return out

    for it in items:
        if isinstance(it, dict):
            en = str(it.get("en") or it.get("word") or it.get("english") or "").strip()
            zh = str(it.get("zh") or it.get("cn") or it.get("chinese") or "").strip()
            if en:
                out.append((en, zh))
        elif isinstance(it, (list, tuple)) and len(it) >= 1:
            en = str(it[0]).strip()
            zh = str(it[1]).strip() if len(it) > 1 else ""
            if en:
                out.append((en, zh))
        elif isinstance(it, str):
            en = it.strip()
            if en:
                out.append((en, ""))
    return out


def _normalize_sentences(spec: Dict[str, Any]) -> List[Tuple[str, str]]:
    """
    Optional: tries to extract sentence pairs if present.
    Supports:
      spec["sentences"] or spec["phrases"] : list of dict/en-zh pairs or strings
    """
    items = spec.get("sentences") or spec.get("phrases") or spec.get("expressions") or []
    out: List[Tuple[str, str]] = []
    if not isinstance(items, list):
        return out

    for it in items:
        if isinstance(it, dict):
            en = str(it.get("en") or it.get("english") or "").strip()
            zh = str(it.get("zh") or it.get("cn") or it.get("chinese") or "").strip()
            if en:
                out.append((en, zh))
        elif isinstance(it, (list, tuple)) and len(it) >= 1:
            en = str(it[0]).strip()
            zh = str(it[1]).strip() if len(it) > 1 else ""
            if en:
                out.append((en, zh))
        elif isinstance(it, str):
            en = it.strip()
            if en:
                out.append((en, ""))
    return out


def _add_natural_punctuation(text: str, lang: str) -> str:
    """
    Helps slow down and sound more natural if input is short/unnatural.
    """
    t = (text or "").strip()
    if not t:
        return t
    # If it looks like a single word without punctuation, add a period.
    if all(ch.isalnum() or ch in "-' " for ch in t) and len(t.split()) <= 4:
        return t + "."
    # For Chinese, ensure ending punctuation.
    if lang == "zh" and t[-1] not in "。！？":
        return t + "。"
    if lang == "en" and t[-1] not in ".!?":
        return t + "."
    return t


async def _speak_to_mp3(text: str, voice: str, rate: str, out_mp3: Path) -> None:
    _ensure_parent(out_mp3)
    communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate)
    await communicate.save(str(out_mp3))


def tts_en(
    text: str,
    out_mp3: Path,
    *,
    voice: str = DEFAULT_VOICE_EN,
    rate: str = "-10%",
) -> Path:
    text = _add_natural_punctuation(text, "en")
    asyncio.run(_speak_to_mp3(text=text, voice=voice, rate=rate, out_mp3=Path(out_mp3)))
    return Path(out_mp3)


def tts_zh(
    text: str,
    out_mp3: Path,
    *,
    voice: str = DEFAULT_VOICE_ZH,
    rate: str = "-10%",
) -> Path:
    text = _add_natural_punctuation(text, "zh")
    asyncio.run(_speak_to_mp3(text=text, voice=voice, rate=rate, out_mp3=Path(out_mp3)))
    return Path(out_mp3)


def generate_audio(
    arg1: Union[Dict[str, Any], str],
    arg2: Union[Path, str, None] = None,
    out_dir: Union[Path, str, None] = None,
    slug: Optional[str] = None,
    *,
    rate: str = "-10%",
    voice_en: str = DEFAULT_VOICE_EN,
    voice_zh: str = DEFAULT_VOICE_ZH,
) -> List[Path]:
    """
    Backward-compatible generator.

    Mode A (your pipeline expects this):
        generate_audio(spec: dict, out_dir: Path) -> List[Path]

    Mode B (alternate / legacy style):
        generate_audio(en_text: str, zh_text: str, out_dir: Path, slug: str) -> List[Path]

    Output:
        Creates:
          out_dir/en/<slug>.mp3
          out_dir/zh/<slug>.mp3

        Returns list of created file Paths.
    """
    # --------------------
    # Mode A: generate_audio(spec, out_dir)
    # --------------------
    if isinstance(arg1, dict) and arg2 is not None and out_dir is None:
        spec = arg1
        base_out = Path(arg2)
        (base_out / "en").mkdir(parents=True, exist_ok=True)
        (base_out / "zh").mkdir(parents=True, exist_ok=True)

        created: List[Path] = []

        # 1) vocab audio
        for en, zh in _normalize_vocab(spec):
            s = _slugify(en)
            en_path = base_out / "en" / f"{s}.mp3"
            zh_path = base_out / "zh" / f"{s}.mp3"
            tts_en(en, en_path, voice=voice_en, rate=rate)
            if (zh or "").strip():
                tts_zh(zh, zh_path, voice=voice_zh, rate=rate)
                created.extend([en_path, zh_path])
            else:
                created.append(en_path)

        # 2) sentences/phrases audio (optional, if present)
        for en, zh in _normalize_sentences(spec):
            s = _slugify(en)[:60]
            en_path = base_out / "en" / f"sent_{s}.mp3"
            zh_path = base_out / "zh" / f"sent_{s}.mp3"
            tts_en(en, en_path, voice=voice_en, rate=rate)
            if (zh or "").strip():
                tts_zh(zh, zh_path, voice=voice_zh, rate=rate)
                created.extend([en_path, zh_path])
            else:
                created.append(en_path)

        return created

    # --------------------
    # Mode B: generate_audio(en_text, zh_text, out_dir, slug)
    # --------------------
    if isinstance(arg1, str):
        en_text = arg1
        zh_text = str(arg2 or "")
        if out_dir is None or slug is None:
            raise TypeError("generate_audio(en, zh, out_dir, slug) requires out_dir and slug.")
        base_out = Path(out_dir)
        s = _slugify(slug)

        en_path = base_out / "en" / f"{s}.mp3"
        zh_path = base_out / "zh" / f"{s}.mp3"
        (base_out / "en").mkdir(parents=True, exist_ok=True)
        (base_out / "zh").mkdir(parents=True, exist_ok=True)

        created: List[Path] = []
        tts_en(en_text, en_path, voice=voice_en, rate=rate)
        created.append(en_path)

        if (zh_text or "").strip():
            tts_zh(zh_text, zh_path, voice=voice_zh, rate=rate)
            created.append(zh_path)

        return created

    raise TypeError("generate_audio() invalid arguments. Expected (spec, out_dir) or (en, zh, out_dir, slug).")


async def list_voices(search: Optional[str] = None) -> List[str]:
    voices = await edge_tts.list_voices()
    names: List[str] = []
    for v in voices:
        name = v.get("ShortName") or ""
        if not search or search.lower() in name.lower():
            names.append(name)
    return sorted(set(names))
