"""
Shared dataset-path helpers for train_yolo.py and evaluate_yolo.py.

Kept in one place because Ultralytics' own path-resolution behavior (see
dataset.yaml's `path:` comment) is subtle enough that getting the
images<->labels substitution wrong once already caused a real bug during
this project's own testing - not worth risking two divergent copies that
could silently drift apart.
"""

from __future__ import annotations

from pathlib import Path

import yaml

# Files that exist in an "empty" images/labels directory on purpose and must
# never count as real dataset content: .gitkeep keeps the folder tracked by
# git, classes.txt is LabelImg's class-index file (see training/LABELIMG_SETUP.md)
# and is present from day one, before any image has ever been labeled.
_PLACEHOLDER_NAMES = {".gitkeep", "classes.txt"}


def has_real_files(directory: Path) -> bool:
    """True if `directory` has anything besides known placeholders."""
    if not directory.is_dir():
        return False
    return any(p.name not in _PLACEHOLDER_NAMES for p in directory.iterdir())


def resolve_split_dirs(data_yaml: Path, splits: tuple[str, ...]) -> list[Path]:
    """Derive the image AND label directories a dataset.yaml actually points
    to for the given split key(s), the same way Ultralytics itself does:
    `path:` (if present) + each split's path for images, and an
    images->labels substitution for labels. A split key absent from the
    yaml (e.g. `test` before it's populated) is silently skipped rather than
    treated as an error - callers decide whether that's acceptable for their
    use case.
    """
    with data_yaml.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)

    root = data_yaml.parent
    if config.get("path"):
        root = (data_yaml.parent / config["path"]).resolve()

    dirs: list[Path] = []
    for split_key in splits:
        split_value = config.get(split_key)
        if not split_value:
            continue
        image_dir = (root / split_value).resolve()
        dirs.append(image_dir)
        # Ultralytics locates labels by replacing the LAST "images" path
        # segment with "labels" - mirrored here rather than assuming a
        # fixed images/<split> <-> labels/<split> layout.
        parts = list(image_dir.parts)
        for i in range(len(parts) - 1, -1, -1):
            if parts[i] == "images":
                parts[i] = "labels"
                dirs.append(Path(*parts))
                break
    return dirs
