from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class AtlasPaths:
    # Obsidian vault content
    scratchpad: Path
    scratchpad_archive: Path
    daily_notes_dir: Path

    # Tool-owned data (inside the python project)
    tool_data_dir: Path
    backups_dir: Path
    logs_dir: Path


def get_paths(project_root: Path | None = None) -> AtlasPaths:
    """
    Centralized, single-source-of-truth paths for your environment.
    Later, this function is where we'll load config.yaml for other users.
    """
    if project_root is None:
        project_root = Path(__file__).resolve().parent

    tool_data_dir = project_root / "data"
    backups_dir = tool_data_dir / "backups"
    logs_dir = tool_data_dir / "logs"

    return AtlasPaths(
        scratchpad=Path("/Users/stephenkennedy/Obsidian/Lighthouse/4-RoR/X/Scratchpad.md"),
        scratchpad_archive=Path("/Users/stephenkennedy/Obsidian/Lighthouse/4-RoR/X/Scratchpad Archive.md"),
        daily_notes_dir=Path("/Users/stephenkennedy/Obsidian/Lighthouse/4-RoR/Calendar/Notes/Daily Notes"),
        tool_data_dir=tool_data_dir,
        backups_dir=backups_dir,
        logs_dir=logs_dir,
    )