from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import shutil
from time import perf_counter
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

from skyed.parser import parse_homework_text
from skyed.utils import slugify, ensure_dir
from skyed.cards import generate_vocab_cards, slugify as card_slugify
from skyed.tts_edge import generate_audio, generate_long_audio_variants
from skyed.quizgen import generate_quiz, normalize_theme_variant
from skyed.wp import upload_media, create_post, ensure_page_path, next_sequential_slug, assert_slug_available
from skyed.lesson_metadata import infer_lesson_metadata, update_catalog
from skyed.tag_registry import discover_tag_games, write_tag_registry
from skyed.picture_reader import parse_bilingual_image


def _sentence_audio_stem(base_text: str) -> str:
    """
    Stable stem for sentence audio files.
    - uses a short slug from the sentence text (or its first chunk)
    - adds a hash suffix to avoid collisions
    Result is used as: sent_<stem>.mp3
    """
    t = (base_text or "").strip()
    short = card_slugify(t[:60] if len(t) > 60 else t)
    h = hashlib.sha1(t.encode("utf-8")).hexdigest()[:10]
    return f"{short}_{h}" if short else h


def _h(text: str) -> str:
    return html.escape(text or "", quote=False)


def _hu(text: str) -> str:
    return html.escape(text or "", quote=True)


def build_lesson_html(
    title: str,
    vocab_items: List[Dict[str, str]],
    sentence_items: List[Dict[str, str]],
    quiz_url: str,
    *,
    quiz_embed_mode: str = "embed",  # embed | link | off
    quiz_note: str = "",
) -> str:
    """
    LMS-style lesson page:
      - Vocab grid: each card contains image + EN+CN + audio (EN+CN).
      - Sentences section: EN+CN line pairs + audio (EN+CN).
      - Quiz block: always shows a "Start Quiz" button when URL is available.
        iframe is optional (Tutor LMS may sanitize/limit iframe/script behavior).
    """
    css = """
    <style>
      :root{
        --max:1100px; --r:16px; --stroke:rgba(0,0,0,.10);
        --shadow:0 10px 25px rgba(0,0,0,.10);
        --bg:#f6f7fb; --card:#fff; --muted:#6b7280;
        --brand1:#2563eb; --brand2:#38bdf8;
      }
      body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;background:var(--bg);color:#111827;}
      .wrap{max-width:var(--max);margin:0 auto;padding:18px 14px 60px;}
      .hero{background:linear-gradient(90deg,var(--brand1),var(--brand2));color:#fff;border-radius:18px;padding:16px 16px 12px;margin-bottom:14px;box-shadow:var(--shadow);}
      .hero h2{margin:0;font-size:20px;}
      .hero .sub{opacity:.9;margin-top:6px;font-size:13px;}
      .h3{font-size:14px;margin:16px 0 10px;color:#0f172a;}
      .grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;}
      @media (max-width:980px){.grid{grid-template-columns:repeat(2,minmax(0,1fr));}}
      @media (max-width:620px){.grid{grid-template-columns:1fr;}}
      .card{background:var(--card);border:1px solid var(--stroke);border-radius:var(--r);overflow:hidden;box-shadow:var(--shadow);display:flex;flex-direction:column;}
      .media{aspect-ratio:4/3;background:#eef2ff;display:flex;align-items:center;justify-content:center;}
      /* IMPORTANT: contain -> prevents “cut” look */
      .media img{width:100%;height:100%;object-fit:contain;display:block;background:#eef2ff;}
      .bd{padding:12px 12px 14px;display:flex;flex-direction:column;gap:10px;}
      .row{display:flex;align-items:baseline;justify-content:space-between;gap:10px;flex-wrap:wrap;}
      .en{font-weight:800;font-size:18px;}
      .zh{font-weight:650;color:var(--muted);}
      audio{width:100%;}
      .note{font-size:12px;color:var(--muted);margin-top:6px;}

      .sentwrap{display:flex;flex-direction:column;gap:10px;}
      .skyed-sent{background:var(--card);border:1px solid var(--stroke);border-radius:16px;box-shadow:var(--shadow);padding:12px;}
      .skyed-sent .txt{display:flex;flex-direction:column;gap:6px;margin-bottom:10px;}
      .skyed-sent .txt b{color:#0f172a;}
      .skyed-sent .aud{display:flex;flex-direction:column;gap:8px;}

      .quizbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:6px 0 12px;}
      .btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;padding:10px 14px;border-radius:999px;
           text-decoration:none;font-weight:800;border:1px solid rgba(255,255,255,.25);}
      .btn-primary{background:linear-gradient(90deg,var(--brand1),var(--brand2));color:#fff;}
      .btn-ghost{background:#fff;color:#0f172a;border:1px solid var(--stroke);}
      .warn{background:#fff7ed;border:1px solid rgba(249,115,22,.25);color:#9a3412;border-radius:14px;padding:10px 12px;}
      iframe{width:100%;height:900px;border:0;border-radius:16px;box-shadow:var(--shadow);background:#fff;}
    </style>
    """

    vocab_cards: List[str] = []
    for it in vocab_items:
        en = (it.get("en") or "").strip()
        zh = (it.get("zh") or "").strip()
        img = (it.get("img") or "").strip()
        a_en = (it.get("audio_en") or "").strip()
        a_zh = (it.get("audio_zh") or "").strip()

        en_t = _h(en)
        zh_t = _h(zh)
        img_u = _hu(img)
        a_en_u = _hu(a_en)
        a_zh_u = _hu(a_zh)
        alt_t = _hu(en)

        card = ['<div class="card">']
        card.append('<div class="media">')
        if img_u:
            card.append(f'<img src="{img_u}" alt="{alt_t}" loading="lazy">')
        card.append("</div>")
        card.append('<div class="bd">')
        card.append('<div class="row">')
        card.append(f'<div class="en">{en_t}</div>')
        card.append(f'<div class="zh">{zh_t}</div>' if zh_t else '<div class="zh"></div>')
        card.append("</div>")
        if a_en_u:
            card.append(f'<audio controls src="{a_en_u}"></audio>')
        if a_zh_u:
            card.append(f'<audio controls src="{a_zh_u}"></audio>')
        card.append("</div></div>")
        vocab_cards.append("".join(card))

    sent_cards: List[str] = []
    for it in sentence_items:
        en = (it.get("en") or "").strip()
        zh = (it.get("zh") or "").strip()
        a_en = (it.get("audio_en") or "").strip()
        a_zh = (it.get("audio_zh") or "").strip()

        en_t = _h(en)
        zh_t = _h(zh)
        a_en_u = _hu(a_en)
        a_zh_u = _hu(a_zh)

        row = ['<div class="skyed-sent">']
        row.append('<div class="txt">')
        if en_t:
            row.append(f"<div><b>EN:</b> {en_t}</div>")
        if zh_t:
            row.append(f"<div><b>CN:</b> {zh_t}</div>")
        row.append("</div>")
        row.append('<div class="aud">')
        if a_en_u:
            row.append(f'<audio controls src="{a_en_u}"></audio>')
        if a_zh_u:
            row.append(f'<audio controls src="{a_zh_u}"></audio>')
        row.append("</div></div>")
        sent_cards.append("".join(row))

    q_url = (quiz_url or "").strip()
    embed = (quiz_embed_mode or "embed").strip().lower() == "embed"
    link_ok = bool(q_url) and q_url.lower() != "about:blank"

    # For Tutor LMS safety: show button always when link exists; embed iframe only if allowed.
    quiz_block = []
    if quiz_note:
        quiz_block.append(f'<div class="warn">{_h(quiz_note)}</div>')
    quiz_block.append('<div class="quizbar">')
    if link_ok:
        quiz_block.append(f'<a class="btn btn-primary" href="{_hu(q_url)}" target="_blank" rel="noopener">▶ Start Practice</a>')
        quiz_block.append(f'<a class="btn btn-ghost" href="{_hu(q_url)}">Open here</a>')
    else:
        quiz_block.append('<span class="warn">Practice URL not configured. Configure QUIZ_PUBLIC_BASE only if you want a separate hosted practice page.</span>')
    quiz_block.append('</div>')
    if embed and link_ok:
        quiz_block.append(f'<iframe src="{_hu(q_url)}" loading="lazy"></iframe>')

    html_out = f"""{css}
    <div class="wrap">
      <div class="hero">
        <h2>{_h(title)}</h2>
        <div class="sub">Vocabulary → Sentences → Practice</div>
      </div>

      <div class="h3">Vocabulary Cards</div>
      <div class="grid">
        {''.join(vocab_cards)}
      </div>
      <div class="note">Keep practice 5–10 minutes. Audio optional.</div>

      <div class="h3">Sentences</div>
      <div class="sentwrap">
        {''.join(sent_cards)}
      </div>

      <div class="h3">Practice</div>
      {''.join(quiz_block)}
    </div>
    """
    return html_out



