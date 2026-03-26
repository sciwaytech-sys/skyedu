from __future__ import annotations

import asyncio
import hashlib
import os
import random
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import edge_tts  # type: ignore
except Exception:  # pragma: no cover
    edge_tts = None


def _tts_log(msg: str) -> None:
    print(msg, flush=True)


def _slugify(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^a-z0-9_\-]+", "", s)
    return s or "item"


def _rate_string(rate_percent: int) -> str:
    return f"+{rate_percent}%" if rate_percent >= 0 else f"{rate_percent}%"


async def _synth_to_mp3(
    text: str,
    voice: str,
    rate: str,
    out_path: Path,
    *,
    max_retries: int = 5,
    base_sleep: float = 1.25,
    semaphore: Optional[asyncio.Semaphore] = None,
) -> Tuple[bool, str]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if edge_tts is None:
        return False, "edge-tts is not installed"
    if out_path.exists() and out_path.stat().st_size > 1024:
        return True, ""

    if semaphore is None:
        semaphore = asyncio.Semaphore(3)

    last_err = ""
    async with semaphore:
        for attempt in range(1, max_retries + 1):
            try:
                comm = edge_tts.Communicate(text, voice=voice, rate=rate)
                await comm.save(str(out_path))
                if out_path.exists() and out_path.stat().st_size > 1024:
                    return True, ""
                last_err = "file_not_written_or_too_small"
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"

            try:
                if out_path.exists() and out_path.stat().st_size < 1024:
                    out_path.unlink(missing_ok=True)
            except Exception:
                pass

            if attempt < max_retries:
                sleep_s = base_sleep * (2 ** (attempt - 1)) + random.uniform(0.0, 0.6)
                await asyncio.sleep(sleep_s)

    return False, last_err


