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
from skyed.tag_registry import safe_tag, hydrate_tag_game_entries
from skyed.picture_reader import parse_picture_to_reader_spec
from skyed.utils import slugify, ensure_dir
from skyed.cards import generate_vocab_cards, slugify as card_slugify
from skyed.tts_edge import generate_audio, generate_long_audio_variants, generate_word_audio_set
from skyed.quizgen import generate_quiz, normalize_theme_variant
from skyed.wp import upload_media, create_post, ensure_page_path, next_sequential_slug, assert_slug_available
from skyed.tag_gamegen import export_tag_s_touch_listen_cards
from skyed.pipeline_helpers import (
    log_step as _log_step,
    sentence_audio_stem as _sentence_audio_stem,
    tag_s_output_root as _tag_s_output_root,
    h as _h,
    hu as _hu,
    build_lesson_html,
    build_picture_reader_html,
    audio_rel_key as _audio_rel_key,
    AUDIO_FILE_EXTENSIONS,
    resolve_uploaded_audio_url as _resolve_uploaded_audio_url,
    map_special_audio_items as _map_special_audio_items,
    copy_special_audio_assets_from_env as _copy_special_audio_assets_from_env,
    copy_ng_happy_practice_assets_from_env as _copy_ng_happy_practice_assets_from_env,
    load_ng_selected_tag_games_from_env as _load_ng_selected_tag_games_from_env,
    merge_tag_game_lists as _merge_tag_game_lists,
    tag_game_info_from_root as _tag_game_info_from_root,
    extract_day_number as _extract_day_number,
    build_publish_slug as _build_publish_slug,
    normalize_publish_slug_mode as _normalize_publish_slug_mode,
    clean_publish_group_path as _clean_publish_group_path,
    sanitize_publish_slug as _sanitize_publish_slug,
    infer_mode_surface_from_theme,
    build_consistency_report as _build_consistency_report,
    rewrite_practice_media_urls as _rewrite_practice_media_urls,
    rewrite_tag_game_media_urls as _rewrite_tag_game_media_urls,
    deploy_tag_games_for_publish as _deploy_tag_games_for_publish,
)




