from __future__ import annotations
import json
import os
import random
import shutil
from pathlib import Path

from skyed.quizzes.schema import validate_package
from skyed.quizzes.builders.prepositions_dragdrop import build_items as build_dd, PREP_SET_C
from skyed.quizzes.builders.mcq_text import build_items as build_mcq
from skyed.quizzes.builders.qa_dialog import build_items as build_qa

ROOT = Path(__file__).resolve().parents[1]  # skyed/

def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")

def write_text(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")

def copytree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)

def build_package(lesson_id: str, title: str, skin: str, scene: str, seed: int = 2026) -> dict:
    rng = random.Random(seed)

    scene_zones_path = ROOT / "assets" / "scenes" / scene / "zones.json"
    zones = json.loads(read_text(scene_zones_path))

    dd = build_dd(rng=rng, scene=scene, zones=zones, n=12, prep_set=PREP_SET_C)

    objects = zones["objects"]
    anchors = ["table", "chair", "box", "bag", "board"]

    mcq = build_mcq(rng=rng, taught_preps=PREP_SET_C, objects=objects, anchors=anchors, n=10)
    qa = build_qa(rng=rng, taught_preps=PREP_SET_C, objects=objects, anchors=anchors, n=8)

    pkg = {
        "lesson_id": lesson_id,
        "title": title,
        "skin": skin,
        "scene": scene,
        "quizzes": dd + mcq + qa
    }
    validate_package(pkg)
    return pkg

def bundle_output(pkg: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    # write quiz.json
    write_text(out_dir / "quiz.json", json.dumps(pkg, ensure_ascii=False, indent=2))

    # index.html from base template
    base = read_text(ROOT / "quizzes" / "render" / "base.html")
    base = base.replace("{{TITLE}}", pkg["title"])
    write_text(out_dir / "index.html", base)

    # runtime.js
    shutil.copy2(ROOT / "quizzes" / "render" / "runtime.js", out_dir / "runtime.js")

    # templates folder
    tpl_dir = out_dir / "templates"
    tpl_dir.mkdir(exist_ok=True)
    shutil.copy2(ROOT / "quizzes" / "render" / "dragdrop.html", tpl_dir / "dragdrop.html")
    shutil.copy2(ROOT / "quizzes" / "render" / "mcq.html", tpl_dir / "mcq.html")
    shutil.copy2(ROOT / "quizzes" / "render" / "qa.html", tpl_dir / "qa.html")

    # skin.css: concatenate fluent + brand
    fluent = read_text(ROOT / "quizzes" / "skins" / "fluent_win11.css")
    brand = read_text(ROOT / "quizzes" / "skins" / "sky_brand.css")
    write_text(out_dir / "skin.css", fluent + "\n\n" + brand)

    # copy assets scene pack to output
    src_scene = ROOT / "assets" / "scenes" / pkg["scene"]
    dst_scene = out_dir / "assets" / "scenes" / pkg["scene"]
    dst_scene.parent.mkdir(parents=True, exist_ok=True)
    copytree(src_scene, dst_scene)

def main() -> None:
    lesson_id = "demo_prepositions_setC"
    title = "Set C — Prepositions Game Pack"
    skin = "fluent_win11+sky_brand"
    scene = "classroom"

    pkg = build_package(lesson_id, title, skin, scene, seed=20260207)

    out_dir = Path("output") / "lessons" / lesson_id / "quiz"
    bundle_output(pkg, out_dir)

    print(f"OK: built {len(pkg['quizzes'])} quizzes at {out_dir / 'index.html'}")

if __name__ == "__main__":
    main()
