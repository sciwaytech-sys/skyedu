# skyed/cards.py
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont


# ----------------------------
# Utilities
# ----------------------------

def slugify(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^a-z0-9_\-]+", "", s)
    return s or "item"


def _font_candidates(user_font: Optional[str]) -> List[str]:
    cands: List[str] = []
    if user_font:
        cands.append(str(user_font))

    # Project-bundled fonts (optional)
    cands += [
        str(Path("assets/fonts/NotoSansCJKsc-Regular.ttf")),
        str(Path("assets/fonts/NotoSansSC-Regular.otf")),
        str(Path("assets/fonts/SourceHanSansSC-Regular.otf")),
        str(Path("assets/fonts/SourceHanSansCN-Regular.otf")),
    ]

    # Windows built-in fonts (Chinese-capable)
    cands += [
        r"C:\Windows\Fonts\msyh.ttc",     # Microsoft YaHei
        r"C:\Windows\Fonts\msyhbd.ttc",   # YaHei Bold
        r"C:\Windows\Fonts\simhei.ttf",   # SimHei
        r"C:\Windows\Fonts\simsun.ttc",   # SimSun
        r"C:\Windows\Fonts\arial.ttf",    # Latin fallback
    ]
    return cands


def load_font_safe(font_path: Optional[str], size: int) -> ImageFont.FreeTypeFont:
    """
    Load a font safely:
    - tries user-provided path
    - then bundled assets/fonts
    - then Windows fonts
    - finally Pillow default bitmap font
    Never raises OSError.
    """
    for fp in _font_candidates(font_path):
        try:
            p = Path(fp)
            if p.exists():
                return ImageFont.truetype(str(p), size)
        except Exception:
            continue
    return ImageFont.load_default()


def _cover_fit(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Scale+crop to fully cover target rectangle (like CSS background-size: cover)."""
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


# ----------------------------
# Flashcard rendering
# ----------------------------

def make_flashcard(
    en: str,
    zh: str,
    font_path: Optional[str],
    out_path: Path,
    *,
    ai_image: Optional[Image.Image] = None,
) -> Path:
    """
    Create one modern flashcard PNG.
    - Big image on the left
    - Word + translation on the right
    - Brand header
    """
    out_path = Path(out_path)
    W, H = 1600, 900

    bg = Image.new("RGB", (W, H), (248, 250, 252))
    d = ImageDraw.Draw(bg)

    # Fonts (safe)
    f_brand = load_font_safe(font_path, 38)
    f_slogan = load_font_safe(font_path, 22)
    f_en = load_font_safe(font_path, 72)
    f_zh = load_font_safe(font_path, 46)
    f_hint = load_font_safe(font_path, 24)

    # Brand header
    d.rectangle([0, 0, W, 86], fill=(7, 89, 133))
    d.text((30, 24), "Sky Education", fill=(255, 255, 255), font=f_brand)
    slogan = "We are smart, because we love to learn."
    d.text((W - 30 - d.textlength(slogan, font=f_slogan), 30), slogan, fill=(226, 232, 240), font=f_slogan)

    # Card container with subtle shadow
    pad = 42
    box = (pad, 110, W - pad, H - pad)

    shadow = Image.new("RGB", (W, H), (0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle([box[0] + 8, box[1] + 10, box[2] + 8, box[3] + 10], radius=40, fill=(0, 0, 0))
    shadow = shadow.filter(ImageFilter.GaussianBlur(12))
    bg = Image.blend(bg, shadow, 0.12)
    d = ImageDraw.Draw(bg)

    d.rounded_rectangle(box, radius=40, fill=(255, 255, 255), outline=(226, 232, 240), width=3)

    # Layout: left image + right panel
    img_x1, img_y1 = pad + 30, 150
    img_x2, img_y2 = int(W * 0.62), H - pad - 30
    panel_x1, panel_y1 = img_x2 + 26, 150
    panel_x2, panel_y2 = W - pad - 30, H - pad - 30

    d.rounded_rectangle([img_x1, img_y1, img_x2, img_y2], radius=28, fill=(241, 245, 249))
    d.rounded_rectangle([panel_x1, panel_y1, panel_x2, panel_y2], radius=28, fill=(248, 250, 252))

    # Fill image region
    if ai_image is not None:
        fitted = _cover_fit(ai_image, img_x2 - img_x1, img_y2 - img_y1)
        bg.paste(fitted, (img_x1, img_y1))

    # Text panel
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


# ----------------------------
# Vocabulary cards pipeline
# ----------------------------

def _normalize_vocab(spec: Dict[str, Any]) -> List[Tuple[str, str]]:
    """
    Accept multiple shapes:
      spec["vocab"] or spec["vocabulary"] can be:
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


def generate_vocab_cards(spec: Dict[str, Any], font_path: Optional[str], out_dir: Path) -> List[Path]:
    """
    Generate flashcards into out_dir as PNGs.
    If ComfyUI is available + workflow exists, it will generate images; otherwise cards are made with empty image area.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Optional AI images via ComfyUI
    comfy_url = os.environ.get("COMFY_URL", "http://127.0.0.1:8188")
    workflow_path = Path(os.environ.get("COMFY_WORKFLOW", "assets/comfy/workflow_api.json"))
    ai_dir = out_dir / "ai"
    ai_dir.mkdir(parents=True, exist_ok=True)

    vocab = _normalize_vocab(spec)
    results: List[Path] = []

    can_use_comfy = workflow_path.exists()
    generate_image_for_word = None
    if can_use_comfy:
        try:
            from .comfy_client import generate_image_for_word as _gen
            generate_image_for_word = _gen
        except Exception:
            generate_image_for_word = None

    for (en, zh) in vocab:
        slug = slugify(en)
        out_path = out_dir / f"{slug}.png"

        ai_img: Optional[Image.Image] = None
        if generate_image_for_word is not None:
            try:
                ai_png = ai_dir / f"{slug}.png"
                generate_image_for_word(
                    comfy_url=comfy_url,
                    workflow_path=workflow_path,
                    word=en,
                    out_path=ai_png,
                    seed=42,
                    steps=28,
                    cfg=6.0,
                    timeout_s=600,
                )
                ai_img = Image.open(ai_png).convert("RGB")
            except Exception:
                ai_img = None

        make_flashcard(en, zh, font_path, out_path, ai_image=ai_img)
        results.append(out_path)

    return results