def _build_ng_tag_game(spec: Dict, lesson_root: Path, title: str) -> Optional[Path]:
    """Build the automatic NG touch-listen tag_s package.

    Important compatibility rule:
    - use the same tag normalization as tag_registry/tag_gamegen
    - keep the automatic NG package keyed by tag, not by lesson slug

    This avoids path mismatches such as my_body vs mybody and keeps NG output
    aligned with the reusable tag_s structure the rest of the project expects.
    """
    vocab = spec.get("vocab", []) or []
    if not vocab:
        return None

    lesson_tag = safe_tag(title) or "ng"
    raw_tags = spec.get("tags", []) or []
    primary_tag = ""
    for raw in raw_tags:
        candidate = safe_tag(str(raw or ""))
        if candidate:
            primary_tag = candidate
            break
    tag = primary_tag or lesson_tag
    game_id = f"{tag}_v1"

    exported_vocab: List[Dict[str, str]] = []
    for entry in vocab:
        en = str(entry.get("en") or "").strip()
        zh = str(entry.get("zh") or "").strip()
        if not en:
            continue
        stem = card_slugify(en)
        img_rel = f"cards/{stem}.png"
        audio_rel = f"audio/en/{stem}.mp3"
        if not (lesson_root / img_rel).exists():
            img_rel = ""
        if not (lesson_root / audio_rel).exists():
            audio_rel = ""
        exported_vocab.append({
            "en": en,
            "zh": zh,
            "pos": str(entry.get("pos") or "").strip(),
            "img": img_rel,
            "audio_en": audio_rel,
        })

    if not exported_vocab:
        return None

    # Keep NG auto tag_s isolated inside the lesson output instead of writing
    # into the shared Card Cutter tag_s library.
    out_root = ensure_dir(lesson_root / "_inline_tag_s")
    return export_tag_s_touch_listen_cards(
        tag=tag,
        vocab=exported_vocab,
        out_dir=out_root,
        lesson_assets_root=lesson_root,
        game_id=game_id,
        title=f"{title} · Touch and Listen",
    )



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
    ap.add_argument("--ng-generate", action="store_true", help="Internal entrypoint for NG tab generation.")
    ap.add_argument("--ng-publish", action="store_true", help="Internal entrypoint for NG tab generate+publish.")
    args = ap.parse_args()

    ng_entrypoint = "publish_ng" if args.ng_publish else ("generate_ng" if args.ng_generate else "")
    if ng_entrypoint:
        args.theme = "ng"
        if not args.lesson_mode:
            args.lesson_mode = "standard_homework"
        if not args.surface_variant:
            args.surface_variant = "ng"
        if args.ng_publish:
            args.publish = True

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
    entrypoint_env = (os.getenv("SKYED_PIPELINE_ENTRYPOINT", "") or "").strip()
    ng_action_env = (os.getenv("SKYED_NG_ACTION", "") or "").strip()
    effective_entrypoint = ng_entrypoint or entrypoint_env
    is_ng_workflow = bool(ng_entrypoint or ng_action_env or effective_entrypoint == "ng_tab")
    if lesson_theme == "ng" and not is_ng_workflow:
        print("[NG] theme=ng ignored outside NG tab; falling back to sky")
        lesson_theme = "sky"
        if lesson_mode == "standard_homework":
            surface_variant = "classic"
    if effective_entrypoint:
        print(f"[ENTRYPOINT] {effective_entrypoint}")
    if ng_action_env:
        print(f"[NG] ACTION={ng_action_env}")

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
    if page_kind != "picture_reader" and is_ng_workflow and lesson_theme == "ng" and not args.publish_only and not dry_run:
        try:
            ng_tag_game_root = _build_ng_tag_game(spec, lesson_root, title)
            if ng_tag_game_root:
                _log_step(f"[NG] tag_s created: {ng_tag_game_root}")
        except Exception as exc:
            raise RuntimeError(f"Failed to build NG tag_s package: {exc}")

    categories = infer_categories(spec, page_kind=page_kind, theme=lesson_theme, lesson_mode=lesson_mode, surface_variant=surface_variant)
    # Shared tag_s discovery from homework tags caused unrelated Card Cutter
    # packs to leak into normal lesson publishing. Keep the standard pipeline
    # isolated. Only the NG workflow may attach tag_s, and even there only the
    # auto-generated NG pack plus explicitly selected packs are allowed.
    enable_legacy_tag_discovery = (os.getenv("SKYED_ENABLE_SHARED_TAG_DISCOVERY", "0") or "0").strip().lower() in {"1", "true", "yes", "on"}
    selected_ng_tag_games = hydrate_tag_game_entries(_load_ng_selected_tag_games_from_env(), project_root=Path(__file__).resolve().parent) if (is_ng_workflow and lesson_theme == "ng") else []
    auto_ng_tag_game = _tag_game_info_from_root(ng_tag_game_root) if (is_ng_workflow and lesson_theme == "ng") else None
    if is_ng_workflow and lesson_theme == "ng":
        tag_games = _merge_tag_game_lists([auto_ng_tag_game] if auto_ng_tag_game else [], selected_ng_tag_games)
    else:
        tag_games = []
    if enable_legacy_tag_discovery and page_kind != "picture_reader":
        from skyed.tag_registry import discover_tag_games as _discover_tag_games
        discover_tags = spec.get("tags", []) or []
        tag_games = _merge_tag_game_lists(tag_games, hydrate_tag_game_entries(_discover_tag_games(discover_tags, theme=lesson_theme), project_root=Path(__file__).resolve().parent))
    tag_games = hydrate_tag_game_entries(tag_games, project_root=Path(__file__).resolve().parent)

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
            mapped.append({**item, "url": _resolve_uploaded_audio_url(audio_url_by_rel, rel)})
        if mapped:
            out["audio_variants"] = mapped
        return out

    reading_remote = map_block_audio(spec.get("reading_block") or {})
    listening_remote = map_block_audio(spec.get("listening_block") or {})
    special_audio_items_remote = _map_special_audio_items(special_audio_items_local, audio_url_by_rel)
    tag_games = _deploy_tag_games_for_publish(tag_games, log_func=_log_step)
    tag_games = _rewrite_tag_game_media_urls(
        tag_games,
        lesson_root=lesson_root,
        upload_media_func=upload_media,
        wp_base=wp_base,
        wp_user=wp_user,
        wp_pass=wp_pass,
        card_url_by_stem=card_url_by_stem,
        audio_url_by_rel=audio_url_by_rel,
    )
    inline_ready_tag_games: List[Dict[str, str]] = []
    skipped_tag_games = 0
    for game in tag_games:
        data = game.get("data") if isinstance(game, dict) else None
        items = data.get("items") if isinstance(data, dict) else None
        if isinstance(data, dict) and isinstance(items, list) and items:
            inline_ready_tag_games.append(game)
        else:
            skipped_tag_games += 1
    if skipped_tag_games:
        _log_step(f"[tag_s] inline skip count={skipped_tag_games} (missing embedded data)")
    tag_games = inline_ready_tag_games

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

        payload_hash = hashlib.sha1(payload_path.read_bytes()).hexdigest()[:12]
        data_url_for_shortcode = f"{data_url}{'&' if '?' in data_url else '?'}v={payload_hash}"
        shortcode = f'[skyed_lesson data_url="{data_url_for_shortcode}" theme="{lesson_theme}"]'

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