def build_picture_reader_html(title: str, lines: List[Dict[str, str]]) -> str:
    css = """
    <style>
      :root{--bg:#f4f7fb;--card:#ffffff;--ink:#0f172a;--muted:#64748b;--brand:#2563eb;--brand2:#38bdf8;--line:#dbe7ff;--shadow:0 18px 40px rgba(15,23,42,.08);}
      body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;background:linear-gradient(180deg,#eef6ff 0%,#f8fbff 48%,#f4f7fb 100%);color:var(--ink);}
      .reader-shell{max-width:940px;margin:0 auto;padding:18px 14px 64px;}
      .reader-hero{background:linear-gradient(120deg,var(--brand),var(--brand2));padding:20px;border-radius:26px;color:#fff;box-shadow:var(--shadow);margin-bottom:18px;}
      .reader-hero h1{margin:0 0 6px;font-size:clamp(24px,3vw,38px);}
      .reader-hero p{margin:0;opacity:.95;font-size:14px;}
      .reader-frame{background:rgba(255,255,255,.72);border:1px solid rgba(37,99,235,.12);backdrop-filter:blur(8px);border-radius:30px;padding:14px;box-shadow:var(--shadow);}
      .reader-paper{background:var(--card);border-radius:24px;padding:16px;border:1px solid rgba(15,23,42,.06);}
      .reader-tip{display:flex;gap:10px;align-items:center;background:#eff6ff;border:1px solid #bfdbfe;color:#1d4ed8;border-radius:18px;padding:12px 14px;margin-bottom:14px;font-size:14px;}
      .reader-list{display:flex;flex-direction:column;gap:12px;}
      .reader-line{border:1px solid var(--line);border-radius:18px;padding:16px;background:linear-gradient(180deg,#ffffff,#f8fbff);cursor:pointer;transition:.18s ease;position:relative;width:100%;text-align:left;}
      .reader-line:hover,.reader-line:focus-visible{transform:translateY(-1px);box-shadow:0 10px 24px rgba(37,99,235,.10);border-color:#93c5fd;}
      .reader-line.is-playing{border-color:#2563eb;box-shadow:0 14px 28px rgba(37,99,235,.18);}
      .reader-line__num{position:absolute;top:12px;right:12px;min-width:28px;height:28px;border-radius:999px;background:#dbeafe;color:#1d4ed8;font-weight:800;display:flex;align-items:center;justify-content:center;font-size:13px;}
      .reader-line__en{font-size:clamp(18px,2.5vw,26px);font-weight:800;line-height:1.45;padding-right:40px;}
      .reader-line__zh{font-size:clamp(15px,2vw,21px);color:#334155;line-height:1.65;margin-top:8px;}
      .reader-line__actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:12px;color:var(--muted);font-size:13px;}
      .reader-pill{display:inline-flex;align-items:center;gap:8px;background:#eff6ff;color:#1d4ed8;border-radius:999px;padding:8px 12px;font-weight:700;}
      .reader-muted{color:var(--muted);}
      @media (max-width:640px){.reader-shell{padding:12px 10px 44px}.reader-hero{border-radius:22px}.reader-frame{border-radius:22px;padding:10px}.reader-paper{padding:12px;border-radius:18px}.reader-line{padding:14px 14px 16px}}
    </style>
    """
    cards: List[str] = []
    for idx, it in enumerate(lines, start=1):
        en = _h((it.get("en") or "").strip())
        zh = _h((it.get("zh") or "").strip())
        raw = _h((it.get("raw") or "").strip())
        a_en = _hu((it.get("audio_en") or "").strip())
        a_zh = _hu((it.get("audio_zh") or "").strip())
        text_main = en or raw
        text_sub = zh if zh else ""
        zh_html = f'<div class="reader-line__zh">{text_sub}</div>' if text_sub else ""
        cards.append(
            f'<button class="reader-line" type="button" data-audio-en="{a_en}" data-audio-zh="{a_zh}">'
            f'<span class="reader-line__num">{idx}</span>'
            f'<div class="reader-line__en">{text_main}</div>'
            f'{zh_html}'
            f'<div class="reader-line__actions"><span class="reader-pill">Tap to listen</span>'
            f'<span class="reader-muted">English and Chinese will play in order.</span></div></button>'
        )
    script = """
    <script>
    (function(){
      let current=null;
      function stopCurrent(){ if(current){ try{ current.pause(); current.currentTime=0; }catch(e){} current=null; } document.querySelectorAll('.reader-line.is-playing').forEach(el=>el.classList.remove('is-playing')); }
      function playQueue(urls, host){
        stopCurrent();
        host.classList.add('is-playing');
        const clean=urls.filter(Boolean);
        let idx=0;
        function next(){
          if(idx>=clean.length){ host.classList.remove('is-playing'); current=null; return; }
          current=new Audio(clean[idx++]);
          current.addEventListener('ended', next, {once:true});
          current.addEventListener('error', next, {once:true});
          current.play().catch(next);
        }
        next();
      }
      document.querySelectorAll('.reader-line').forEach(btn=>{
        btn.addEventListener('click', ()=>{
          const urls=[btn.dataset.audioEn||'', btn.dataset.audioZh||''];
          playQueue(urls, btn);
        });
      });
    })();
    </script>
    """
    return f"""{css}<div class=\"reader-shell\"><section class=\"reader-hero\"><h1>{_h(title)}</h1><p>Tap any line to hear the bilingual reading on mobile. Designed for parent-guided reading practice.</p></section><div class=\"reader-frame\"><div class=\"reader-paper\"><div class=\"reader-tip\">Touch any sentence block to play the audio. The layout keeps the picture-text reading style, but turns every line into an easy listening target.</div><div class=\"reader-list\">{''.join(cards)}</div></div></div></div>{script}"""


