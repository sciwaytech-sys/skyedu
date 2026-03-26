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
from skyed.lesson_metadata import infer_categories
from skyed.tag_registry import discover_tag_games
from skyed.picture_reader import parse_picture_to_reader_spec
from skyed.utils import slugify, ensure_dir
from skyed.cards import generate_vocab_cards, slugify as card_slugify
from skyed.tts_edge import generate_audio, generate_long_audio_variants, generate_word_audio_set
from skyed.quizgen import generate_quiz, normalize_theme_variant
from skyed.wp import upload_media, create_post, ensure_page_path, next_sequential_slug, assert_slug_available
from skyed.tag_gamegen import export_tag_s_touch_listen_cards


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



def _log_step(message: str) -> None:
    print(str(message or ""), flush=True)


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
    extra_audio_items: Optional[List[Dict[str, str]]] = None,
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
      .extraaud{display:flex;flex-direction:column;gap:10px;}
      .extraaud .track{background:var(--card);border:1px solid var(--stroke);border-radius:16px;box-shadow:var(--shadow);padding:12px;}
      .extraaud .track b{display:block;margin-bottom:8px;color:#0f172a;}
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

    extra_audio_block: List[str] = []
    if extra_audio_items:
        section_title = "Extra Audio"
        for item in extra_audio_items:
            candidate_title = str(item.get("title") or "").strip()
            if candidate_title:
                section_title = candidate_title
                break
        extra_audio_block.append(f'<div class="h3">{_h(section_title)}</div>')
        extra_audio_block.append('<div class="extraaud">')
        for item in extra_audio_items:
            label = _h(str(item.get("label") or section_title))
            url = _hu(str(item.get("url") or ""))
            if not url:
                continue
            extra_audio_block.append(f'<div class="track"><b>{label}</b><audio controls src="{url}"></audio></div>')
        extra_audio_block.append('</div>')

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

      {''.join(extra_audio_block)}

      <div class="h3">Practice</div>
      {''.join(quiz_block)}
    </div>
    """
    return html_out


def build_picture_reader_html(
    title: str,
    sentence_items: List[Dict[str, str]],
    *,
    cover_image: str = "",
) -> str:
    css = """
    <style>
      :root{
        --max:880px; --r:28px; --stroke:rgba(15,23,42,.10);
        --bg:#fff8ef; --card:#ffffff; --card-2:#fffaf3; --muted:#7d6656;
        --brand1:#f08c00; --brand2:#ffbf69; --shadow:0 18px 40px rgba(202,120,27,.12);
      }
      body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;background:var(--bg);color:#3a2b20;}
      .wrap{max-width:var(--max);margin:0 auto;padding:18px 14px 56px;}
      .hero{background:linear-gradient(135deg,var(--brand1),var(--brand2));color:#fff;border-radius:30px;padding:18px 18px 14px;box-shadow:var(--shadow);margin-bottom:16px;}
      .hero h2{margin:0;font-size:22px;line-height:1.25;}
      .hero .sub{margin-top:6px;font-size:13px;opacity:.95;}
      .hero .meta{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;}
      .chip{display:inline-flex;align-items:center;padding:8px 14px;border-radius:999px;background:rgba(255,255,255,.16);color:#fff;font-size:12px;font-weight:800;}
      .sheet{background:linear-gradient(180deg,var(--card),var(--card-2));border:1px solid var(--stroke);border-radius:30px;box-shadow:var(--shadow);overflow:hidden;}
      .sheet__top{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 16px 12px;border-bottom:1px solid rgba(240,140,0,.10);}
      .pill{display:inline-flex;align-items:center;padding:8px 14px;border-radius:999px;background:rgba(240,140,0,.12);color:#b15d00;font-size:13px;font-weight:800;}
      .note{font-size:13px;color:var(--muted);}
      .flow{padding:10px 14px 6px;}
      .line{display:grid;grid-template-columns:34px minmax(0,1fr) auto;gap:12px;width:100%;text-align:left;border:0;background:transparent;padding:12px 4px;border-radius:18px;cursor:pointer;transition:background .16s ease, transform .16s ease;}
      .line + .line{border-top:1px dashed rgba(180,123,41,.16);}
      .line:hover,.line:focus{background:rgba(255,191,105,.12);outline:none;}
      .line.is-playing{background:rgba(240,140,0,.08);}
      .idx{display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;border-radius:999px;background:rgba(240,140,0,.12);color:#b15d00;font-size:12px;font-weight:800;margin-top:2px;}
      .body{min-width:0;}
      .label{display:block;font-size:11px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:#c07a1f;margin-bottom:4px;}
      .label.zh{margin-top:10px;color:#8b6b52;}
      .en{display:inline;font-size:22px;font-weight:900;line-height:1.52;padding:1px 4px;border-radius:10px;transition:background .15s ease, box-shadow .15s ease;}
      .zh{display:inline-block;font-size:18px;line-height:1.68;color:#4b3a2e;padding:1px 4px;border-radius:10px;transition:background .15s ease, box-shadow .15s ease;}
      .line.is-speaking-en .en,.line.is-speaking-zh .zh{background:rgba(255,191,105,.24);box-shadow:0 0 0 6px rgba(255,191,105,.12);}
      .tap{align-self:center;white-space:nowrap;font-size:13px;color:#b15d00;font-weight:800;}
      .art{padding:4px 16px 16px;}
      .art img{display:block;width:100%;max-height:260px;object-fit:cover;border-radius:22px;border:1px solid rgba(240,140,0,.10);}
      @media (max-width:640px){
        .sheet__top{align-items:flex-start;flex-direction:column;gap:8px;}
        .line{grid-template-columns:32px minmax(0,1fr);gap:10px;padding:12px 2px;}
        .tap{grid-column:2;color:var(--muted);font-size:12px;margin-top:2px;}
        .en{font-size:19px;}
        .zh{font-size:16px;}
      }
    </style>
    """

    rows = []
    for idx, it in enumerate(sentence_items):
        en = _h((it.get("en") or "").strip())
        zh = _h((it.get("zh") or "").strip())
        a_en = _hu((it.get("audio_en") or "").strip())
        a_zh = _hu((it.get("audio_zh") or "").strip())
        if not en and not zh:
            continue
        row = [f'<button class="line" type="button" data-audio-en="{a_en}" data-audio-zh="{a_zh}">']
        row.append(f'<span class="idx">{idx + 1}</span>')
        row.append('<span class="body">')
        if en:
            row.append('<span class="label">English</span>')
            row.append(f'<span class="en">{en}</span>')
        if zh:
            row.append('<span class="label zh">Chinese</span>')
            row.append(f'<span class="zh">{zh}</span>')
        row.append('</span>')
        row.append('<span class="tap">Tap to listen</span>')
        row.append('</button>')
        rows.append(''.join(row))

    cover_html = f'<div class="art"><img src="{_hu(cover_image)}" alt="{_h(title)}" loading="lazy"></div>' if cover_image else ''

    parts = [
        css,
        '<div class="wrap">',
        '  <div class="hero">',
        '    <div class="pill" style="background:rgba(255,255,255,.18);color:#fff;">Sky Reading Frame</div>',
        f'    <h2>{_h(title)}</h2>',
        '    <div class="sub">Touch any line to hear it. English plays first, then Chinese.</div>',
        f'    <div class="meta"><span class="chip">{len(rows)} lines</span><span class="chip">Touch to listen</span></div>',
        '  </div>',
        '  <section class="sheet">',
        '    <div class="sheet__top"><div class="pill">Interactive picture text</div><div class="note">Single reading block for fast mobile replay.</div></div>',
        f'    <div class="flow">{"".join(rows)}</div>',
        f'    {cover_html}',
        '  </section>',
        '</div>',
        """<script>
    (function(){
      const entries = document.querySelectorAll('.line');
      let current = null;
      let currentEntry = null;
      function clearState(){ entries.forEach(function(el){ el.classList.remove('is-playing','is-speaking-en','is-speaking-zh'); }); }
      function stopCurrent(){ if (current) { try { current.pause(); current.currentTime = 0; } catch(e){} } current = null; currentEntry = null; clearState(); }
      function playUrl(url, onEnd){ if (!url) { if (onEnd) onEnd(); return; } const audio = new Audio(url); current = audio; audio.onended = function(){ if (onEnd) onEnd(); }; audio.onerror = function(){ if (onEnd) onEnd(); }; audio.play().catch(function(){ if (onEnd) onEnd(); }); }
      function playEntry(entry){ if (currentEntry === entry) { stopCurrent(); return; } stopCurrent(); currentEntry = entry; const enUrl = entry.getAttribute('data-audio-en') || ''; const zhUrl = entry.getAttribute('data-audio-zh') || ''; entry.classList.add('is-playing'); if (enUrl) { entry.classList.add('is-speaking-en'); playUrl(enUrl, function(){ entry.classList.remove('is-speaking-en'); if (zhUrl) { entry.classList.add('is-speaking-zh'); playUrl(zhUrl, function(){ stopCurrent(); }); } else { stopCurrent(); } }); } else if (zhUrl) { entry.classList.add('is-speaking-zh'); playUrl(zhUrl, function(){ stopCurrent(); }); } }
      entries.forEach(function(entry){ entry.addEventListener('click', function(){ playEntry(entry); }); entry.addEventListener('keydown', function(ev){ if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); playEntry(entry); } }); });
    })();
    </script>""",
    ]
    return ''.join(parts)


def _audio_rel_key(audio_root: Path, f: Path) -> str:
    """Stable key preserving language subfolder, e.g. en/apple.mp3, zh/sent_xxx.mp3"""
    try:
        return f.relative_to(audio_root).as_posix()
    except Exception:
        # fallback (shouldn't normally happen)
        return f.name


AUDIO_FILE_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".aac"}


def _copy_special_audio_assets_from_env(lesson_root: Path) -> List[Dict[str, str]]:
    enabled = (os.environ.get("SKYED_SPECIAL_AUDIO_ENABLED", "0") or "0").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return []
    src_dir_raw = (os.environ.get("SKYED_SPECIAL_AUDIO_DIR", "") or "").strip()
    if not src_dir_raw:
        return []
    src_dir = Path(src_dir_raw).expanduser()
    if not src_dir.exists() or not src_dir.is_dir():
        raise RuntimeError(f"Special lesson audio folder not found: {src_dir}")
    dst_dir = ensure_dir(Path(lesson_root) / "audio" / "manual")
    title_default = (os.environ.get("SKYED_SPECIAL_AUDIO_TITLE", "Extra Audio") or "Extra Audio").strip() or "Extra Audio"
    items: List[Dict[str, str]] = []
    for src in sorted(src_dir.iterdir(), key=lambda p: p.name.lower()):
        if not src.is_file() or src.suffix.lower() not in AUDIO_FILE_EXTENSIONS:
            continue
        safe_name = ''.join(ch if ch.isalnum() or ch in '._-' else '_' for ch in src.name)
        dst = dst_dir / safe_name
        shutil.copy2(src, dst)
        label = src.stem.replace('_', ' ').replace('-', ' ').strip() or title_default
        items.append({
            "label": label,
            "title": title_default,
            "url": str(dst.relative_to(lesson_root)).replace('\\', '/'),
        })
    return items


def _map_special_audio_items(items: List[Dict[str, str]], audio_url_by_rel: Dict[str, str]) -> List[Dict[str, str]]:
    mapped: List[Dict[str, str]] = []
    for item in items or []:
        rel = str(item.get("url") or "").strip()
        mapped.append({
            "label": str(item.get("label") or "Extra Audio").strip() or "Extra Audio",
            "title": str(item.get("title") or "Extra Audio").strip() or "Extra Audio",
            "url": audio_url_by_rel.get(rel, rel),
        })
    return mapped


def _copy_ng_happy_practice_assets_from_env(lesson_root: Path) -> List[Dict[str, str]]:
    raw = (os.environ.get("SKYED_NG_AUDIO_FIELDS", "") or "").strip()
    if not raw:
        return []
    try:
        mapping = json.loads(raw)
    except Exception as exc:
        raise RuntimeError(f"Invalid SKYED_NG_AUDIO_FIELDS JSON: {exc}")
    if not isinstance(mapping, dict):
        return []
    dst_dir = ensure_dir(Path(lesson_root) / "audio" / "ng_happy_practice")
    items: List[Dict[str, str]] = []
    for label, src_value in mapping.items():
        label_text = str(label or "").strip()
        src_raw = str(src_value or "").strip()
        if not label_text or not src_raw:
            continue
        src = Path(src_raw).expanduser()
        if not src.exists() or not src.is_file():
            raise RuntimeError(f"NG audio file not found: {src}")
        if src.suffix.lower() not in AUDIO_FILE_EXTENSIONS:
            raise RuntimeError(f"Unsupported NG audio format: {src.name}")
        safe_name = ''.join(ch if ch.isalnum() or ch in '._-' else '_' for ch in src.name)
        dst = dst_dir / safe_name
        shutil.copy2(src, dst)
        items.append({
            "label": label_text,
            "title": "Happy Practice",
            "url": str(dst.relative_to(lesson_root)).replace('\\', '/'),
        })
    return items


def _build_ng_tag_game(spec: Dict[str, List[Dict[str, str]]], lesson_root: Path, title: str) -> Optional[str]:
    tags = [str(t or "").strip() for t in (spec.get("tags") or []) if str(t or "").strip()]
    tag = (tags[0] if tags else slugify(title) or "ng").strip()
    vocab = spec.get("vocab", []) or []
    if not vocab:
        return None

    touch_audio_dir = ensure_dir(Path(lesson_root) / "audio" / "ng_touch")
    touch_voice = (os.environ.get("SKYED_NG_TAG_VOICE") or "en-US-GuyNeural").strip() or "en-US-GuyNeural"
    touch_rate = (os.environ.get("SKYED_NG_TAG_RATE") or (os.environ.get("SKYED_TTS_RATE") or "-10%")).strip() or "-10%"
    word_jobs = []
    for v in vocab:
        en = str(v.get("en") or "").strip()
        if en:
            word_jobs.append((card_slugify(en), en))
    audio_map = generate_word_audio_set(word_jobs, touch_audio_dir, voice=touch_voice, rate=touch_rate)

    vocab_for_game: List[Dict[str, str]] = []
    for v in vocab:
        en = str(v.get("en") or "").strip()
        if not en:
            continue
        stem = card_slugify(en)
        img_rel = f"cards/{stem}.png"
        if not (Path(lesson_root) / img_rel).exists():
            img_rel = ""
        audio_path = audio_map.get(stem)
        audio_rel = str(audio_path.relative_to(lesson_root)).replace('\\', '/') if audio_path and audio_path.exists() else ""
        vocab_for_game.append({
            "en": en,
            "zh": str(v.get("zh") or "").strip(),
            "img": img_rel,
            "audio_en": audio_rel,
            "pos": str(v.get("pos") or "").strip(),
        })

    game_root = export_tag_s_touch_listen_cards(
        tag=tag,
        vocab=vocab_for_game,
        out_dir=Path(os.getenv("OUTPUT_DIR", "output")) / "tag_s",
        lesson_assets_root=Path(lesson_root),
        game_id=f"ng_touch_{slugify(title) or 'lesson'}",
        title=f"{title} — Touch and Listen",
    )
    return str(game_root)


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
    if theme == "ng":
        return "standard_homework", "ng"
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
    ap.add_argument("--input", required=False, help="Path to homework text file")
    ap.add_argument("--page-kind", default="lesson", choices=["lesson", "picture_reader"], help="Publishing flow kind.")
    ap.add_argument("--input-image", default=None, help="Path to picture source for picture_reader mode.")
    ap.add_argument("--reader-title", default=None, help="Optional manual title for picture_reader mode.")
    ap.add_argument("--ocr-backend", default=os.getenv("SKYED_OCR_BACKEND", "auto"), choices=["auto", "tesseract", "easyocr", "paddle"], help="OCR backend for picture_reader mode.")
    ap.add_argument("--ocr-device", default=os.getenv("SKYED_OCR_DEVICE", "cpu"), choices=["cpu", "cuda"], help="OCR device hint for picture_reader mode.")
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
        choices=["sky", "sky_tiles", "strict_dark", "fun_mission", "strict", "fun", "app", "ng"],
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
        choices=["classic", "tiles", "strict_dark", "ng"],
        help="Optional explicit renderer surface. If omitted, inferred from theme.",
    )
    args = ap.parse_args()

    # Support both WP_BASE_URL and WP_BASE (older runs/configs).
    wp_base = (os.getenv("WP_BASE_URL", "").strip() or os.getenv("WP_BASE", "").strip())
    wp_user = os.getenv("WP_USER", "").strip()
    wp_pass = os.getenv("WP_APP_PASSWORD", "").strip()
    wp_post_type = os.getenv("WP_POST_TYPE", "page").strip()  # page works normally in your setup
    wp_render_mode = (os.getenv("WP_RENDER_MODE", "shortcode") or "shortcode").strip().lower()
    default_theme = "fun_mission" if ((args.page_kind or "lesson").strip().lower() == "picture_reader") else "sky"
    lesson_theme = normalize_theme_variant((args.theme or os.getenv("WP_LESSON_THEME", default_theme) or default_theme).strip().lower())
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
    hw_text = ""
    ocr_debug: Dict[str, object] = {}
    if page_kind == "picture_reader":
        if not args.input_image:
            raise RuntimeError("picture_reader mode requires --input-image")
        spec = parse_picture_to_reader_spec(
            args.input_image,
            title=args.reader_title or args.lesson_title or "",
            tags=[],
            debug_out=None,
            ocr_backend=args.ocr_backend,
            ocr_device=args.ocr_device,
        )
        ocr_debug = dict(spec.get("picture_reader") or {})
    else:
        if not args.input:
            raise RuntimeError("lesson mode requires --input")
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

    special_audio_items_local: List[Dict[str, str]] = []

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
        for name in ("cards", "flashcards", "audio", "index.html", "quiz.json", "lesson.html", "spec_debug.json", "image_specs.json", "image_report.json", "image_plans.json", "ai_status.txt", "lesson_payload.txt", "consistency_report.json", "ocr_debug.json", "source_image"):
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
        source_image_dir = ensure_dir(lesson_root / "source_image") if page_kind == "picture_reader" else None

        vocab_list = spec.get("vocab", []) or []
        missing_zh = [v.get("en", "") for v in vocab_list if not (v.get("zh") or "").strip()]
        if missing_zh:
            print("[WARN] Missing Chinese for vocab:", ", ".join(missing_zh), flush=True)
        if page_kind != "picture_reader" and len(vocab_list) == 0 and lesson_mode != "reading_listening":
            raise RuntimeError(
                "Parser produced 0 vocab items. Check output/<slug>/spec_debug.json.\n"
                "Your homework.txt MUST include:\n"
                "#Vocabulary（词汇）：table, chair, ..."
            )

        if page_kind == "picture_reader":
            if source_image_dir:
                src = Path(args.input_image)
                if src.exists():
                    shutil.copy2(src, source_image_dir / src.name)
            if ocr_debug:
                try:
                    (lesson_root / "ocr_debug.json").write_text(json.dumps(ocr_debug, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass
            if dry_run:
                _log_step("[DRY RUN] Skipping audio generation.")
                audio_files = []
            else:
                _log_step("[STAGE] audio generation starting")
                t_audio = perf_counter()
                audio_files = generate_audio(spec, audio_dir)
                _log_step(f"[TIME] audio={perf_counter() - t_audio:.2f}s")
            _ = audio_files
        else:
            if dry_run:
                _log_step("[DRY RUN] Skipping image + audio generation.")
                card_files = []
                audio_files = []
            else:
                _log_step("[STAGE] image card generation starting")
                t_cards = perf_counter()
                card_files = generate_vocab_cards(spec, font_path, cards_dir)
                _log_step(f"[TIME] cards={perf_counter() - t_cards:.2f}s")

                t_audio = perf_counter()
                audio_files = generate_audio(spec, audio_dir)
                _log_step(f"[TIME] audio={perf_counter() - t_audio:.2f}s")

                if lesson_mode == "reading_listening":
                    t_long_audio = perf_counter()
                    spec = generate_long_audio_variants(spec, lesson_root)
                    _log_step(f"[TIME] long_audio={perf_counter() - t_long_audio:.2f}s")
                    try:
                        (lesson_root / "spec_debug.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
                    except Exception:
                        pass

            try:
                special_audio_items_local = _copy_special_audio_assets_from_env(lesson_root)
                if lesson_theme == "ng":
                    special_audio_items_local.extend(_copy_ng_happy_practice_assets_from_env(lesson_root))
            except Exception as exc:
                raise RuntimeError(f"Failed to route special lesson audio: {exc}")

            _ = (card_files, audio_files)

            _log_step("[STAGE] quiz generation starting")
            t_quiz = perf_counter()
            quiz_json_path = generate_quiz(spec, lesson_root, n_questions=8, theme_variant=lesson_theme)
            _log_step(f"[TIME] quiz={perf_counter() - t_quiz:.2f}s")

            template_html_path = Path("templates/quiz_index.html")
            if template_html_path.exists():
                template_html = template_html_path.read_text(encoding="utf-8", errors="ignore")
            else:
                template_html = """<!doctype html><html><head><meta charset="utf-8"><title>Quiz</title></head>
<body><div id="app">Quiz template missing.</div></body></html>"""
            (lesson_root / "index.html").write_text(template_html, encoding="utf-8")

            if quiz_json_path.name != "quiz.json":
                shutil.copy2(quiz_json_path, lesson_root / "quiz.json")

    if not special_audio_items_local:
        for folder_name, title_default in (("manual", (os.environ.get("SKYED_SPECIAL_AUDIO_TITLE", "Extra Audio") or "Extra Audio").strip() or "Extra Audio"), ("ng_happy_practice", "Happy Practice")):
            manual_dir = lesson_root / "audio" / folder_name
            if not manual_dir.exists():
                continue
            for f in sorted(manual_dir.iterdir(), key=lambda p: p.name.lower()):
                if not f.is_file() or f.suffix.lower() not in AUDIO_FILE_EXTENSIONS:
                    continue
                special_audio_items_local.append({
                    "label": f.stem.replace("_", " ").replace("-", " ").strip() or title_default,
                    "title": title_default,
                    "url": str(f.relative_to(lesson_root)).replace('\\', '/'),
                })

    ng_tag_game_root = None
    if page_kind != "picture_reader" and lesson_theme == "ng" and not args.publish_only and not dry_run:
        try:
            ng_tag_game_root = _build_ng_tag_game(spec, lesson_root, title)
            if ng_tag_game_root:
                _log_step(f"[NG] tag_s created: {ng_tag_game_root}")
        except Exception as exc:
            raise RuntimeError(f"Failed to build NG tag_s package: {exc}")

    categories = infer_categories(spec, page_kind=page_kind, theme=lesson_theme, lesson_mode=lesson_mode, surface_variant=surface_variant)
    discover_tags = spec.get("tags", []) or []
    if lesson_theme == "ng" and not discover_tags:
        discover_tags = [slugify(title) or "ng"]
    tag_games = discover_tag_games(discover_tags, theme=lesson_theme)

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

        items_local.append({
            "en": en,
            "zh": zh,
            "img": img_rel,
            "audio_en": a_en_rel,
            "audio_zh": a_zh_rel,
            "pos": (v.get("pos") or ""),
        })

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

        sent_local.append({
            "en": en_txt,
            "zh": zh_txt,
            "audio_en": a_en_rel,
            "audio_zh": a_zh_rel,
        })

    t_lesson_html = perf_counter()
    if page_kind == "picture_reader":
        cover_rel = ""
        source_image_dir = lesson_root / "source_image"
        imgs = [f for f in source_image_dir.iterdir() if f.is_file()] if source_image_dir.exists() else []
        if imgs:
            cover_rel = f"source_image/{imgs[0].name}"
        lesson_html_local = build_picture_reader_html(title, sent_local, cover_image=cover_rel)
    else:
        lesson_html_local = build_lesson_html(
            title,
            items_local,
            sent_local,
            quiz_url="index.html",
            quiz_embed_mode="embed",
            quiz_note="",
            extra_audio_items=special_audio_items_local,
        )
    print(f"[TIME] lesson_html={perf_counter() - t_lesson_html:.2f}s")
    (lesson_root / "lesson.html").write_text(lesson_html_local, encoding="utf-8")

    try:
        (lesson_root / "lesson_manifest.json").write_text(json.dumps({
            "title": title,
            "page_kind": page_kind,
            "theme": lesson_theme,
            "lesson_mode": lesson_mode,
            "surface_variant": surface_variant,
            "tags": spec.get("tags", []) or [],
            "categories": categories,
            "tag_games": tag_games,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    _log_step(f"Generated: {lesson_root}")
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
    _log_step(f"[PUBLISH] GROUP={wp_group_path or '[root]'}")
    _log_step(f"[PUBLISH] SLUG_MODE={publish_slug_mode}")
    _log_step(f"[PUBLISH] SLUG={publish_slug}")
    _log_step(f"[PUBLISH] ROUTE=/{publish_rel_path}")

    # Upload media (cards + audio). NOTE: we upload the rendered cards (cards/*.png), not raw ai images.
    card_url_by_stem: Dict[str, str] = {}
    cards_dir = lesson_root / "cards"
    if cards_dir.exists():
        t_upload_cards = perf_counter()
        card_count = 0
        card_files_to_upload = sorted(cards_dir.glob("*.png"))
        total_card_files = len(card_files_to_upload)
        _log_step(f"[PUBLISH] uploading cards count={total_card_files}")
        for idx, f in enumerate(card_files_to_upload, start=1):
            _log_step(f"[PUBLISH] card {idx}/{total_card_files}: {f.name}")
            j = upload_media(wp_base, wp_user, wp_pass, f)
            url = (j or {}).get("source_url") or ""
            if url:
                card_url_by_stem[f.stem] = url
            card_count += 1
        _log_step(f"[TIME] upload_cards={perf_counter() - t_upload_cards:.2f}s count={card_count}")

    source_image_url = ""
    source_image_dir = lesson_root / "source_image"
    if source_image_dir.exists():
        imgs = [f for f in source_image_dir.iterdir() if f.is_file()]
        if imgs:
            j = upload_media(wp_base, wp_user, wp_pass, imgs[0])
            source_image_url = (j or {}).get("source_url") or ""
    # IMPORTANT: key by relative path (en/apple.mp3 vs zh/apple.mp3) to avoid collisions
    audio_url_by_rel: Dict[str, str] = {}
    audio_dir = lesson_root / "audio"
    if audio_dir.exists():
        t_upload_audio = perf_counter()
        audio_count = 0
        audio_files_to_upload = [f for f in sorted(audio_dir.rglob("*")) if f.is_file() and f.suffix.lower() in AUDIO_FILE_EXTENSIONS]
        total_audio_files = len(audio_files_to_upload)
        _log_step(f"[PUBLISH] uploading audio count={total_audio_files}")
        for idx, f in enumerate(audio_files_to_upload, start=1):
            _log_step(f"[PUBLISH] audio {idx}/{total_audio_files}: {f.name}")
            j = upload_media(wp_base, wp_user, wp_pass, f)
            url = (j or {}).get("source_url") or ""
            if url:
                audio_url_by_rel[_audio_rel_key(audio_dir, f)] = url
            audio_count += 1
        _log_step(f"[TIME] upload_audio={perf_counter() - t_upload_audio:.2f}s count={audio_count}")

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
    special_audio_items_remote = _map_special_audio_items(special_audio_items_local, audio_url_by_rel)

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
        if page_kind == "picture_reader":
            html_out = build_picture_reader_html(title, sent_remote, cover_image=source_image_url)
        else:
            html_out = build_lesson_html(
                title,
                items_remote,
                sent_remote,
                quiz_url,
                quiz_embed_mode=quiz_embed_mode,
                quiz_note=quiz_note,
                extra_audio_items=special_audio_items_remote,
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
        _log_step(f"[TIME] create_post={perf_counter() - t_create_post:.2f}s")
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

        quiz_dict = _rewrite_practice_media_urls(quiz_dict, card_url_by_stem, audio_url_by_rel)

        payload = {
            "title": title,
            "slug": publish_slug,
            "page_kind": page_kind,
            "tags": spec.get("tags", []) or [],
            "categories": categories,
            "tag_games": tag_games,
            "vocab": items_remote,
            "sentences": sent_remote,
            "reading_block": reading_remote,
            "listening_block": listening_remote,
            "extra_audio": special_audio_items_remote,
            "qa": (spec.get("qa", []) or []) if lesson_theme == "sky" else [],
            "comprehension_questions": spec.get("comprehension_questions", []) or [],
            "quiz": quiz_dict,
            "practice": quiz_dict,
            "renderer_theme": lesson_theme,
            "consistency": consistency_report,
            "picture_reader": {
                "cover_image": source_image_url,
                "cover_image_local": (spec.get("picture_reader") or {}).get("source_image_local", ""),
                "line_count": len(sent_remote),
            } if page_kind == "picture_reader" else {},
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

        t_payload_upload = perf_counter()
        payload_media = upload_media(wp_base, wp_user, wp_pass, payload_path)
        _log_step(f"[TIME] upload_payload={perf_counter() - t_payload_upload:.2f}s")
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
        _log_step(f"[TIME] create_post={perf_counter() - t_create_post:.2f}s")
    _log_step(f"Publish slug: {publish_slug}")
    _log_step("Published post:")
    if isinstance(post, dict):
        print(post.get("link") or post)
    else:
        print(post)


if __name__ == "__main__":
    main()
