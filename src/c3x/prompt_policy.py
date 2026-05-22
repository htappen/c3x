from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def caveman_mode_text() -> str:
    path = Path(__file__).resolve().parent / "prompts" / "caveman_mode.md"
    return path.read_text(encoding="utf-8").strip()

