from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(slots=True)
class LessonPreset:
    lesson_type: str
    theme: str
    surface_variant: str
    workflow_profile: str
    parser_profile: str
    practice_profile: str
    audio_profile: str
    renderer_profile: str
    display_name: str


PRESETS: Dict[str, LessonPreset] = {
    "standard_classic": LessonPreset(
        lesson_type="standard_homework",
        theme="sky",
        surface_variant="classic",
        workflow_profile="standard_homework",
        parser_profile="standard_homework",
        practice_profile="standard_homework",
        audio_profile="default",
        renderer_profile="classic",
        display_name="Standard Homework · Sky Classic",
    ),
    "kid_classic": LessonPreset(
        lesson_type="kid_homework",
        theme="sky",
        surface_variant="classic",
        workflow_profile="kid_homework",
        parser_profile="standard_homework",
        practice_profile="kid_homework",
        audio_profile="default",
        renderer_profile="kid_classic",
        display_name="Kid Homework · Sky Classic",
    ),
    "kid_tiles": LessonPreset(
        lesson_type="kid_homework",
        theme="sky",
        surface_variant="tiles",
        workflow_profile="kid_homework",
        parser_profile="standard_homework",
        practice_profile="kid_homework",
        audio_profile="default",
        renderer_profile="sky_tiles",
        display_name="Kid Homework · Sky Tiles",
    ),
    "strict_dark": LessonPreset(
        lesson_type="reading_listening",
        theme="strict",
        surface_variant="strict_dark",
        workflow_profile="reading_listening",
        parser_profile="reading_listening",
        practice_profile="reading_listening",
        audio_profile="strict_dual_accent",
        renderer_profile="strict_dark",
        display_name="Reading & Listening · Strict Dark",
    ),
}


LESSON_TYPE_TO_SURFACES: Dict[str, List[str]] = {
    "Standard Homework": ["Standard Homework · Sky Classic"],
    "Kid Homework": [
        "Kid Homework · Sky Classic",
        "Kid Homework · Sky Tiles",
    ],
    "Reading & Listening": [
        "Reading & Listening · Strict Dark",
    ],
}


DISPLAY_TO_PRESET_KEY: Dict[str, str] = {
    preset.display_name: key for key, preset in PRESETS.items()
}