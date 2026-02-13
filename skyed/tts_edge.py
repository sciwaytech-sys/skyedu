from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any, Dict, List

import edge_tts  # type: ignore


def _slugify(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^a-z0-9_\-]+", "", s)
    return s or "item"


def _rate_string(rate_percent: int) -> str:
    return f"+{rate_percent}%" if rate_percent >= 0 else f"{rate_percent}%"


async def _synth_to_mp3(text: str, voice: str, rate: str, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    comm = edge_tts.Communicate(text, voice=voice, rate=rate)
    await comm.save(str(out_path))


def generate_audio(spec: Dict[str, Any], out_dir: Path) -> List[Path]:
    """
    Generate EN+ZH audio for:
      - vocab: audio/en/<stem>.mp3 and audio/zh/<stem>.mp3
      - sentences: audio/en/sent_<stem>.mp3 and audio/zh/sent_<stem>.mp3 (only if text exists)

    Returns list of output Paths (both languages).
    """
    out_dir = Path(out_dir)
    en_dir = out_dir / "en"
    zh_dir = out_dir / "zh"
    en_dir.mkdir(parents=True, exist_ok=True)
    zh_dir.mkdir(parents=True, exist_ok=True)

    voice_en = (os.environ.get("SKYED_VOICE_EN") or "en-US-JennyNeural").strip()
    voice_zh = (os.environ.get("SKYED_VOICE_ZH") or "zh-CN-XiaoxiaoNeural").strip()

    rate_env = (os.environ.get("SKYED_TTS_RATE") or "-10%").strip()
    if rate_env.endswith("%"):
        rate = rate_env
    else:
        try:
            rate = _rate_string(int(rate_env))
        except Exception:
            rate = "-10%"

    outputs: List[Path] = []
    coros: List[asyncio.Future] = []

    # ---- Vocab audio ----
    vocab = spec.get("vocab", []) or []
    for v in vocab:
        en = (v.get("en") or "").strip()
        zh = (v.get("zh") or "").strip()
        if not en:
            continue

        stem = _slugify(en)

        p_en = en_dir / f"{stem}.mp3"
        outputs.append(p_en)
        coros.append(_synth_to_mp3(en, voice_en, rate, p_en))

        # Always generate a ZH track too; if zh missing, fallback to EN spoken with ZH voice
        zh_text = zh if zh else en
        p_zh = zh_dir / f"{stem}.mp3"
        outputs.append(p_zh)
        coros.append(_synth_to_mp3(zh_text, voice_zh, rate, p_zh))

    # ---- Sentence audio (paired EN/ZH) ----
    sentences = spec.get("sentences", []) or []
    for s in sentences:
        en_s = (s.get("en") or "").strip()
        zh_s = (s.get("zh") or "").strip()

        base = en_s or zh_s
        if not base:
            continue

        # IMPORTANT: run_pipeline.py expects sent_<stem>.mp3 where stem is derived there.
        # Here we do NOT hash; we slugify base text. run_pipeline.py uses hashed stems,
        # so it will still upload correctly because upload_media uses f.stem as key.
        # To keep stable mapping, run_pipeline.py already calculates the stem and then
        # looks for sent_<stem>.mp3 under audio/en and audio/zh.
        #
        # Therefore: we must generate stems the SAME WAY as run_pipeline.py.
        # We read the stem from env if provided by pipeline; otherwise slugify(base).
        #
        # Pipeline sets only SKYED_TTS_* env; it doesn't pass stems per sentence.
        # So we must mirror pipeline stem logic here to match it:
        #   sent_<slug60>_<sha1_10>.mp3
        #
        # We'll implement the same: slug(base[:60]) + sha1(base)[:10]
        import hashlib

        short = _slugify(base[:60] if len(base) > 60 else base)
        h = hashlib.sha1(base.encode("utf-8")).hexdigest()[:10]
        stem = f"{short}_{h}" if short else h

        if en_s:
            p_en = en_dir / f"sent_{stem}.mp3"
            outputs.append(p_en)
            coros.append(_synth_to_mp3(en_s, voice_en, rate, p_en))

        if zh_s:
            p_zh = zh_dir / f"sent_{stem}.mp3"
            outputs.append(p_zh)
            coros.append(_synth_to_mp3(zh_s, voice_zh, rate, p_zh))

    async def _runner() -> None:
        if coros:
            # gather accepts coroutines directly
            await asyncio.gather(*coros)

    # Run once; this creates and manages the event loop correctly
    asyncio.run(_runner())

    return outputs