def _audio_rel_key(audio_root: Path, f: Path) -> str:
    """Stable key preserving language subfolder, e.g. en/apple.mp3, zh/sent_xxx.mp3"""
    try:
        return f.relative_to(audio_root).as_posix()
    except Exception:
        # fallback (shouldn't normally happen)
        return f.name


def _extract_day_number(title: str) -> str:
    m = __import__("re").search(r"\bday\s*(\d+)\b", title or "", flags=__import__("re").IGNORECASE)
    return m.group(1) if m else ""


def _build_publish_slug(title: str, tags: List[str], style: str = "auto") -> str:
    style = (style or "auto").strip().lower()
    day = _extract_day_number(title)
    clean_tags = [slugify(str(t or "")) for t in (tags or []) if str(t or "").strip()]
    first_tag = clean_tags[0] if clean_tags else ""
    topic = first_tag or slugify(title.replace("Homework", "").replace("homework", ""))
    topic = __import__("re").sub(r"(^day-\d+-?|^day\d+-?|^-+)", "", topic).strip("-")
    topic = "-".join([x for x in topic.split("-")[:2] if x])
    if style == "shortcode" and day:
        return f"hw-{int(day):03d}"
    if style == "topic" and topic:
        return topic
    if style == "topic_day" and topic and day:
        return f"{topic}-{day}"
    if style == "day_topic" and day and topic:
        return f"d{int(day)}-{topic}"
    if day and topic:
        return f"d{int(day)}-{topic}"
    if topic:
        return topic
    if day:
        return f"hw-{int(day):03d}"
    return slugify(title)


