# skyed/cards.py
from __future__ import annotations

import os
import re
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont


def slugify(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^a-z0-9_\-]+", "", s)
    return s or "item"


def stable_seed_for_word(word: str) -> int:
    """Stable per-word seed to avoid repeated images when prompts differ."""
    w = (word or "").strip().encode("utf-8")
    h = hashlib.sha1(w).hexdigest()[:8]
    # 32-bit signed positive range
    return int(h, 16) & 0x7FFFFFFF


def _font_candidates(user_font: Optional[str]) -> List[str]:
    cands: List[str] = []
    if user_font:
        cands.append(str(user_font))

    cands += [
        str(Path("assets/fonts/NotoSansCJKsc-Regular.ttf")),
        str(Path("assets/fonts/NotoSansSC-Regular.otf")),
        str(Path("assets/fonts/SourceHanSansSC-Regular.otf")),
        str(Path("assets/fonts/SourceHanSansCN-Regular.otf")),
    ]

    cands += [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        r"C:\Windows\Fonts\arial.ttf",
    ]
    return cands


def load_font_safe(font_path: Optional[str], size: int) -> ImageFont.FreeTypeFont:
    for fp in _font_candidates(font_path):
        try:
            p = Path(fp)
            if p.exists():
                return ImageFont.truetype(str(p), size)
        except Exception:
            continue
    return ImageFont.load_default()