def generate_audio(spec: Dict[str, Any], out_dir: Path) -> List[Path]:
    """
    Robust EN+ZH audio generation.

    Output layout:
      audio/en/<stem>.mp3
      audio/zh/<stem>.mp3
      audio/en/sent_<stem>.mp3
      audio/zh/sent_<stem>.mp3

    Key behavior:
      - bounded concurrency
      - retries for transient 503/network failures
      - skips already existing files
      - does not abort the whole run on one failed file
      - raises only if too many files fail
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

    concurrency = max(1, int(os.environ.get("SKYED_TTS_CONCURRENCY", "3") or "3"))
    max_retries = max(1, int(os.environ.get("SKYED_TTS_RETRIES", "5") or "5"))

    outputs: List[Path] = []
    jobs: List[Dict[str, Any]] = []

    vocab = spec.get("vocab", []) or []
    for v in vocab:
        en = (v.get("en") or "").strip()
        zh = (v.get("zh") or "").strip()
        if not en:
            continue

        stem = _slugify(en)

        p_en = en_dir / f"{stem}.mp3"
        outputs.append(p_en)
        jobs.append({"text": en, "voice": voice_en, "rate": rate, "out": p_en, "label": f"vocab_en:{en}"})

        zh_text = zh if zh else en
        p_zh = zh_dir / f"{stem}.mp3"
        outputs.append(p_zh)
        jobs.append({"text": zh_text, "voice": voice_zh, "rate": rate, "out": p_zh, "label": f"vocab_zh:{en}"})

    sentences = spec.get("sentences", []) or []
    for s in sentences:
        en_s = (s.get("en") or "").strip()
        zh_s = (s.get("zh") or "").strip()
        base = en_s or zh_s
        if not base:
            continue

        short = _slugify(base[:60] if len(base) > 60 else base)
        h = hashlib.sha1(base.encode("utf-8")).hexdigest()[:10]
        stem = f"{short}_{h}" if short else h

        if en_s:
            p_en = en_dir / f"sent_{stem}.mp3"
            outputs.append(p_en)
            jobs.append({"text": en_s, "voice": voice_en, "rate": rate, "out": p_en, "label": f"sent_en:{en_s[:50]}"})

        if zh_s:
            p_zh = zh_dir / f"sent_{stem}.mp3"
            outputs.append(p_zh)
            jobs.append({"text": zh_s, "voice": voice_zh, "rate": rate, "out": p_zh, "label": f"sent_zh:{zh_s[:50]}"})

    failures: List[Tuple[str, str]] = []

    async def _runner() -> None:
        semaphore = asyncio.Semaphore(concurrency)

        async def _one(job: Dict[str, Any]) -> None:
            label = str(job.get("label") or "item")
            _tts_log(f"[TTS] START {label}")
            ok, err = await _synth_to_mp3(
                job["text"],
                job["voice"],
                job["rate"],
                job["out"],
                max_retries=max_retries,
                semaphore=semaphore,
            )
            if ok:
                _tts_log(f"[TTS] DONE {label}")
            else:
                _tts_log(f"[TTS] FAIL {label}: {err}")
                failures.append((label, err))

        await asyncio.gather(*[_one(job) for job in jobs], return_exceptions=False)

    if jobs:
        _tts_log(f"[TTS] QUEUE total={len(jobs)} concurrency={concurrency} voices=EN:{voice_en} ZH:{voice_zh}")
        asyncio.run(_runner())

    if failures:
        report = out_dir / "tts_failures.txt"
        report.write_text("\n".join(f"{label} :: {err}" for label, err in failures), encoding="utf-8")
        raise RuntimeError(f"tag_s TTS failed for {len(failures)} item(s). See: {report}")
    return outputs



def generate_long_audio_variants(spec: Dict[str, Any], lesson_root: Path) -> Dict[str, Any]:
    """
    Additive extension used by stricter older-student lesson surfaces.
    Keeps generate_audio() intact and optionally creates long-form variants
    for reading/listening blocks when those sections exist.
    """
    lesson_root = Path(lesson_root)
    rate_env = (os.environ.get("SKYED_TTS_RATE") or "-10%").strip()
    if rate_env.endswith("%"):
        rate = rate_env
    else:
        try:
            rate = _rate_string(int(rate_env))
        except Exception:
            rate = "-10%"

    concurrency = max(1, int(os.environ.get("SKYED_TTS_CONCURRENCY", "3") or "3"))
    max_retries = max(1, int(os.environ.get("SKYED_TTS_RETRIES", "5") or "5"))
    gb_voice = (os.environ.get("SKYED_STRICT_VOICE_GB") or "en-GB-SoniaNeural").strip()
    us_voice = (os.environ.get("SKYED_STRICT_VOICE_US") or "en-US-GuyNeural").strip()

    async def _runner() -> Dict[str, Any]:
        semaphore = asyncio.Semaphore(concurrency)
        for block_key, subdir in (("reading_block", "reading"), ("listening_block", "listening")):
            block = spec.get(block_key) or {}
            if not isinstance(block, dict):
                continue
            text = str(block.get("text") or "").strip()
            if not text:
                continue
            out_dir = lesson_root / "audio" / subdir
            out_dir.mkdir(parents=True, exist_ok=True)
            variants = []
            for voice, key, label in ((gb_voice, "british_female", "British Female"), (us_voice, "us_male", "US Male")):
                out_path = out_dir / f"{key}.mp3"
                ok, err = await _synth_to_mp3(text, voice, rate, out_path, max_retries=max_retries, semaphore=semaphore)
                if ok:
                    variants.append({
                        "key": key,
                        "label": label,
                        "url": str(out_path.relative_to(lesson_root)).replace('\\', '/'),
                    })
            if variants:
                block["audio_variants"] = variants
                spec[block_key] = block
        return spec

    return asyncio.run(_runner())



def generate_word_audio_set(
    items: List[Tuple[str, str]],
    out_dir: Path,
    *,
    voice: str = "en-US-GuyNeural",
    rate: str | None = None,
) -> Dict[str, Path]:
    """Generate one mp3 per (slug, text) pair and return slug->path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    chosen_rate = (rate or (os.environ.get("SKYED_TTS_RATE") or "-10%").strip() or "-10%")
    concurrency = max(1, int(os.environ.get("SKYED_TTS_CONCURRENCY", "3") or "3"))
    max_retries = max(1, int(os.environ.get("SKYED_TTS_RETRIES", "5") or "5"))
    outputs: Dict[str, Path] = {}
    jobs: List[Dict[str, Any]] = []
    for slug, text in items:
        safe_slug = _slugify(slug)
        clean_text = str(text or "").strip()
        if not safe_slug or not clean_text:
            continue
        out = out_dir / f"{safe_slug}.mp3"
        outputs[safe_slug] = out
        jobs.append({"slug": safe_slug, "text": clean_text, "voice": voice, "rate": chosen_rate, "out": out})

    failures: List[Tuple[str, str]] = []

    async def _runner() -> None:
        semaphore = asyncio.Semaphore(concurrency)
        async def _one(job: Dict[str, Any]) -> None:
            label = str(job.get("slug") or "item")
            _tts_log(f"[TTS] START tag_s:{label}")
            ok, err = await _synth_to_mp3(job["text"], job["voice"], job["rate"], job["out"], max_retries=max_retries, semaphore=semaphore)
            if ok:
                _tts_log(f"[TTS] DONE tag_s:{label}")
            else:
                _tts_log(f"[TTS] FAIL tag_s:{label}: {err}")
                failures.append((label, err))
        await asyncio.gather(*[_one(job) for job in jobs], return_exceptions=False)

    if jobs:
        _tts_log(f"[TTS] QUEUE total={len(jobs)} concurrency={concurrency} voice={voice}")
        asyncio.run(_runner())
    if failures:
        report = out_dir / "tts_failures.txt"
        report.write_text("\n".join(f"{label} :: {err}" for label, err in failures), encoding="utf-8")
        raise RuntimeError(f"tag_s TTS failed for {len(failures)} item(s). See: {report}")
    return outputs
