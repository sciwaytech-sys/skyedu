from __future__ import annotations
import argparse
import os
import shutil
from pathlib import Path
from dotenv import load_dotenv

from skyed.parser import parse_homework_text
from skyed.utils import slugify, ensure_dir
from skyed.cards import generate_vocab_cards
from skyed.tts import generate_audio
from skyed.quizgen import generate_quiz
from skyed.wp import upload_media, create_post

def build_lesson_html(title: str, card_urls, audio_urls, quiz_iframe_url: str) -> str:
    cards_html = "".join([f'<img src="{u}" style="max-width:100%;border-radius:14px;margin:10px 0;" />' for u in card_urls])
    audio_html = "".join([f'<p><audio controls src="{u}" style="width:100%"></audio></p>' for u in audio_urls])

    html = f"""
    <h2>{title}</h2>

    <h3>Vocabulary Cards</h3>
    {cards_html}

    <h3>Audio Practice</h3>
    {audio_html}

    <h3>Quiz</h3>
    <iframe src="{quiz_iframe_url}" width="100%" height="900" style="border:0;border-radius:14px;" loading="lazy"></iframe>
    """
    return html

def main():
    load_dotenv()

    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Path to homework text file")
    ap.add_argument("--lesson_title", default=None)
    ap.add_argument("--publish", action="store_true", help="If set, upload to WordPress and create post")
    args = ap.parse_args()

    wp_base = os.getenv("WP_BASE_URL", "").strip()
    wp_user = os.getenv("WP_USER", "").strip()
    wp_pass = os.getenv("WP_APP_PASSWORD", "").strip()
    wp_post_type = os.getenv("WP_POST_TYPE", "post").strip()

    output_dir = Path(os.getenv("OUTPUT_DIR", "output"))
    font_path = os.getenv("FONT_PATH", "").strip() or None
    quiz_public_base = os.getenv("QUIZ_PUBLIC_BASE", "").rstrip("/")

    hw_text = Path(args.input).read_text(encoding="utf-8")
    spec = parse_homework_text(hw_text)

    title = args.lesson_title or spec.get("title", "Homework")
    slug = slugify(title)

    lesson_root = ensure_dir(output_dir / slug)

    # Clean previous run
    for name in ("cards", "audio"):
        p = lesson_root / name
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)

    cards_dir = ensure_dir(lesson_root / "cards")
    audio_dir = ensure_dir(lesson_root / "audio")

    # Generate cards + audio
    card_files = generate_vocab_cards(spec, font_path, cards_dir)
    audio_files = generate_audio(spec, audio_dir)

    # Generate quiz files directly into lesson_root so /quiz/<slug>/ works
    quiz_json_path = generate_quiz(spec, lesson_root, n_questions=8)
    # Copy template html as index.html
    template_html = Path("templates/quiz_index.html").read_text(encoding="utf-8")
    (lesson_root / "index.html").write_text(template_html, encoding="utf-8")
    # Ensure quiz.json is named quiz.json in lesson_root
    if quiz_json_path.name != "quiz.json":
        shutil.copy2(quiz_json_path, lesson_root / "quiz.json")

    print(f"Generated: {lesson_root}")
    print(f"Quiz local path: {(lesson_root / 'index.html')}")

    if not args.publish:
        print("Skipping WordPress publish. Use --publish to upload + create post.")
        return

    if not (wp_base and wp_user and wp_pass and quiz_public_base):
        raise RuntimeError("Missing WP_BASE_URL / WP_USER / WP_APP_PASSWORD / QUIZ_PUBLIC_BASE in .env")

    # Upload media
    card_urls = []
    for f in card_files:
        j = upload_media(wp_base, wp_user, wp_pass, f)
        card_urls.append(j.get("source_url"))

    audio_urls = []
    for f in audio_files:
        j = upload_media(wp_base, wp_user, wp_pass, f)
        audio_urls.append(j.get("source_url"))

    quiz_url = f"{quiz_public_base}/{slug}/"
    html = build_lesson_html(title, card_urls, audio_urls, quiz_url)

    post = create_post(wp_base, wp_user, wp_pass, title=title, html=html, post_type=wp_post_type, status="publish")
    print("Published post:")
    print(post.get("link"))

if __name__ == "__main__":
    main()