def _normalize_publish_slug_mode(mode: str) -> str:
    m = (mode or "auto_lesson").strip().lower()
    if m in ("auto_lesson", "title", "custom"):
        return m
    return "auto_lesson"


def _clean_publish_group_path(path: str) -> str:
    raw = str(path or "").strip().strip("/")
    if not raw:
        return ""
    parts = [slugify(seg) for seg in raw.split("/") if slugify(seg)]
    return "/".join(parts)


def _sanitize_publish_slug(value: str) -> str:
    return slugify(str(value or "").strip())


def infer_mode_surface_from_theme(theme: str) -> tuple[str, str]:
    theme = normalize_theme_variant((theme or "sky").strip().lower())
    if theme == "sky_tiles":
        return "kid_homework", "tiles"
    if theme == "strict_dark":
        return "reading_listening", "strict_dark"
    return "standard_homework", "classic"


def _build_consistency_report(spec: Dict[str, List[Dict[str, str]]]) -> Dict[str, object]:
    import re
    vocab = spec.get("vocab", []) or []
    sentences = spec.get("sentences", []) or []
    vocab_words = [str(v.get("en") or "").strip() for v in vocab if str(v.get("en") or "").strip()]
    sent_texts = [str(s.get("en") or "").strip() for s in sentences if str(s.get("en") or "").strip()]
    used = []
    for w in vocab_words:
        if any(re.search(rf"\b{re.escape(w)}\b", s, flags=re.IGNORECASE) for s in sent_texts):
            used.append(w)
    unused = [w for w in vocab_words if w not in used]
    missing_zh = [str(v.get("en") or "").strip() for v in vocab if not str(v.get("zh") or "").strip()]
    missing_pos = [str(v.get("en") or "").strip() for v in vocab if not str(v.get("pos") or "").strip()]
    return {
        "vocab_count": len(vocab_words),
        "sentence_count": len(sent_texts),
        "vocab_seen_in_sentences": used,
        "vocab_not_seen_in_sentences": unused,
        "missing_zh": missing_zh,
        "missing_pos": missing_pos,
        "coverage_ratio": round((len(used) / max(1, len(vocab_words))), 3),
    }


