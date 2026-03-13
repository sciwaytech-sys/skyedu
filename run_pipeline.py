from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

from skyed.parser import parse_homework_text
from skyed.utils import slugify, ensure_dir
from skyed.cards import generate_vocab_cards, slugify as card_slugify
from skyed.tts_edge import generate_audio
from skyed.quizgen import generate_quiz
from skyed.wp import upload_media, create_post, ensure_page_path, next_sequential_slug, assert_slug_available


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


def _rewrite_practice_media_urls(practice: Dict, card_url_by_stem: Dict[str, str]) -> Dict:
    """Map local cards/<stem>.png references inside practice JSON to uploaded WP media URLs."""
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

    for q in practice.get("questions", []) or []:
        if not isinstance(q, dict):
            continue
        if "prompt_image" in q:
            q["prompt_image"] = map_img(q.get("prompt_image", ""))
        for choice in q.get("choices", []) or []:
            if isinstance(choice, dict) and "img" in choice:
                choice["img"] = map_img(choice.get("img", ""))
    return practice


def main() -> None:
    load_dotenv()

    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Path to homework text file")
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
        choices=["sky", "strict", "fun", "app"],
        help="Lesson renderer theme for WordPress shortcode publishing.",
    )
    args = ap.parse_args()

    # Support both WP_BASE_URL and WP_BASE (older runs/configs).
    wp_base = (os.getenv("WP_BASE_URL", "").strip() or os.getenv("WP_BASE", "").strip())
    wp_user = os.getenv("WP_USER", "").strip()
    wp_pass = os.getenv("WP_APP_PASSWORD", "").strip()
    wp_post_type = os.getenv("WP_POST_TYPE", "page").strip()  # page works normally in your setup
    wp_render_mode = (os.getenv("WP_RENDER_MODE", "shortcode") or "shortcode").strip().lower()
    lesson_theme = (args.theme or os.getenv("WP_LESSON_THEME", "sky") or "sky").strip().lower()
    if lesson_theme not in ("sky", "strict", "fun", "app"):
        lesson_theme = "sky"

    output_dir = Path(os.getenv("OUTPUT_DIR", "output"))
    font_path = os.getenv("FONT_PATH", "").strip() or None

    # Quiz hosting base (NOT WordPress permalinks). If not set, we still generate local quiz files.
    quiz_public_base = os.getenv("QUIZ_PUBLIC_BASE", "").rstrip("/")
    quiz_embed_mode = (os.getenv("QUIZ_EMBED_MODE", "embed") or "embed").strip().lower()

    print(f"[ENV] PYTHON={os.sys.executable}")
    print(f"[PUBLISH] THEME={lesson_theme}")

    hw_text = Path(args.input).read_text(encoding="utf-8", errors="ignore")
    spec = parse_homework_text(hw_text)

    dry_run = bool(args.dry_run) or (os.getenv("SKYED_DRY_RUN", "").strip().lower() in ("1", "true", "yes", "on"))

    title = args.lesson_title or spec.get("title", "Homework")
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
        if len(vocab_list) == 0:
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
            # Generate cards + audio (EN + ZH for vocab + sentences)
            card_files = generate_vocab_cards(spec, font_path, cards_dir)
            audio_files = generate_audio(spec, audio_dir)

        # Keep variables referenced so linters don't complain in stricter configs
        _ = (card_files, audio_files)

        # Generate quiz into lesson_root
        quiz_json_path = generate_quiz(spec, lesson_root, n_questions=8)

        template_html_path = Path("templates/quiz_index.html")
        if template_html_path.exists():
            template_html = template_html_path.read_text(encoding="utf-8", errors="ignore")
        else:
            # fallback: minimal loader (expects quiz.json alongside)
            template_html = """<!doctype html><html><head><meta charset="utf-8"><title>Quiz</title></head>
<body><div id="app">Quiz template missing.</div></body></html>"""
        (lesson_root / "index.html").write_text(template_html, encoding="utf-8")

        if quiz_json_path.name != "quiz.json":
            shutil.copy2(quiz_json_path, lesson_root / "quiz.json")

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

    # Local quiz iframe points to local index.html (works as file)
    lesson_html_local = build_lesson_html(
        title,
        items_local,
        sent_local,
        quiz_url="index.html",
        quiz_embed_mode="embed",
        quiz_note="",
    )
    (lesson_root / "lesson.html").write_text(lesson_html_local, encoding="utf-8")

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
        for f in cards_dir.glob("*.png"):
            j = upload_media(wp_base, wp_user, wp_pass, f)
            url = (j or {}).get("source_url") or ""
            if url:
                card_url_by_stem[f.stem] = url

    # IMPORTANT: key by relative path (en/apple.mp3 vs zh/apple.mp3) to avoid collisions
    audio_url_by_rel: Dict[str, str] = {}
    audio_dir = lesson_root / "audio"
    if audio_dir.exists():
        for f in audio_dir.rglob("*.mp3"):
            j = upload_media(wp_base, wp_user, wp_pass, f)
            url = (j or {}).get("source_url") or ""
            if url:
                audio_url_by_rel[_audio_rel_key(audio_dir, f)] = url

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
    else:
        # Recommended mode: publish a shortcode block that renders the lesson via a WP plugin.
        # This survives restrictive WP roles (no unfiltered_html) and allows real styling + audio + quiz JS.
        quiz_dict = {}
        try:
            qp = lesson_root / "quiz.json"
            if qp.exists():
                quiz_dict = json.loads(qp.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            quiz_dict = {}

        quiz_dict = _rewrite_practice_media_urls(quiz_dict, card_url_by_stem)

        payload = {
            "title": title,
            "slug": publish_slug,
            "tags": spec.get("tags", []) or [],
            "vocab": items_remote,
            "sentences": sent_remote,
            "quiz": quiz_dict,
            "practice": quiz_dict,
            "consistency": consistency_report,
            "meta": {
                "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                "quiz_public_base": quiz_public_base,
                "quiz_embed_mode": quiz_embed_mode,
                "theme_variant": lesson_theme,
                "wp_group_path": wp_group_path,
                "publish_slug": publish_slug,
                "publish_slug_mode": publish_slug_mode,
                "publish_rel_path": publish_rel_path,
            },
        }

        payload_path = lesson_root / "lesson_payload.txt"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        payload_media = upload_media(wp_base, wp_user, wp_pass, payload_path)
        data_url = (payload_media or {}).get("source_url") or ""
        if not data_url:
            raise RuntimeError("Failed to upload lesson_payload.txt to WordPress (no source_url).")

        shortcode = f'[skyed_lesson data_url="{data_url}" theme="{lesson_theme}"]'

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
    print(f"Publish slug: {publish_slug}")
    print("Published post:")
    if isinstance(post, dict):
        print(post.get("link") or post)
    else:
        print(post)


if __name__ == "__main__":
    main()