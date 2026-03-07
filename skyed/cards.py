# skyed/cards.py
from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .image_backends import ImageGenRequest, backend_from_env
from .prompt_templates import normalize_style
from .image_planner import build_image_plans, PlannedItem


def slugify(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^a-z0-9_\-]+", "", s)
    return s or "item"


def _font_candidates(user_font: Optional[str]) -> List[str]:
    cands: List[str] = []
    if user_font:
        cands.append(str(user_font))

    cands += [
        str(Path("assets/fonts/NotoSansCJKsc-VF.ttf")),
        str(Path("assets/fonts/NotoSansMonoCJKsc-VF.ttf")),
        str(Path("assets/fonts/NotoSansCJKsc-Regular.ttf")),
        str(Path("assets/fonts/NotoSansSC-Regular.otf")),
        str(Path("assets/fonts/SourceHanSansSC-Regular.otf")),
        str(Path("assets/fonts/SourceHanSansCN-Regular.otf")),
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyh.ttf",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
    ]
    # de-dup while preserving order
    out: List[str] = []
    seen = set()
    for p in cands:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _load_font(size: int, user_font: Optional[str]) -> ImageFont.FreeTypeFont:
    for cand in _font_candidates(user_font):
        try:
            p = Path(cand)
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
    im2 = im.resize((max(1, nw), max(1, nh)), Image.LANCZOS)

    left = max(0, (nw - target_w) // 2)
    top = max(0, (nh - target_h) // 2)
    return im2.crop((left, top, left + target_w, top + target_h))


def _hash_color_triplet(key: str) -> Tuple[Tuple[int,int,int], Tuple[int,int,int], Tuple[int,int,int]]:
    import hashlib
    h = hashlib.sha256((key or "x").encode("utf-8")).hexdigest()
    def c(off: int) -> Tuple[int,int,int]:
        r = int(h[off:off+2], 16)
        g = int(h[off+2:off+4], 16)
        b = int(h[off+4:off+6], 16)
        # soften and brighten a bit
        r = 80 + (r % 140)
        g = 90 + (g % 140)
        b = 110 + (b % 140)
        return (r, g, b)
    return c(0), c(6), c(12)


def _make_placeholder_panel(en: str, w: int, h: int, font_path: Optional[str]) -> Image.Image:
    """
    Deterministic fallback art when AI image is missing.
    Goal: never ship a blank panel.
    """
    en_t = (en or "").strip()
    c1, c2, c3 = _hash_color_triplet(en_t.lower())
    img = Image.new("RGB", (max(64, int(w)), max(64, int(h))), (238, 244, 252))
    d = ImageDraw.Draw(img)

    # simple vertical gradient
    for y in range(img.size[1]):
        t = y / max(1, img.size[1] - 1)
        r = int(c1[0] * (1 - t) + c2[0] * t)
        g = int(c1[1] * (1 - t) + c2[1] * t)
        b = int(c1[2] * (1 - t) + c2[2] * t)
        d.line([(0, y), (img.size[0], y)], fill=(r, g, b))

    pad = int(min(img.size) * 0.08)
    d.rounded_rectangle(
        (pad, pad, img.size[0] - pad, img.size[1] - pad),
        radius=int(min(img.size) * 0.09),
        outline=(255, 255, 255),
        width=3,
    )

    cx = int(img.size[0] * 0.72)
    cy = int(img.size[1] * 0.38)
    cr = int(min(img.size) * 0.18)
    d.ellipse((cx - cr, cy - cr, cx + cr, cy + cr), fill=c3)

    # small dots
    for i in range(8):
        x = int(img.size[0] * (0.15 + (i % 4) * 0.07))
        y = int(img.size[1] * (0.25 + (i // 4) * 0.12))
        d.ellipse((x, y, x + 8, y + 8), fill=(255, 255, 255))

    # centered word (inside panel)
    f = _load_font(int(min(img.size) * 0.16), font_path)
    text = (en_t[:20] if en_t else "?")
    bbox = d.textbbox((0, 0), text, font=f)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = (img.size[0] - tw) // 2
    ty = int(img.size[1] * 0.60)

    d.text((tx + 2, ty + 2), text, fill=(0, 0, 0), font=f)
    d.text((tx, ty), text, fill=(255, 255, 255), font=f)

    return img

def make_flashcard(
    en: str,
    zh: str,
    font_path: Optional[str],
    out_path: Path,
    *,
    ai_image: Optional[Image.Image] = None,
) -> None:
    """A simple vocab flashcard with brand-ish styling.

    Important: even if AI image generation fails, we NEVER leave the image panel blank.
    """
    W, H = 1024, 768
    bg = Image.new("RGB", (W, H), (245, 248, 252))
    d = ImageDraw.Draw(bg)

    en_text = (en or "").strip()
    zh_text = (zh or "").strip()

    # soft top ribbon
    ribbon = Image.new("RGB", (W, 160), (6, 182, 212))
    ribbon = ribbon.filter(ImageFilter.GaussianBlur(0))
    bg.paste(ribbon, (0, 0))

    # image panel
    panel_x1, panel_y1 = 60, 190
    panel_x2, panel_y2 = W - 60, H - 60
    d.rounded_rectangle((panel_x1, panel_y1, panel_x2, panel_y2), radius=32, fill=(255, 255, 255))

    img_x1, img_y1 = panel_x1 + 26, panel_y1 + 26
    img_x2, img_y2 = panel_x2 - 26, panel_y1 + 320

    d.rounded_rectangle((img_x1, img_y1, img_x2, img_y2), radius=24, fill=(235, 243, 250))

    if ai_image is not None:
        fitted = _cover_fit(ai_image, img_x2 - img_x1, img_y2 - img_y1)
        bg.paste(fitted, (img_x1, img_y1))
    else:
        ph = _make_placeholder_panel(en_text, img_x2 - img_x1, img_y2 - img_y1, font_path)
        bg.paste(ph, (img_x1, img_y1))

    f_hint = _load_font(28, font_path)
    f_en = _load_font(78, font_path)
    f_zh = _load_font(56, font_path)
    f_small = _load_font(30, font_path)

    d.text((panel_x1 + 34, panel_y1 + 34), "WORD", fill=(2, 132, 199), font=f_hint)

    d.text((panel_x1 + 34, panel_y1 + 78), en_text, fill=(15, 23, 42), font=f_en)
    if zh_text:
        d.text((panel_x1 + 34, panel_y1 + 175), zh_text, fill=(30, 64, 175), font=f_zh)

    hint = "Say it 2 times + make 1 full sentence."
    d.text((panel_x1 + 34, panel_y2 - 54), hint, fill=(100, 116, 139), font=f_small)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bg.save(out_path, format="PNG")


def _normalize_vocab(spec: Dict[str, Any]) -> List[Dict[str, str]]:
    vocab = spec.get("vocab", []) if isinstance(spec, dict) else []
    out: List[Dict[str, str]] = []
    if isinstance(vocab, list):
        for it in vocab:
            if isinstance(it, dict):
                en = str(it.get("en", "")).strip()
                zh = str(it.get("zh", "")).strip()
            else:
                en = str(it).strip()
                zh = ""
            if en:
                pos = ""
                if isinstance(it, dict):
                    pos = str(it.get("pos") or "").strip().lower()
                out.append({"en": en, "zh": zh, "pos": pos})
    return out


def _int_env(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, "")).strip() or default)
    except Exception:
        return default


def generate_vocab_cards(spec: Dict[str, Any], font_path: Optional[str], out_dir: Path) -> List[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    picture_type = os.environ.get("PICTURE_CARDS_TYPE", "Cartoon")
    style = normalize_style(picture_type)

    backend, backend_name = backend_from_env()

    # defaults per backend
    if backend_name == "cloudflare_flux":
        default_steps = 4
        default_timeout = 180
        default_concurrency = 4
    elif backend_name == "hf_endpoint":
        default_steps = 20
        default_timeout = 240
        default_concurrency = 4
    else:
        default_steps = 28
        default_timeout = 600
        default_concurrency = 1

    width = _int_env("IMG_WIDTH", 768)
    height = _int_env("IMG_HEIGHT", 768)
    steps = _int_env("IMG_STEPS", default_steps)
    timeout_s = _int_env("IMG_TIMEOUT_S", default_timeout)
    concurrency = _int_env("IMG_CONCURRENCY", default_concurrency)

    # backend-specific env (used by backend_from_env)
    comfy_url = os.environ.get("COMFY_URL", "http://127.0.0.1:8188")
    workflow_path = Path(os.environ.get("COMFY_WORKFLOW", "assets/comfy/workflow_api.json"))

    ai_dir = out_dir / "ai"
    ai_dir.mkdir(parents=True, exist_ok=True)

    vocab = _normalize_vocab(spec)
    plans: List[PlannedItem] = build_image_plans(spec)

    # Debug dump: how each vocab item was planned (POS + render_mode + subject phrase)
    try:
        (out_dir / "image_plans.json").write_text(
            __import__("json").dumps(
                [
                    {
                        "en": p.en,
                        "zh": p.zh,
                        "pos": p.pos,
                        "render_mode": p.render_mode,
                        "subject": p.subject,
                        "fallback_mode": p.fallback_mode,
                    }
                    for p in plans
                ],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass
    results: List[Path] = []

    # Status marker for debugging
    status_path = out_dir / "ai_status.txt"
    lines: List[str] = []
    lines.append(f"AI IMAGE GEN: ENABLED")
    lines.append(f"BACKEND={backend_name}")
    lines.append(f"STYLE={style}")
    lines.append(f"IMG_WIDTH={width} IMG_HEIGHT={height} IMG_STEPS={steps} IMG_TIMEOUT_S={timeout_s} IMG_CONCURRENCY={concurrency}")
    if backend_name == "comfyui":
        lines.append(f"COMFY_URL={comfy_url}")
        lines.append(f"COMFY_WORKFLOW={workflow_path} exists={workflow_path.exists()}")
    elif backend_name == "cloudflare_flux":
        lines.append(f"CF_ACCOUNT_ID={os.environ.get('CF_ACCOUNT_ID','')}")
        lines.append(f"CF_MODEL={os.environ.get('CF_MODEL','@cf/black-forest-labs/flux-1-schnell')}")
        lines.append(f"CF_API_TOKEN_SET={'yes' if os.environ.get('CF_API_TOKEN') else 'no'}")
    elif backend_name == "hf_endpoint":
        lines.append(f"HF_IMAGE_ENDPOINT_URL={os.environ.get('HF_IMAGE_ENDPOINT_URL', os.environ.get('HF_ENDPOINT',''))}")
        lines.append(f"HF_TOKEN_SET={'yes' if os.environ.get('HF_TOKEN') else 'no'}")
        lines.append(f"HF_GUIDANCE={os.environ.get('HF_GUIDANCE','')}")
    status_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Generate AI images first (possibly concurrently)
    def _looks_blank(png_bytes: bytes) -> bool:
        """Heuristic blank/gray detection.

        Note: flashcard-style images can have large flat backgrounds.
        We only treat as blank if variance is extremely low.
        """
        try:
            im = Image.open(BytesIO(png_bytes)).convert("L")
            # downscale for speed
            im = im.resize((128, 128))
            hist = im.histogram()
            total = sum(hist)
            if total <= 0:
                return True
            mean = sum(i * c for i, c in enumerate(hist)) / float(total)
            var = sum(((i - mean) ** 2) * c for i, c in enumerate(hist)) / float(total)
            return var < 1.0  # extremely flat
        except Exception:
            return False

    def _int_env2(name: str, default: int) -> int:
        try:
            return int(str(os.environ.get(name, "")).strip() or default)
        except Exception:
            return default

    max_retries = _int_env2("IMG_MAX_RETRIES", 2)

    plan_by_word = {p.en.strip().lower(): p for p in plans}

    def _gen_one(en_word: str) -> Tuple[str, Optional[str]]:
        slug = slugify(en_word)
        ai_png = ai_dir / f"{slug}.png"
        fail_marker = ai_dir / f"{slug}.fail.txt"

        if ai_png.exists() and ai_png.stat().st_size > 0 and not fail_marker.exists():
            return en_word, None

        planned = plan_by_word.get(en_word.strip().lower())
        subj = planned.subject if planned else en_word
        mode = planned.render_mode if planned else "single_object"

        last_err: Optional[str] = None
        import hashlib
        base_seed = int(hashlib.sha256((subj or en_word).encode("utf-8")).hexdigest()[:8], 16)

        for attempt in range(0, max(0, max_retries) + 1):
            try:
                req = ImageGenRequest(
                    subject=subj,
                    style=style,
                    render_mode=mode,
                    width=width,
                    height=height,
                    steps=steps,
                    seed=None if attempt == 0 else (base_seed + attempt),
                )
                png_bytes = backend.generate_png(req, timeout_s=timeout_s)
                if _looks_blank(png_bytes):
                    last_err = "Blank/near-blank output detected"
                    continue
                ai_png.write_bytes(png_bytes)
                if fail_marker.exists():
                    fail_marker.unlink(missing_ok=True)
                return en_word, None
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                continue

        # Final fallback attempt: try icon_card prompt once (often succeeds when object prompt fails).
        try:
            req_fb = ImageGenRequest(
                subject=f"{en_word} simple icon",
                style=style,
                render_mode="icon_card",
                width=width,
                height=height,
                steps=max(1, min(steps, 6)),
                seed=base_seed + 99,
            )
            png_bytes = backend.generate_png(req_fb, timeout_s=timeout_s)
            if not _looks_blank(png_bytes):
                ai_png.write_bytes(png_bytes)
                if fail_marker.exists():
                    fail_marker.unlink(missing_ok=True)
                return en_word, None
        except Exception:
            pass

        fail_marker.write_text(str(last_err or "Unknown error"), encoding="utf-8")
        return en_word, str(last_err or "Unknown error")

    if vocab:
        if concurrency <= 1:
            for it in vocab:
                _gen_one(it["en"])
        else:
            with ThreadPoolExecutor(max_workers=concurrency) as ex:
                futs = [ex.submit(_gen_one, it["en"]) for it in vocab]
                for _f in as_completed(futs):
                    pass

    # Build flashcards
    for it in vocab:
        en = it.get("en", "")
        zh = it.get("zh", "")
        slug = slugify(en)
        out_path = out_dir / f"{slug}.png"

        ai_png = ai_dir / f"{slug}.png"
        fail_marker = ai_dir / f"{slug}.fail.txt"

        ai_img: Optional[Image.Image] = None
        try:
            if ai_png.exists() and ai_png.stat().st_size > 0 and not fail_marker.exists():
                ai_img = Image.open(ai_png).convert("RGB")
        except Exception:
            ai_img = None

        make_flashcard(en, zh, font_path, out_path, ai_image=ai_img)
        results.append(out_path)

    return results
