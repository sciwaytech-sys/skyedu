# skyed/cards.py
from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import json

from .image_backends import ImageGenRequest, backend_from_env
from .asset_library import default_asset_library_root, find_loose_file_asset
from .prompt_templates import normalize_style
from .image_specs import ImageSpec, build_specs_from_parsed_spec
from .image_validation import ImageValidator
from .scene_fallbacks import render_deterministic_scene


def _progress_log(message: str) -> None:
    print(str(message or ""), flush=True)


# slugify re-exported from utils — kept for asset_factory.py backward compat
from .utils import slugify_file as slugify  # noqa: F401


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
    Goal: never ship a blank panel, and avoid turning the image area into a text card.
    """
    en_t = (en or "").strip()
    c1, c2, c3 = _hash_color_triplet(en_t.lower())
    img = Image.new("RGB", (max(64, int(w)), max(64, int(h))), (238, 244, 252))
    d = ImageDraw.Draw(img)

    for y in range(img.size[1]):
        t = y / max(1, img.size[1] - 1)
        r = int(c1[0] * (1 - t) + c2[0] * t)
        g = int(c1[1] * (1 - t) + c2[1] * t)
        b = int(c1[2] * (1 - t) + c2[2] * t)
        d.line([(0, y), (img.size[0], y)], fill=(r, g, b))

    pad = int(min(img.size) * 0.08)
    d.rounded_rectangle((pad, pad, img.size[0] - pad, img.size[1] - pad), radius=int(min(img.size) * 0.09), outline=(255, 255, 255), width=3)
    d.rounded_rectangle((int(img.size[0] * 0.12), int(img.size[1] * 0.18), int(img.size[0] * 0.88), int(img.size[1] * 0.82)), radius=32, fill=(255, 255, 255, 120))

    # friendly abstract scene pieces
    cx = int(img.size[0] * 0.68)
    cy = int(img.size[1] * 0.38)
    cr = int(min(img.size) * 0.16)
    d.ellipse((cx - cr, cy - cr, cx + cr, cy + cr), fill=c3)
    d.rounded_rectangle((int(img.size[0] * 0.18), int(img.size[1] * 0.48), int(img.size[0] * 0.48), int(img.size[1] * 0.72)), radius=24, fill=c2)
    d.rounded_rectangle((int(img.size[0] * 0.50), int(img.size[1] * 0.56), int(img.size[0] * 0.80), int(img.size[1] * 0.72)), radius=24, fill=c1)
    for i in range(8):
        x = int(img.size[0] * (0.14 + (i % 4) * 0.09))
        y = int(img.size[1] * (0.22 + (i // 4) * 0.12))
        d.ellipse((x, y, x + 10, y + 10), fill=(255, 255, 255))

    return img

def _save_png_optimized(img: Image.Image, out_path: Path) -> None:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG", optimize=True, compress_level=9)


def _save_rgba_png_optimized(img: Image.Image, out_path: Path) -> None:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG", optimize=True, compress_level=9)


def _local_asset_library_root() -> Path:
    project_root = os.environ.get("SKYED_PROJECT_ROOT", "").strip() or None
    return default_asset_library_root(project_root)


def _materialize_local_asset_png(target: str, out_path: Path) -> Optional[Path]:
    root = _local_asset_library_root()
    match = find_loose_file_asset(root, target=target)
    if match is None or not match.exists():
        return None
    try:
        img = Image.open(match).convert("RGB")
        _save_png_optimized(img, out_path)
        return match
    except Exception:
        return None


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
    _save_png_optimized(bg, out_path)


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



def _contain_fit(img: Image.Image, target_w: int, target_h: int, *, bg=(238, 244, 252)) -> Image.Image:
    im = img.convert("RGB")
    sw, sh = im.size
    if sw <= 0 or sh <= 0:
        return Image.new("RGB", (target_w, target_h), bg)
    scale = min(target_w / sw, target_h / sh)
    nw, nh = max(1, int(sw * scale)), max(1, int(sh * scale))
    im2 = im.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGB", (target_w, target_h), bg)
    left = (target_w - nw) // 2
    top = (target_h - nh) // 2
    canvas.paste(im2, (left, top))
    return canvas


def make_website_illustration(en: str, font_path: Optional[str], out_path: Path, *, ai_image: Optional[Image.Image] = None) -> None:
    W, H = 800, 600
    canvas = Image.new("RGB", (W, H), (245, 248, 252))
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle((20, 20, W - 20, H - 20), radius=32, fill=(245, 248, 252), outline=(220, 229, 241), width=3)
    art = ai_image if ai_image is not None else _make_placeholder_panel(en, W - 80, H - 120, font_path)
    fitted = _contain_fit(art, W - 80, H - 120, bg=(245, 248, 252))
    canvas.paste(fitted, (40, 40))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _save_png_optimized(canvas, out_path)


def _render_mode_for_image_spec(spec: ImageSpec) -> str:
    mode = str(getattr(spec, "render_mode", "") or "").strip().lower()
    if mode:
        return mode
    pos = (spec.pos or "").strip().lower()
    if pos == "verb":
        return "action_scene"
    if pos == "preposition":
        return "relation_scene"
    if pos in ("adjective", "time", "phrase"):
        return "attribute_scene"
    return "single_object"


def _retry_positive_prompt(img_spec: ImageSpec, attempt: int) -> str:
    base = (img_spec.positive_prompt or "").strip()
    if attempt <= 0:
        return base
    extras = [
        "literal child-friendly ESL card illustration",
        "show the target meaning directly",
        "avoid decorative substitutions",
        "make the main concept immediately recognizable",
        "no writing anywhere in the image",
        "no labels on clothes, walls, books, posters, or signs",
    ]
    if attempt >= 2:
        extras.extend([
            "simple composition",
            "one main scene only",
            "obvious school or home context",
            "remove all poster-like or sign-like details",
        ])
    return ", ".join([x for x in [base] + extras if x])


def _compact_cloudflare_prompt(positive: str, negative: str) -> str:
    pos = (positive or "").strip()
    neg_lc = (negative or "").lower()
    pos_lc = pos.lower()
    forced_positive = [
        "NO TEXT ON THE PICTURE",
        "NO LETTERS ON THE PICTURE",
        "NO NUMBERS ON THE PICTURE",
        "NO CHINESE CHARACTERS ON THE PICTURE",
        "NO ENGLISH WORDS ON THE PICTURE",
        "NO LABELS OR SIGNBOARDS",
    ]
    promoted: list[str] = []
    high_value = [
        "no text",
        "no letters",
        "no numbers",
        "no chinese characters",
        "no english words",
        "no labels",
        "no signboards",
        "no watermark",
        "no logo",
        "no caption",
        "no handwriting",
        "no poster writing",
        "no gibberish text",
    ]
    for token in high_value:
        if token in neg_lc and token not in pos_lc:
            promoted.append(token)
    if "gibberish typography" in neg_lc and "no gibberish text" not in pos_lc:
        promoted.append("no gibberish text")
    parts = []
    if pos:
        parts.append(pos)
    parts.extend(forced_positive)
    parts.extend(promoted)
    return ", ".join(list(dict.fromkeys([p for p in parts if p])))


def _retry_negative_prompt(img_spec: ImageSpec, attempt: int) -> str:
    negatives = list(img_spec.must_exclude or [])
    if attempt >= 1:
        negatives.extend([
            "symbolic substitution",
            "decorative still life",
            "random object",
            "ambiguous scene",
            "text",
            "letters",
            "numbers",
            "chinese characters",
            "english words",
            "label",
            "caption",
            "gibberish typography",
            "misspelled text",
            "broken words",
        ])
    if attempt >= 2:
        negatives.extend([
            "fashion item",
            "camera",
            "vase",
            "stationery only",
        ])
    return ", ".join(dict.fromkeys(x.strip() for x in negatives if x and x.strip()))


def _save_clean_placeholder(label: str, out_path: Path, font_path: Optional[str]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ph = _make_placeholder_panel(label, 768, 768, font_path)
    _save_png_optimized(ph, out_path)

def _looks_blank(png_bytes: bytes) -> bool:
    try:
        im = Image.open(BytesIO(png_bytes)).convert("L")
        im = im.resize((128, 128))
        hist = im.histogram()
        total = sum(hist)
        if total <= 0:
            return True
        mean = sum(i * c for i, c in enumerate(hist)) / float(total)
        var = sum(((i - mean) ** 2) * c for i, c in enumerate(hist)) / float(total)
        return var < 1.0
    except Exception:
        return False


def _should_use_local_cost_saver(img_spec: ImageSpec, backend_name: str) -> bool:
    if backend_name != "cloudflare_flux":
        return False
    if os.getenv("SKYED_CF_SMART_LOCAL", "0").strip().lower() not in {"1", "true", "yes", "on"}:
        return False
    word = (img_spec.word or "").strip().lower()
    pos = (img_spec.pos or "").strip().lower()
    scene_type = (img_spec.scene_type or "").strip().lower()
    render_mode = (img_spec.render_mode or "").strip().lower()
    if pos in {"number", "preposition", "pronoun", "question_word"}:
        return True
    if pos == "adjective" and (
        word in {"happy", "sad", "angry", "tired", "sick", "scared", "hungry", "thirsty", "hot", "cold", "full"}
        or word in {"red", "blue", "green", "yellow", "purple", "orange", "brown", "black", "white", "pink"}
    ):
        return True
    if render_mode in {"counting_scene", "relation_scene", "guided_scene", "symbolic_card"}:
        return True
    if "emotion" in scene_type or "colour" in scene_type or "color" in scene_type:
        return True
    return False



def _generate_single_spec_image(
    img_spec: ImageSpec,
    *,
    slug: str,
    backend,
    backend_name: str,
    style: str,
    width: int,
    height: int,
    steps: int,
    timeout_s: int,
    validator: Optional[ImageValidator],
    ai_png: Path,
    font_path: Optional[str] = None,
    max_retries: Optional[int] = None,
) -> Dict[str, Any]:
    """Generate or resolve one image spec into a PNG and report row.

    Kept as a shared helper for Asset Factory and the lesson card pipeline so the
    two flows stay aligned after the refactor.
    """
    ai_png = Path(ai_png)
    ai_png.parent.mkdir(parents=True, exist_ok=True)
    spec_word = str(getattr(img_spec, "word", "") or "").strip() or slug
    row: Dict[str, Any] = {
        "word": spec_word,
        "slug": slug,
        "status": "pending",
        "backend": backend_name,
        "attempts": [],
        "ai_path": str(ai_png),
        "image_spec": img_spec.to_dict(),
    }

    if max_retries is None:
        if backend_name == "cloudflare_flux":
            default_retries = 0
        elif backend_name == "hf_endpoint":
            default_retries = 1
        else:
            default_retries = 2
        max_retries = _int_env("IMG_MAX_RETRIES", default_retries)

    import hashlib
    seed_base = int(hashlib.sha256((img_spec.positive_prompt or spec_word).encode("utf-8")).hexdigest()[:8], 16)
    fail_marker = ai_png.with_suffix(".fail.txt")
    fallback_marker = ai_png.with_suffix(".fallback.txt")
    accepted = False
    last_err: Optional[str] = None
    validator = validator or ImageValidator()

    if backend_name == "local_assets_only":
        local_match = _materialize_local_asset_png(spec_word, ai_png)
        if local_match is not None:
            row["status"] = "local_asset_match"
            row["local_asset_path"] = str(local_match)
            row["final_prompt"] = "LOCAL_ASSET_ONLY"
            return row
        deterministic = render_deterministic_scene(img_spec, ai_png, include_labels=False)
        if deterministic is not None:
            row["status"] = "local_asset_missing_deterministic_fallback"
            row["fallback_reason"] = "No matching local asset"
            row["final_prompt"] = "LOCAL_DETERMINISTIC_SCENE"
            return row
        _save_clean_placeholder(spec_word, ai_png, font_path)
        row["status"] = "local_asset_missing_placeholder"
        row["fallback_reason"] = "No matching local asset"
        row["final_prompt"] = "LOCAL_PLACEHOLDER"
        return row

    if _should_use_local_cost_saver(img_spec, backend_name):
        deterministic = render_deterministic_scene(img_spec, ai_png, include_labels=False)
        if deterministic is not None:
            row["status"] = "deterministic_local_no_cost"
            row["smart_local"] = True
            row["final_prompt"] = "LOCAL_DETERMINISTIC_SCENE"
            return row

    for attempt in range(0, max(0, int(max_retries)) + 1):
        positive = _retry_positive_prompt(img_spec, attempt)
        negative = _retry_negative_prompt(img_spec, attempt)
        effective_positive = _compact_cloudflare_prompt(positive, negative) if backend_name == "cloudflare_flux" else positive
        req = ImageGenRequest(
            subject=img_spec.word,
            style=style,
            render_mode=_render_mode_for_image_spec(img_spec),
            width=width,
            height=height,
            steps=steps,
            seed=seed_base + attempt,
            positive_prompt=effective_positive,
            negative_prompt=negative,
        )
        attempt_row: Dict[str, Any] = {
            "attempt": attempt,
            "render_mode": req.render_mode,
            "positive_prompt": positive,
            "effective_positive_prompt": effective_positive,
            "negative_prompt": negative,
        }
        try:
            png_bytes = backend.generate_png(req, timeout_s=timeout_s)
            if _looks_blank(png_bytes):
                attempt_row["accepted"] = False
                attempt_row["error"] = "Blank/near-blank output detected"
                row["attempts"].append(attempt_row)
                last_err = "Blank/near-blank output detected"
                continue
            ai_png.write_bytes(png_bytes)
            validation = validator.validate(ai_png, img_spec, used_prompt=effective_positive)
            attempt_row["validation"] = {
                "accepted": validation.accepted,
                "score": validation.score,
                "reasons": list(validation.reasons),
                "ocr_checked": validation.ocr_checked,
                "ocr_error": validation.ocr_error,
                "detected_text": list(validation.detected_text),
            }
            row["attempts"].append(attempt_row)
            if validation.accepted:
                accepted = True
                row["status"] = "accepted"
                row["final_prompt"] = effective_positive
                row["source_positive_prompt"] = positive
                row["final_negative_prompt"] = negative
                if fail_marker.exists():
                    fail_marker.unlink(missing_ok=True)
                if fallback_marker.exists():
                    fallback_marker.unlink(missing_ok=True)
                break
            last_err = "; ".join(validation.reasons) or "Validation rejected image"
            if validation.ocr_error:
                row["ocr_error"] = validation.ocr_error
            if any(r.startswith("Text validation unavailable:") for r in validation.reasons):
                break
        except Exception as e:
            attempt_row["accepted"] = False
            attempt_row["error"] = f"{type(e).__name__}: {e}"
            row["attempts"].append(attempt_row)
            last_err = f"{type(e).__name__}: {e}"

    if not accepted:
        deterministic = render_deterministic_scene(img_spec, ai_png, include_labels=False)
        if deterministic is not None:
            fallback_marker.write_text(last_err or "Deterministic fallback created", encoding="utf-8")
            fail_marker.write_text(last_err or "Deterministic fallback created", encoding="utf-8")
            row["status"] = "deterministic_fallback"
            row["fallback_reason"] = last_err or "AI image rejected"
            row["deterministic_fallback"] = True
        else:
            _save_clean_placeholder(img_spec.word or spec_word, ai_png, font_path)
            fallback_marker.write_text(last_err or "Clean placeholder created", encoding="utf-8")
            fail_marker.write_text(last_err or "Clean placeholder created", encoding="utf-8")
            row["status"] = "clean_placeholder"
            row["fallback_reason"] = last_err or "AI image rejected"

    return row

def generate_vocab_cards(spec: Dict[str, Any], font_path: Optional[str], out_dir: Path) -> List[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    flashcards_dir = out_dir.parent / "flashcards"
    flashcards_dir.mkdir(parents=True, exist_ok=True)

    picture_type = os.environ.get("PICTURE_CARDS_TYPE", "Cartoon")
    style = normalize_style(picture_type)

    backend, backend_name = backend_from_env()

    if backend_name == "cloudflare_flux":
        default_steps = 1
        default_timeout = 180
        default_concurrency = 1
        default_retries = 0
    elif backend_name == "hf_endpoint":
        default_steps = 20
        default_timeout = 240
        default_concurrency = 4
        default_retries = 1
    else:
        default_steps = 28
        default_timeout = 600
        default_concurrency = 1
        default_retries = 2

    width = _int_env("IMG_WIDTH", 768)
    height = _int_env("IMG_HEIGHT", 768)
    steps = _int_env("IMG_STEPS", default_steps)
    timeout_s = _int_env("IMG_TIMEOUT_S", default_timeout)
    concurrency = _int_env("IMG_CONCURRENCY", default_concurrency)

    comfy_url = os.environ.get("COMFY_URL", "http://127.0.0.1:8188")
    workflow_path = Path(os.environ.get("COMFY_WORKFLOW", "assets/comfy/workflow_api.json"))

    ai_dir = out_dir / "ai"
    ai_dir.mkdir(parents=True, exist_ok=True)

    vocab = _normalize_vocab(spec)
    image_specs = build_specs_from_parsed_spec(spec)
    spec_by_word = {s.word.strip().lower(): s for s in image_specs}
    validator = ImageValidator()

    image_specs_path = out_dir / "image_specs.json"
    image_report_path = out_dir / "image_report.json"
    status_path = out_dir / "ai_status.txt"

    try:
        image_specs_path.write_text(
            json.dumps([s.to_dict() for s in image_specs], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

    results: List[Path] = []
    report_rows: List[Dict[str, Any]] = []
    total_items = len(vocab)

    def _int_env2(name: str, default: int) -> int:
        try:
            return int(str(os.environ.get(name, "")).strip() or default)
        except Exception:
            return default

    max_retries = _int_env2("IMG_MAX_RETRIES", default_retries)

    lines: List[str] = []
    lines.append("AI IMAGE GEN: DISABLED (LOCAL ASSETS ONLY)" if backend_name == "local_assets_only" else "AI IMAGE GEN: ENABLED")
    lines.append(f"BACKEND={backend_name}")
    lines.append(f"STYLE={style}")
    lines.append("SOURCE_OF_TRUTH=image_specs")
    lines.append(f"IMG_WIDTH={width} IMG_HEIGHT={height} IMG_STEPS={steps} IMG_TIMEOUT_S={timeout_s} IMG_CONCURRENCY={concurrency}")
    lines.append(f"IMG_MAX_RETRIES={max_retries}")
    lines.append(f"LOCAL_ASSET_LIBRARY_DIR={_local_asset_library_root()}")
    if backend_name == "comfyui":
        lines.append(f"COMFY_URL={comfy_url}")
        lines.append(f"COMFY_WORKFLOW={workflow_path} exists={workflow_path.exists()}")
    elif backend_name == "cloudflare_flux":
        lines.append(f"CF_ACCOUNT_ID={os.environ.get('CF_ACCOUNT_ID','')}")
        lines.append(f"CF_MODEL={os.environ.get('CF_MODEL','@cf/black-forest-labs/flux-1-schnell')}")
        lines.append(f"CF_API_TOKEN_SET={'yes' if os.environ.get('CF_API_TOKEN') else 'no'}")
        lines.append("CF_NOTE=Cloudflare FLUX runs in economy mode by default here: explicit width/height are sent when accepted by the API, negative_prompt is promoted into the positive prompt, and default steps are clamped to 1 unless you override IMG_STEPS manually.")
    elif backend_name == "hf_endpoint":
        lines.append(f"HF_IMAGE_ENDPOINT_URL={os.environ.get('HF_IMAGE_ENDPOINT_URL', os.environ.get('HF_ENDPOINT',''))}")
        lines.append(f"HF_TOKEN_SET={'yes' if os.environ.get('HF_TOKEN') else 'no'}")
        lines.append(f"HF_GUIDANCE={os.environ.get('HF_GUIDANCE','')}")
    status_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _gen_one(entry: Dict[str, str]) -> Dict[str, Any]:
        en_word = str(entry.get("en") or "").strip()
        _progress_log(f"[Cards] START {en_word}")
        slug = slugify(en_word)
        ai_png = ai_dir / f"{slug}.png"
        fail_marker = ai_dir / f"{slug}.fail.txt"
        fallback_marker = ai_dir / f"{slug}.fallback.txt"
        img_spec = spec_by_word.get(en_word.strip().lower())

        if img_spec is None:
            img_spec = ImageSpec(
                word=en_word,
                pos=str(entry.get("pos") or "noun"),
                zh=str(entry.get("zh") or ""),
                positive_prompt=f"ESL lesson illustration for children, clear literal meaning of {en_word}, school or home context",
                negative_prompt="abstract, symbolic substitution, random object",
                fallback_label=en_word,
            )

        row: Dict[str, Any] = {
            "word": en_word,
            "slug": slug,
            "status": "pending",
            "backend": backend_name,
            "attempts": [],
            "ai_path": str(ai_png),
            "image_spec": img_spec.to_dict(),
        }

        import hashlib
        seed_base = int(hashlib.sha256((img_spec.positive_prompt or en_word).encode("utf-8")).hexdigest()[:8], 16)
        accepted = False
        last_err: Optional[str] = None

        if backend_name == "local_assets_only":
            local_match = _materialize_local_asset_png(en_word, ai_png)
            if local_match is not None:
                row["status"] = "local_asset_match"
                row["local_asset_path"] = str(local_match)
                row["final_prompt"] = "LOCAL_ASSET_ONLY"
                _progress_log(f"[Cards] DONE {en_word} status={row['status']}")
                return row
            deterministic = render_deterministic_scene(img_spec, ai_png, include_labels=False)
            if deterministic is not None:
                row["status"] = "local_asset_missing_deterministic_fallback"
                row["fallback_reason"] = "No matching local asset"
                row["final_prompt"] = "LOCAL_DETERMINISTIC_SCENE"
                _progress_log(f"[Cards] DONE {en_word} status={row['status']}")
                return row
            _save_clean_placeholder(img_spec.word or en_word, ai_png, font_path)
            row["status"] = "local_asset_missing_placeholder"
            row["fallback_reason"] = "No matching local asset"
            row["final_prompt"] = "LOCAL_PLACEHOLDER"
            _progress_log(f"[Cards] DONE {en_word} status={row['status']}")
            return row

        if _should_use_local_cost_saver(img_spec, backend_name):
            deterministic = render_deterministic_scene(img_spec, ai_png, include_labels=False)
            if deterministic is not None:
                row["status"] = "deterministic_local_no_cost"
                row["smart_local"] = True
                row["final_prompt"] = "LOCAL_DETERMINISTIC_SCENE"
                _progress_log(f"[Cards] DONE {en_word} status={row['status']}")
                return row

        for attempt in range(0, max(0, max_retries) + 1):
            positive = _retry_positive_prompt(img_spec, attempt)
            negative = _retry_negative_prompt(img_spec, attempt)
            effective_positive = _compact_cloudflare_prompt(positive, negative) if backend_name == "cloudflare_flux" else positive
            req = ImageGenRequest(
                subject=img_spec.word,
                style=style,
                render_mode=_render_mode_for_image_spec(img_spec),
                width=width,
                height=height,
                steps=steps,
                seed=seed_base + attempt,
                positive_prompt=effective_positive,
                negative_prompt=negative,
            )
            attempt_row: Dict[str, Any] = {
                "attempt": attempt,
                "render_mode": req.render_mode,
                "positive_prompt": positive,
                "effective_positive_prompt": effective_positive,
                "negative_prompt": negative,
            }
            try:
                png_bytes = backend.generate_png(req, timeout_s=timeout_s)
                if _looks_blank(png_bytes):
                    attempt_row["accepted"] = False
                    attempt_row["error"] = "Blank/near-blank output detected"
                    row["attempts"].append(attempt_row)
                    last_err = "Blank/near-blank output detected"
                    continue
                ai_png.write_bytes(png_bytes)
                validation = validator.validate(ai_png, img_spec, used_prompt=effective_positive)
                attempt_row["validation"] = {
                    "accepted": validation.accepted,
                    "score": validation.score,
                    "reasons": list(validation.reasons),
                    "ocr_checked": validation.ocr_checked,
                    "ocr_error": validation.ocr_error,
                    "detected_text": list(validation.detected_text),
                }
                row["attempts"].append(attempt_row)
                if validation.accepted:
                    accepted = True
                    row["status"] = "accepted"
                    row["final_prompt"] = effective_positive
                    row["source_positive_prompt"] = positive
                    row["final_negative_prompt"] = negative
                    if fail_marker.exists():
                        fail_marker.unlink(missing_ok=True)
                    if fallback_marker.exists():
                        fallback_marker.unlink(missing_ok=True)
                    break
                last_err = "; ".join(validation.reasons) or "Validation rejected image"
                if validation.ocr_error:
                    row["ocr_error"] = validation.ocr_error
                if any(r.startswith("Text validation unavailable:") for r in validation.reasons):
                    break
            except Exception as e:
                attempt_row["accepted"] = False
                attempt_row["error"] = f"{type(e).__name__}: {e}"
                row["attempts"].append(attempt_row)
                last_err = f"{type(e).__name__}: {e}"

        if not accepted:
            deterministic = render_deterministic_scene(img_spec, ai_png, include_labels=False)
            if deterministic is not None:
                fallback_marker.write_text(last_err or "Deterministic fallback created", encoding="utf-8")
                fail_marker.write_text(last_err or "Deterministic fallback created", encoding="utf-8")
                row["status"] = "deterministic_fallback"
                row["fallback_reason"] = last_err or "AI image rejected"
                row["deterministic_fallback"] = True
            else:
                _save_clean_placeholder(img_spec.word or en_word, ai_png, font_path)
                fallback_marker.write_text(last_err or "Clean placeholder created", encoding="utf-8")
                fail_marker.write_text(last_err or "Clean placeholder created", encoding="utf-8")
                row["status"] = "clean_placeholder"
                row["fallback_reason"] = last_err or "AI image rejected"

        _progress_log(f"[Cards] DONE {en_word} status={row.get('status','unknown')}")
        return row

    if vocab:
        _progress_log(f"[Cards] QUEUE total={total_items} backend={backend_name} concurrency={concurrency}")
        if concurrency <= 1:
            for it in vocab:
                report_rows.append(_gen_one(it))
        else:
            with ThreadPoolExecutor(max_workers=concurrency) as ex:
                futs = [ex.submit(_gen_one, it) for it in vocab]
                for f in as_completed(futs):
                    report_rows.append(f.result())

    try:
        image_report_path.write_text(json.dumps(report_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    for it in vocab:
        en = it.get("en", "")
        zh = it.get("zh", "")
        slug = slugify(en)
        website_img = out_dir / f"{slug}.png"
        flashcard_img = flashcards_dir / f"{slug}.png"

        ai_png = ai_dir / f"{slug}.png"
        ai_img: Optional[Image.Image] = None
        try:
            if ai_png.exists() and ai_png.stat().st_size > 0:
                ai_img = Image.open(ai_png).convert("RGB")
        except Exception:
            ai_img = None

        make_website_illustration(en, font_path, website_img, ai_image=ai_img)
        make_flashcard(en, zh, font_path, flashcard_img, ai_image=ai_img)
        results.append(website_img)

    return results