def _rewrite_practice_media_urls(practice: Dict, card_url_by_stem: Dict[str, str], audio_url_by_rel: Dict[str, str]) -> Dict:
    """Map local cards/audio references inside practice JSON to uploaded WP media URLs."""
    if not isinstance(practice, dict):
        return practice

    def map_img(value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            return raw
        if raw.startswith("http://") or raw.startswith("https://"):
            return raw
        path = Path(raw)
        if path.parts and path.parts[0] == "cards":
            stem = path.stem
            return card_url_by_stem.get(stem, raw)
        return raw

    def map_audio(value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            return raw
        if raw.startswith("http://") or raw.startswith("https://"):
            return raw
        path = Path(raw)
        if path.parts and path.parts[0] == "audio":
            rel = Path(*path.parts[1:]).as_posix()
            return audio_url_by_rel.get(rel, raw)
        return raw

    for q in practice.get("questions", []) or []:
        if not isinstance(q, dict):
            continue
        if "prompt_image" in q:
            q["prompt_image"] = map_img(q.get("prompt_image", ""))
        if "prompt_audio" in q:
            q["prompt_audio"] = map_audio(q.get("prompt_audio", ""))
        for choice in q.get("choices", []) or []:
            if isinstance(choice, dict):
                if "img" in choice:
                    choice["img"] = map_img(choice.get("img", ""))
                if "audio" in choice:
                    choice["audio"] = map_audio(choice.get("audio", ""))
    return practice


def main() -> None:
    load_dotenv()

    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="", help="Path to homework text file")
    ap.add_argument("--input-image", default="", help="Path to a bilingual picture/text image for picture-reader publishing")
    ap.add_argument("--reader-title", default=None, help="Optional title override for picture-reader pages")
    ap.add_argument("--page-kind", default="lesson", choices=["lesson", "picture_reader"], help="Internal output/publish mode")
    ap.add_argument("--lesson_title", default=None)
    ap.add_argument("--publish", action="store_true", help="If set, upload to WordPress and create post/page")
    ap.add_argument("--publish-only", action="store_true", help="Publish from existing output folder (no generation)")
    ap.add_argument("--dry-run", action="store_true", help="Skip image+audio generation (debug/validation)")
    ap.add_argument("--publish_slug", default=None, help="Custom short permalink slug for WordPress publishing")
    ap.add_argument("--publish-slug-mode", default=os.getenv("WP_PUBLISH_SLUG_MODE", "auto_lesson"), choices=["auto_lesson", "title", "custom"], help="How to build the public lesson slug.")
    ap.add_argument("--publish-slug-prefix", default=os.getenv("WP_PUBLISH_SLUG_PREFIX", "lesson"), help="Prefix used in auto_lesson mode, e.g. lesson -> lesson1, lesson2")
    ap.add_argument("--wp-group-path", default=os.getenv("WP_GROUP_PATH", ""), help="Optional hierarchical page path such as beginners or beginners/a1")
    ap.add_argument("--publish_slug_style", default=os.getenv("WP_PUBLISH_SLUG_STYLE", "auto"), choices=["auto","day_topic","topic_day","topic","shortcode"], help="Rule for generating a short publish slug when using title mode")
    ap.add_argument(
        "--theme",
        default=None,
        choices=["sky", "sky_tiles", "strict_dark", "fun_mission", "strict", "fun", "app"],
        help="Lesson renderer theme for WordPress shortcode publishing.",
    )
    ap.add_argument(
        "--lesson-mode",
        default=None,
        choices=["standard_homework", "kid_homework", "reading_listening"],
        help="Optional explicit workflow profile. If omitted, inferred from theme.",
    )
    ap.add_argument(
        "--surface-variant",
        default=None,
        choices=["classic", "tiles", "strict_dark"],
        help="Optional explicit renderer surface. If omitted, inferred from theme.",
    )
    args = ap.parse_args()

    # Support both WP_BASE_URL and WP_BASE (older runs/configs).
    wp_base = (os.getenv("WP_BASE_URL", "").strip() or os.getenv("WP_BASE", "").strip())
    wp_user = os.getenv("WP_USER", "").strip()
    wp_pass = os.getenv("WP_APP_PASSWORD", "").strip()
    wp_post_type = os.getenv("WP_POST_TYPE", "page").strip()  # page works normally in your setup
    wp_render_mode = (os.getenv("WP_RENDER_MODE", "shortcode") or "shortcode").strip().lower()
    lesson_theme = normalize_theme_variant((args.theme or os.getenv("WP_LESSON_THEME", "sky") or "sky").strip().lower())
    inferred_lesson_mode, inferred_surface_variant = infer_mode_surface_from_theme(lesson_theme)
    lesson_mode = (args.lesson_mode or inferred_lesson_mode).strip().lower()
    surface_variant = (args.surface_variant or inferred_surface_variant).strip().lower()

    output_dir = Path(os.getenv("OUTPUT_DIR", "output"))
    font_path = os.getenv("FONT_PATH", "").strip() or None

    # Quiz hosting base (NOT WordPress permalinks). If not set, we still generate local quiz files.
    quiz_public_base = os.getenv("QUIZ_PUBLIC_BASE", "").rstrip("/")
    quiz_embed_mode = (os.getenv("QUIZ_EMBED_MODE", "embed") or "embed").strip().lower()

    print(f"[ENV] PYTHON={os.sys.executable}")
    print(f"[PUBLISH] THEME={lesson_theme} MODE={lesson_mode} SURFACE={surface_variant}")

    page_kind = (args.page_kind or "lesson").strip().lower()
    input_image = Path(args.input_image).expanduser().resolve() if str(args.input_image or "").strip() else None
    if input_image:
        page_kind = "picture_reader"

    if page_kind == "picture_reader":
        if not input_image or not input_image.exists():
            raise RuntimeError("--input-image is required and must point to an existing image for picture_reader mode.")
        reader_slug = slugify(args.reader_title or input_image.stem) or "picture-reader"
        reader_debug = (Path(os.getenv("OUTPUT_DIR", "output")) / reader_slug / "ocr_debug.json")
        reader_payload = parse_bilingual_image(input_image, save_debug_to=reader_debug)
        lines = reader_payload.get("lines", []) or []
        spec = {
            "title": args.reader_title or reader_payload.get("title") or input_image.stem,
            "tags": ["picture-reader", "bilingual"],
            "vocab": [],
            "sentences": [{"en": str(line.get("en") or "").strip(), "zh": str(line.get("zh") or "").strip(), "raw": str(line.get("raw") or "").strip()} for line in lines if str(line.get("en") or line.get("zh") or line.get("raw") or "").strip()],
            "picture_reader_lines": lines,
            "picture_reader_meta": {
                "source_image": str(input_image),
                "ocr_backend": reader_payload.get("ocr_backend") or "tesseract",
                "ocr_lang": reader_payload.get("ocr_lang") or "eng+chi_sim",
            },
        }
    else:
        if not str(args.input or "").strip():
            raise RuntimeError("--input is required for lesson mode.")
        hw_text = Path(args.input).read_text(encoding="utf-8", errors="ignore")
        spec = parse_homework_text(hw_text)

    dry_run = bool(args.dry_run) or (os.getenv("SKYED_DRY_RUN", "").strip().lower() in ("1", "true", "yes", "on"))

    title = args.reader_title or args.lesson_title or spec.get("title", "Homework")
    slug = slugify(title)
    publish_slug = ""
    wp_group_path = _clean_publish_group_path(args.wp_group_path or os.getenv("WP_GROUP_PATH", ""))
    publish_slug_mode = _normalize_publish_slug_mode(args.publish_slug_mode or os.getenv("WP_PUBLISH_SLUG_MODE", "auto_lesson"))
    publish_slug_prefix = _sanitize_publish_slug(args.publish_slug_prefix or os.getenv("WP_PUBLISH_SLUG_PREFIX", "lesson") or "lesson") or "lesson"
    custom_publish_slug = _sanitize_publish_slug(args.publish_slug or os.getenv("WP_PUBLISH_SLUG", "").strip())
    # NOTE: do not create output folder eagerly in --publish-only mode.
    lesson_root = (output_dir / slug)

    # Useful debug dump for every run (helps inspect parser output quickly)
    try:
        (lesson_root / "spec_debug.json").write_text(
            json.dumps(spec, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

    # publish-only mode: skip generation and just publish using existing files
    if args.publish_only:
        if not lesson_root.exists():
            raise RuntimeError(f"--publish-only requested, but lesson output folder not found: {lesson_root}")
        # sanity check
        if not (lesson_root / "lesson.html").exists():
            raise RuntimeError(
                "--publish-only requested, but lesson.html is missing.\n"
                f"Expected: {lesson_root / 'lesson.html'}\n"
                "Run generation first (without --publish-only)."
            )
    else:
        lesson_root = ensure_dir(lesson_root)
        # Clean previous run
        for name in ("cards", "flashcards", "audio", "index.html", "quiz.json", "lesson.html", "spec_debug.json", "image_specs.json", "image_report.json", "image_plans.json", "ai_status.txt", "lesson_payload.txt", "consistency_report.json"):
            p = lesson_root / name
            if p.exists():
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    try:
                        p.unlink()
                    except Exception:
                        pass

        # Re-write debug spec after cleanup
        try:
            ensure_dir(lesson_root)
            (lesson_root / "spec_debug.json").write_text(
                json.dumps(spec, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

        cards_dir = ensure_dir(lesson_root / "cards")
        audio_dir = ensure_dir(lesson_root / "audio")

        vocab_list = spec.get("vocab", []) or []
        missing_zh = [v.get("en", "") for v in vocab_list if not (v.get("zh") or "").strip()]
        if missing_zh:
            print("[WARN] Missing Chinese for vocab:", ", ".join(missing_zh))
        if len(vocab_list) == 0 and lesson_mode != "reading_listening" and page_kind != "picture_reader":
            raise RuntimeError(
                "Parser produced 0 vocab items. Check output/<slug>/spec_debug.json.\n"
                "Your homework.txt MUST include:\n"
                "#Vocabulary（词汇）：table, chair, ..."
            )

        if dry_run:
            print("[DRY RUN] Skipping image + audio generation.")
            card_files = []
            audio_files = []
        else:
            if page_kind != "picture_reader":
                t_cards = perf_counter()
                card_files = generate_vocab_cards(spec, font_path, cards_dir)
                print(f"[TIME] cards={perf_counter() - t_cards:.2f}s")
            else:
                card_files = []

            t_audio = perf_counter()
            audio_files = generate_audio(spec, audio_dir)
            print(f"[TIME] audio={perf_counter() - t_audio:.2f}s")

            if lesson_mode == "reading_listening":
                t_long_audio = perf_counter()
                spec = generate_long_audio_variants(spec, lesson_root)
                print(f"[TIME] long_audio={perf_counter() - t_long_audio:.2f}s")
                try:
                    (lesson_root / "spec_debug.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass

        _ = (card_files, audio_files)

        if page_kind != "picture_reader":
            t_quiz = perf_counter()
            quiz_json_path = generate_quiz(spec, lesson_root, n_questions=8, theme_variant=lesson_theme)
            print(f"[TIME] quiz={perf_counter() - t_quiz:.2f}s")

            template_html_path = Path("templates/quiz_index.html")
            if template_html_path.exists():
                template_html = template_html_path.read_text(encoding="utf-8", errors="ignore")
            else:
                template_html = """<!doctype html><html><head><meta charset="utf-8"><title>Quiz</title></head>
<body><div id="app">Quiz template missing.</div></body></html>"""
            (lesson_root / "index.html").write_text(template_html, encoding="utf-8")

            if quiz_json_path.name != "quiz.json":
                shutil.copy2(quiz_json_path, lesson_root / "quiz.json")
        else:
            quiz_json_path = lesson_root / "quiz.json"

    # Build LOCAL lesson.html (images inside cards, sentences section with CN + audio)
    items_local: List[Dict[str, str]] = []
    vocab_list = spec.get("vocab", []) or []
    for v in vocab_list:
        en = (v.get("en") or "").strip()
        zh = (v.get("zh") or "").strip()
        s = card_slugify(en)

        img_rel = f"cards/{s}.png"
        a_en_rel = f"audio/en/{s}.mp3"
        a_zh_rel = f"audio/zh/{s}.mp3"

        if not (lesson_root / img_rel).exists():
            img_rel = ""
        if not (lesson_root / a_en_rel).exists():
            a_en_rel = ""
        if not (lesson_root / a_zh_rel).exists():
            a_zh_rel = ""

        items_local.append({"en": en, "zh": zh, "img": img_rel, "audio_en": a_en_rel, "audio_zh": a_zh_rel})

    # Sentences (paired EN/CN) with audio
    sent_local: List[Dict[str, str]] = []
    for s in spec.get("sentences", []) or []:
        en_txt = (s.get("en") or "").strip()
        zh_txt = (s.get("zh") or "").strip()
        base = en_txt or zh_txt
        if not base:
            continue

        stem = _sentence_audio_stem(base)
        a_en_rel = f"audio/en/sent_{stem}.mp3"
        a_zh_rel = f"audio/zh/sent_{stem}.mp3"

        if not (lesson_root / a_en_rel).exists():
            a_en_rel = ""
        if not (lesson_root / a_zh_rel).exists():
            a_zh_rel = ""

        sent_local.append({"en": en_txt, "zh": zh_txt, "audio_en": a_en_rel, "audio_zh": a_zh_rel})

    t_lesson_html = perf_counter()
    if page_kind == "picture_reader":
        lesson_html_local = build_picture_reader_html(title, sent_local)
    else:
        lesson_html_local = build_lesson_html(
            title,
            items_local,
            sent_local,
            quiz_url="index.html",
            quiz_embed_mode="embed",
            quiz_note="",
        )
    print(f"[TIME] lesson_html={perf_counter() - t_lesson_html:.2f}s")
    (lesson_root / "lesson.html").write_text(lesson_html_local, encoding="utf-8")

    tag_games = discover_tag_games(lesson_root.parent, spec.get("tags", []) or [], public_base=os.getenv("TAGS_PUBLIC_BASE", "").strip())
    lesson_metadata = infer_lesson_metadata(spec, theme=lesson_theme, publish_slug="")
    try:
        write_tag_registry(lesson_root.parent, public_base=os.getenv("TAGS_PUBLIC_BASE", "").strip())
    except Exception:
        pass
    try:
        update_catalog(lesson_root.parent, lesson_root, title, lesson_metadata)
    except Exception:
        pass

    try:
        (lesson_root / "lesson_manifest.json").write_text(json.dumps({
            "title": title,
            "theme": lesson_theme,
            "lesson_mode": lesson_mode,
            "surface_variant": surface_variant,
            "tags": spec.get("tags", []) or [],
            "categories": lesson_metadata,
            "tag_games": tag_games,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    print(f"Generated: {lesson_root}")
    print(f"Lesson local path: {(lesson_root / 'lesson.html')}")
    print(f"Quiz local path: {(lesson_root / 'index.html')}")
    if (lesson_root / 'cards' / 'image_specs.json').exists():
        print(f"Image specs path: {(lesson_root / 'cards' / 'image_specs.json')}")
    if (lesson_root / 'cards' / 'image_report.json').exists():
        print(f"Image report path: {(lesson_root / 'cards' / 'image_report.json')}")

    do_publish = bool(args.publish or args.publish_only)
    if not do_publish:
        print("Skipping publish. Use --publish to upload + create page/post.")
        return

    if not (wp_base and wp_user and wp_pass):
        raise RuntimeError("Missing WP_BASE_URL / WP_USER / WP_APP_PASSWORD in .env")

    is_page_publish = (wp_post_type or "page").strip().lower() in ("page", "pages")
    if wp_group_path and not is_page_publish:
        raise RuntimeError("Hierarchical website folders require WP_POST_TYPE=page.")

    parent_page_id = ensure_page_path(
        wp_base,
        wp_user,
        wp_pass,
        path=wp_group_path,
        status="publish",
    ) if wp_group_path else None

    if publish_slug_mode == "custom" and custom_publish_slug:
        publish_slug = custom_publish_slug
        assert_slug_available(
            wp_base,
            wp_user,
            wp_pass,
            slug=publish_slug,
            parent=parent_page_id,
            post_type=wp_post_type,
        )
    elif publish_slug_mode == "title":
        publish_slug = custom_publish_slug or _build_publish_slug(title, spec.get("tags", []) or [], args.publish_slug_style)
        assert_slug_available(
            wp_base,
            wp_user,
            wp_pass,
            slug=publish_slug,
            parent=parent_page_id,
            post_type=wp_post_type,
        )
    else:
        publish_slug = next_sequential_slug(
            wp_base,
            wp_user,
            wp_pass,
            parent=parent_page_id,
            prefix=publish_slug_prefix,
            post_type=wp_post_type,
        )

    publish_rel_path = "/".join([p for p in [wp_group_path, publish_slug] if p])
    print(f"[PUBLISH] GROUP={wp_group_path or '[root]'}")
    print(f"[PUBLISH] SLUG_MODE={publish_slug_mode}")
    print(f"[PUBLISH] SLUG={publish_slug}")
    print(f"[PUBLISH] ROUTE=/{publish_rel_path}")

    # Upload media (cards + audio). NOTE: we upload the rendered cards (cards/*.png), not raw ai images.
    card_url_by_stem: Dict[str, str] = {}
    cards_dir = lesson_root / "cards"
    if cards_dir.exists():
        t_upload_cards = perf_counter()
        card_count = 0
        for f in cards_dir.glob("*.png"):
            j = upload_media(wp_base, wp_user, wp_pass, f)
            url = (j or {}).get("source_url") or ""
            if url:
                card_url_by_stem[f.stem] = url
            card_count += 1
        print(f"[TIME] upload_cards={perf_counter() - t_upload_cards:.2f}s count={card_count}")

    # IMPORTANT: key by relative path (en/apple.mp3 vs zh/apple.mp3) to avoid collisions
    audio_url_by_rel: Dict[str, str] = {}
    audio_dir = lesson_root / "audio"
    if audio_dir.exists():
        t_upload_audio = perf_counter()
        audio_count = 0
        for f in audio_dir.rglob("*.mp3"):
            j = upload_media(wp_base, wp_user, wp_pass, f)
            url = (j or {}).get("source_url") or ""
            if url:
                audio_url_by_rel[_audio_rel_key(audio_dir, f)] = url
            audio_count += 1
        print(f"[TIME] upload_audio={perf_counter() - t_upload_audio:.2f}s count={audio_count}")

    consistency_report = _build_consistency_report(spec)
    try:
        (lesson_root / "consistency_report.json").write_text(json.dumps(consistency_report, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    # Build REMOTE items using WP URLs
    items_remote: List[Dict[str, str]] = []
    for v in vocab_list:
        en = (v.get("en") or "").strip()
        zh = (v.get("zh") or "").strip()
        s = card_slugify(en)
        items_remote.append(
            {
                "en": en,
                "zh": zh,
                "img": card_url_by_stem.get(s, ""),
                "audio_en": audio_url_by_rel.get(f"en/{s}.mp3", ""),
                "audio_zh": audio_url_by_rel.get(f"zh/{s}.mp3", ""),
                "pos": (v.get("pos") or ""),
            }
        )

    sent_remote: List[Dict[str, str]] = []
    for s in spec.get("sentences", []) or []:
        en_txt = (s.get("en") or "").strip()
        zh_txt = (s.get("zh") or "").strip()
        base = en_txt or zh_txt
        if not base:
            continue
        stem = _sentence_audio_stem(base)
        sent_remote.append(
            {
                "en": en_txt,
                "zh": zh_txt,
                "audio_en": audio_url_by_rel.get(f"en/sent_{stem}.mp3", ""),
                "audio_zh": audio_url_by_rel.get(f"zh/sent_{stem}.mp3", ""),
            }
        )

    def map_block_audio(block: Dict) -> Dict:
        if not isinstance(block, dict):
            return {}
        out = dict(block)
        mapped = []
        for item in block.get("audio_variants", []) or []:
            if not isinstance(item, dict):
                continue
            rel = str(item.get("url") or "").strip()
            mapped.append({**item, "url": audio_url_by_rel.get(rel, rel)})
        if mapped:
            out["audio_variants"] = mapped
        return out

    reading_remote = map_block_audio(spec.get("reading_block") or {})
    listening_remote = map_block_audio(spec.get("listening_block") or {})

    # Remote practice URL:
    # If QUIZ_PUBLIC_BASE is configured to a real static host, use it.
    # Otherwise shortcode mode will render in-page practice from payload.
    quiz_note = ""
    if quiz_public_base:
        quiz_url = f"{quiz_public_base}/{publish_rel_path}/index.html"
    else:
        quiz_url = ""
        quiz_note = "In-page practice is enabled. Static practice hosting is not configured."

    if wp_render_mode in ("raw_html", "html"):
        # Legacy mode: publish full HTML into a Gutenberg Custom HTML block.
        # NOTE: requires unfiltered_html capability for the API user, otherwise WP will strip <style>/<audio>/<iframe> etc.
        html_out = build_lesson_html(
            title,
            items_remote,
            sent_remote,
            quiz_url,
            quiz_embed_mode=quiz_embed_mode,
            quiz_note=quiz_note,
        )

        t_create_post = perf_counter()
        post = create_post(
            wp_base,
            wp_user,
            wp_pass,
            title=title,
            html=html_out,
            post_type=wp_post_type,
            status="publish",
            content_mode="html_block",
            slug=publish_slug,
            parent=parent_page_id if is_page_publish else None,
        )
        print(f"[TIME] create_post={perf_counter() - t_create_post:.2f}s")
    else:
        # Recommended mode: publish a shortcode block that renders the lesson via a WP plugin.
        # This survives restrictive WP roles (no unfiltered_html) and allows real styling + audio + quiz JS.
        quiz_dict = {}
        if page_kind != "picture_reader":
            try:
                qp = lesson_root / "quiz.json"
                if qp.exists():
                    quiz_dict = json.loads(qp.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                quiz_dict = {}

            quiz_dict = _rewrite_practice_media_urls(quiz_dict, card_url_by_stem, audio_url_by_rel)

        payload_categories = infer_lesson_metadata(spec, theme=lesson_theme, publish_slug=publish_slug)
        payload = {
            "title": title,
            "slug": publish_slug,
            "tags": spec.get("tags", []) or [],
            "categories": payload_categories,
            "tag_games": tag_games,
            "vocab": items_remote,
            "sentences": sent_remote,
            "reading_block": reading_remote,
            "listening_block": listening_remote,
            "comprehension_questions": spec.get("comprehension_questions", []) or [],
            "quiz": quiz_dict,
            "practice": quiz_dict,
            "renderer_theme": lesson_theme,
            "page_kind": page_kind,
            "bilingual_lines": sent_remote if page_kind == "picture_reader" else [],
            "consistency": consistency_report,
            "meta": {
                "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                "quiz_public_base": quiz_public_base,
                "quiz_embed_mode": quiz_embed_mode,
                "tags_public_base": os.getenv("TAGS_PUBLIC_BASE", "").strip(),
                "theme_variant": lesson_theme,
                "page_kind": page_kind,
                "wp_group_path": wp_group_path,
                "publish_slug": publish_slug,
                "publish_slug_mode": publish_slug_mode,
                "publish_rel_path": publish_rel_path,
                "noindex_internal_catalog": True,
            },
        }

        payload_path = lesson_root / "lesson_payload.txt"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        t_payload_upload = perf_counter()
        payload_media = upload_media(wp_base, wp_user, wp_pass, payload_path)
        print(f"[TIME] upload_payload={perf_counter() - t_payload_upload:.2f}s")
        data_url = (payload_media or {}).get("source_url") or ""
        if not data_url:
            raise RuntimeError("Failed to upload lesson_payload.txt to WordPress (no source_url).")

        shortcode = f'[skyed_lesson data_url="{data_url}" theme="{lesson_theme}"]'

        t_create_post = perf_counter()
        post = create_post(
            wp_base,
            wp_user,
            wp_pass,
            title=title,
            html=shortcode,
            post_type=wp_post_type,
            status="publish",
            content_mode="shortcode_block",
            slug=publish_slug,
            parent=parent_page_id if is_page_publish else None,
        )
        print(f"[TIME] create_post={perf_counter() - t_create_post:.2f}s")
    print(f"Publish slug: {publish_slug}")
    print("Published post:")
    if isinstance(post, dict):
        print(post.get("link") or post)
    else:
        print(post)


if __name__ == "__main__":
    main()