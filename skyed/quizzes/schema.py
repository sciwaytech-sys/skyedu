from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Literal

QuizType = Literal["prepositions_dragdrop", "mcq_text", "qa_dialog"]

@dataclass
class LessonQuizPackage:
    lesson_id: str
    title: str
    skin: str
    scene: str
    quizzes: list[dict[str, Any]]  # quiz items (already validated by builder)

def assert_keys(obj: dict, required: list[str], where: str) -> None:
    missing = [k for k in required if k not in obj]
    if missing:
        raise ValueError(f"Missing keys {missing} in {where}")

def validate_package(pkg: dict[str, Any]) -> None:
    assert_keys(pkg, ["lesson_id", "title", "skin", "scene", "quizzes"], "package root")
    if not isinstance(pkg["quizzes"], list) or not pkg["quizzes"]:
        raise ValueError("package.quizzes must be a non-empty list")
    for i, q in enumerate(pkg["quizzes"]):
        assert_keys(q, ["id", "type", "prompt"], f"quiz[{i}]")
        if q["type"] not in ("prepositions_dragdrop", "mcq_text", "qa_dialog"):
            raise ValueError(f"Unknown quiz type: {q['type']}")