def _cover_fit(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    im = img.convert("RGB")
    sw, sh = im.size
    if sw <= 0 or sh <= 0:
        return Image.new("RGB", (target_w, target_h), (240, 240, 240))

    scale = max(target_w / sw, target_h / sh)
    nw, nh = int(sw * scale), int(sh * scale)
    im = im.resize((nw, nh), Image.LANCZOS)
    left = max(0, (nw - target_w) // 2)
    top = max(0, (nh - target_h) // 2)
    return im.crop((left, top, left + target_w, top + target_h))


def make_flashcard(
    en: str,
    zh: str,
    font_path: Optional[str],
    out_path: Path,
    *,
    ai_image: Optional[Image.Image] = None,
) -> Path:
    out_path = Path(out_path)
    W, H = 1600, 900

    bg = Image.new("RGB", (W, H), (248, 250, 252))
    d = ImageDraw.Draw(bg)

    f_brand = load_font_safe(font_path, 38)
    f_slogan = load_font_safe(font_path, 22)
    f_en = load_font_safe(font_path, 72)
    f_zh = load_font_safe(font_path, 46)
    f_hint = load_font_safe(font_path, 24)

    d.rectangle([0, 0, W, 86], fill=(7, 89, 133))
    d.text((30, 24), "Sky Education", fill=(255, 255, 255), font=f_brand)
    slogan = "We are smart, because we love to learn."
    d.text((W - 30 - d.textlength(slogan, font=f_slogan), 30), slogan, fill=(226, 232, 240), font=f_slogan)

    pad = 42
    box = (pad, 110, W - pad, H - pad)

    shadow = Image.new("RGB", (W, H), (0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle([box[0] + 8, box[1] + 10, box[2] + 8, box[3] + 10], radius=40, fill=(0, 0, 0))
    shadow = shadow.filter(ImageFilter.GaussianBlur(12))
    bg = Image.blend(bg, shadow, 0.12)
    d = ImageDraw.Draw(bg)

    d.rounded_rectangle(box, radius=40, fill=(255, 255, 255), outline=(226, 232, 240), width=3)

    img_x1, img_y1 = pad + 30, 150
    img_x2, img_y2 = int(W * 0.62), H - pad - 30
    panel_x1, panel_y1 = img_x2 + 26, 150
    panel_x2, panel_y2 = W - pad - 30, H - pad - 30

    d.rounded_rectangle([img_x1, img_y1, img_x2, img_y2], radius=28, fill=(241, 245, 249))
    d.rounded_rectangle([panel_x1, panel_y1, panel_x2, panel_y2], radius=28, fill=(248, 250, 252))

    if ai_image is not None:
        fitted = _cover_fit(ai_image, img_x2 - img_x1, img_y2 - img_y1)
        bg.paste(fitted, (img_x1, img_y1))

    d.text((panel_x1 + 34, panel_y1 + 34), "WORD", fill=(2, 132, 199), font=f_hint)

    en_text = (en or "").strip()
    zh_text = (zh or "").strip()

    d.text((panel_x1 + 34, panel_y1 + 78), en_text, fill=(15, 23, 42), font=f_en)
    if zh_text:
        d.text((panel_x1 + 34, panel_y1 + 175), zh_text, fill=(30, 64, 175), font=f_zh)

    hint = "Say it 2 times + make 1 full sentence."
    d.text((panel_x1 + 34, panel_y2 - 50), hint, fill=(100, 116, 139), font=f_hint)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    bg.save(out_path, "PNG")
    return out_path


def _normalize_vocab(spec: Dict[str, Any]) -> List[Tuple[str, str]]:
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


def generate_vocab_cards(spec: Dict[str, Any], font_path: Optional[str], out_dir: Path) -> List[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    comfy_url = os.environ.get("COMFY_URL", "http://127.0.0.1:8188")
    workflow_path = Path(os.environ.get("COMFY_WORKFLOW", "assets/comfy/workflow_api.json"))

    picture_type = (os.environ.get("PICTURE_CARDS_TYPE", "Cartoon") or "Cartoon").strip().lower()
    style = "realistic" if picture_type.startswith("real") else "cartoon"

    ai_dir = out_dir / "ai"
    ai_dir.mkdir(parents=True, exist_ok=True)

    vocab = _normalize_vocab(spec)
    results: List[Path] = []

    generate_image_for_word = None
    ai_disabled_reason = ""

    if not workflow_path.exists():
        ai_disabled_reason = f"COMFY_WORKFLOW not found: {workflow_path}"
    else:
        try:
            from .comfy_client import generate_image_for_word as _gen  # type: ignore
            generate_image_for_word = _gen
        except Exception as e:
            # DO NOT silently disable; record reason
            ai_disabled_reason = f"Failed to import skyed.comfy_client.generate_image_for_word: {type(e).__name__}: {e}"
            generate_image_for_word = None

    # Write a one-time status marker so we can prove why AI is on/off
    status_path = out_dir / "ai_status.txt"
    if generate_image_for_word is None:
        status_path.write_text(
            "AI IMAGE GEN: DISABLED\n"
            f"Reason: {ai_disabled_reason}\n"
            f"COMFY_URL={comfy_url}\n"
            f"COMFY_WORKFLOW={workflow_path}\n",
            encoding="utf-8",
        )
    else:
        status_path.write_text(
            "AI IMAGE GEN: ENABLED\n"
            f"COMFY_URL={comfy_url}\n"
            f"COMFY_WORKFLOW={workflow_path}\n"
            f"STYLE={style}\n",
            encoding="utf-8",
        )

    for (en, zh) in vocab:
        slug = slugify(en)
        out_path = out_dir / f"{slug}.png"
        ai_img: Optional[Image.Image] = None

        if generate_image_for_word is not None:
            ai_png = ai_dir / f"{slug}.png"
            fail_marker = ai_dir / f"{slug}.fail.txt"
            try:
                generate_image_for_word(
                    comfy_url=comfy_url,
                    workflow_path=workflow_path,
                    word=en,
                    out_path=ai_png,
                    seed=stable_seed_for_word(en),
                    steps=28,
                    cfg=6.0,
                    timeout_s=600,
                    style=style,
                )
                if ai_png.exists() and ai_png.stat().st_size > 0:
                    ai_img = Image.open(ai_png).convert("RGB")
                    if fail_marker.exists():
                        fail_marker.unlink(missing_ok=True)
                else:
                    raise RuntimeError(f"ComfyUI returned no image file: {ai_png}")
            except Exception as e:
                ai_img = None
                fail_marker.write_text(f"{type(e).__name__}: {e}", encoding="utf-8")

        make_flashcard(en, zh, font_path, out_path, ai_image=ai_img)
        results.append(out_path)

    return results
