from __future__ import annotations

import argparse
import hashlib
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
from skyed.wp import upload_media, create_post


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


def build_lesson_html(
    title: str,
    vocab_items: List[Dict[str, str]],
    sentence_items: List[Dict[str, str]],
    quiz_iframe_url: str,
) -> str:
    """
    LMS-style lesson page:
      - Vocab grid: each card contains image + EN+CN + audio (EN+CN).
      - Sentences section: EN+CN line pairs + audio (EN+CN).
      - Quiz embedded below via iframe.
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

      iframe{width:100%;height:900px;border:0;border-radius:16px;box-shadow:var(--shadow);background:#fff;}
    </style>
    """

    vocab_cards: List[str] = []
    for it in vocab_items:
        en = it.get("en", "")
        zh = it.get("zh", "")
        img = it.get("img", "")
        a_en = it.get("audio_en", "")
        a_zh = it.get("audio_zh", "")

        card = ['<div class="card">']
        card.append('<div class="media">')
        if img:
            card.append(f'<img src="{img}" alt="{en}" loading="lazy">')
        card.append("</div>")
        card.append('<div class="bd">')
        card.append('<div class="row">')
        card.append(f'<div class="en">{en}</div>')
        card.append(f'<div class="zh">{zh}</div>' if zh else '<div class="zh"></div>')
        card.append("</div>")
        if a_en:
            card.append(f'<audio controls src="{a_en}"></audio>')
        if a_zh:
            card.append(f'<audio controls src="{a_zh}"></audio>')
        card.append("</div></div>")
        vocab_cards.append("".join(card))

    sent_cards: List[str] = []
    for it in sentence_items:
        en = it.get("en", "")
        zh = it.get("zh", "")
        a_en = it.get("audio_en", "")
        a_zh = it.get("audio_zh", "")

        row = ['<div class="skyed-sent">']
        row.append('<div class="txt">')
        if en:
            row.append(f"<div><b>EN:</b> {en}</div>")
        if zh:
            row.append(f"<div><b>CN:</b> {zh}</div>")
        row.append("</div>")
        row.append('<div class="aud">')
        if a_en:
            row.append(f'<audio controls src="{a_en}"></audio>')
        if a_zh:
            row.append(f'<audio controls src="{a_zh}"></audio>')
        row.append("</div></div>")
        sent_cards.append("".join(row))

    html = f"""{css}
    <div class="wrap">
      <div class="hero">
        <h2>{title}</h2>
        <div class="sub">Vocabulary → Sentences → Quiz</div>
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

      <div class="h3">Quiz</div>
      <iframe src="{quiz_iframe_url}" loading="lazy"></iframe>
    </div>
    """
    return html


def main() -> None:
    load_dotenv()

    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Path to homework text file")
    ap.add_argument("--lesson_title", default=None)
    ap.add_argument("--publish", action="store_true", help="If set, upload to WordPress and create post/page")
    ap.add_argument("--publish-only", action="store_true", help="Publish from existing output folder (no generation)")
    args = ap.parse_args()

    wp_base = os.getenv("WP_BASE_URL", "").strip()
    wp_user = os.getenv("WP_USER", "").strip()
    wp_pass = os.getenv("WP_APP_PASSWORD", "").strip()
    wp_post_type = os.getenv("WP_POST_TYPE", "page").strip()  # you said page works normally

    output_dir = Path(os.getenv("OUTPUT_DIR", "output"))
    font_path = os.getenv("FONT_PATH", "").strip() or None

    # Quiz hosting base (NOT WordPress permalinks). If not set, we still generate local quiz files.
    quiz_public_base = os.getenv("QUIZ_PUBLIC_BASE", "").rstrip("/")

    hw_text = Path(args.input).read_text(encoding="utf-8", errors="ignore")
    spec = parse_homework_text(hw_text)

    title = args.lesson_title or spec.get("title", "Homework")
    slug = slugify(title)
    lesson_root = ensure_dir(output_dir / slug)

    # publish-only mode: skip generation and just publish using existing files
    if not args.publish_only:
        # Clean previous run
        for name in ("cards", "audio", "index.html", "quiz.json", "lesson.html", "spec_debug.json"):
            p = lesson_root / name
            if p.exists():
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    try:
                        p.unlink()
                    except Exception:
                        pass

        cards_dir = ensure_dir(lesson_root / "cards")
        audio_dir = ensure_dir(lesson_root / "audio")

        vocab_list = spec.get("vocab", []) or []
        if len(vocab_list) == 0:
            # Write debug spec so GUI error points to a file
            (lesson_root / "spec_debug.json").write_text(
                __import__("json").dumps(spec, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            raise RuntimeError(
                "Parser produced 0 vocab items. Check output/<slug>/spec_debug.json.\n"
                "Your homework.txt MUST include:\n"
                "#Vocabulary（词汇）：table, chair, ..."
            )

        # Generate cards + audio (EN + ZH for vocab + sentences)
        card_files = generate_vocab_cards(spec, font_path, cards_dir)
        audio_files = generate_audio(spec, audio_dir)

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
    lesson_html_local = build_lesson_html(title, items_local, sent_local, quiz_iframe_url="index.html")
    (lesson_root / "lesson.html").write_text(lesson_html_local, encoding="utf-8")

    print(f"Generated: {lesson_root}")
    print(f"Lesson local path: {(lesson_root / 'lesson.html')}")
    print(f"Quiz local path: {(lesson_root / 'index.html')}")

    if not args.publish:
        print("Skipping publish. Use --publish to upload + create page/post.")
        return

    if not (wp_base and wp_user and wp_pass):
        raise RuntimeError("Missing WP_BASE_URL / WP_USER / WP_APP_PASSWORD in .env")

    # Upload media (cards + audio). NOTE: we upload the rendered cards (cards/*.png) not raw ai images.
    card_url_by_stem: Dict[str, str] = {}
    cards_dir = lesson_root / "cards"
    if cards_dir.exists():
        for f in cards_dir.glob("*.png"):
            j = upload_media(wp_base, wp_user, wp_pass, f)
            url = (j or {}).get("source_url") or ""
            if url:
                card_url_by_stem[f.stem] = url

    audio_url_by_stem: Dict[str, str] = {}
    audio_dir = lesson_root / "audio"
    if audio_dir.exists():
        for f in audio_dir.rglob("*.mp3"):
            j = upload_media(wp_base, wp_user, wp_pass, f)
            url = (j or {}).get("source_url") or ""
            if url:
                audio_url_by_stem[f.stem] = url

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
                "audio_en": audio_url_by_stem.get(s, ""),
                "audio_zh": audio_url_by_stem.get(s, ""),
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
                "audio_en": audio_url_by_stem.get(f"sent_{stem}", ""),
                "audio_zh": audio_url_by_stem.get(f"sent_{stem}", ""),
            }
        )

    # Remote quiz URL:
    # If QUIZ_PUBLIC_BASE is configured to a real static host (recommended), use it.
    # Otherwise, do NOT point iframe back to the WordPress lesson permalink (prevents recursion).
    if quiz_public_base:
        quiz_url = f"{quiz_public_base}/{slug}/index.html"
    else:
        # Safe fallback: no iframe (avoid recursive embed)
        quiz_url = "about:blank"

    html = build_lesson_html(title, items_remote, sent_remote, quiz_url)

    post = create_post(
        wp_base,
        wp_user,
        wp_pass,
        title=title,
        html=html,
        post_type=wp_post_type,
        status="publish",
    )
    print("Published post:")
    print(post.get("link"))


if __name__ == "__main__":
    main()
