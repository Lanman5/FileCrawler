"""User settings, persisted as JSON between sessions."""
from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .matrix import parse_code, parse_types
from .models import CodeKey, Requirement

# Files that never count as a real deliverable (OS thumbnails, Office lock files, ...).
DEFAULT_IGNORE_FILES = ["Thumbs.db", "desktop.ini", ".DS_Store", "~$*", "*.tmp", ".~lock.*#", "._*"]
DEFAULT_IGNORE_FOLDERS = ["__MACOSX", ".git"]


def default_settings_path() -> Path:
    override = os.environ.get("FILECRAWLER_SETTINGS")
    if override:
        return Path(override)
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "FileCrawler" / "settings.json"
    return Path.home() / ".config" / "filecrawler" / "settings.json"


@dataclass
class Settings:
    # Code prefix -> allowed extensions, e.g. {"2": [".pdf"], "2.2": [".pdf", ".dwg"]}.
    # Used only where the matrix's Allowed Types cell is blank; the longest prefix wins.
    type_rules: Dict[str, List[str]] = field(default_factory=dict)
    ignore_file_patterns: List[str] = field(default_factory=lambda: list(DEFAULT_IGNORE_FILES))
    ignore_folder_names: List[str] = field(default_factory=lambda: list(DEFAULT_IGNORE_FOLDERS))
    zero_byte_is_empty: bool = True
    show_not_required: bool = False
    last_matrix: str = ""
    last_source: str = ""
    last_phase: str = ""
    last_mode: str = "presence"

    def allowed_types_for(self, key: CodeKey) -> Tuple[str, ...]:
        best: Tuple[str, ...] = ()
        best_len = -1
        for prefix, extensions in self.type_rules.items():
            prefix_key = parse_code(prefix)
            if prefix_key and key[: len(prefix_key)] == prefix_key and len(prefix_key) > best_len:
                best, best_len = tuple(extensions), len(prefix_key)
        return best

    def effective_types(self, requirement: Requirement) -> Tuple[str, ...]:
        """The matrix wins; settings fill in where the matrix says nothing."""
        return requirement.allowed_types or self.allowed_types_for(requirement.key)

    def is_ignored_file(self, name: str) -> bool:
        lowered = name.lower()
        return any(fnmatch.fnmatchcase(lowered, p.lower()) for p in self.ignore_file_patterns)

    def is_ignored_folder(self, name: str) -> bool:
        lowered = name.lower()
        return any(fnmatch.fnmatchcase(lowered, p.lower()) for p in self.ignore_folder_names)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Settings":
        path = path or default_settings_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        known = {f.name for f in fields(cls)}
        settings = cls()
        for name, value in data.items():
            if name in known and isinstance(value, type(getattr(settings, name))):
                setattr(settings, name, value)
        return settings

    def save(self, path: Optional[Path] = None) -> None:
        path = path or default_settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


def format_type_rules(rules: Dict[str, List[str]]) -> str:
    ordered = sorted(rules.items(), key=lambda item: parse_code(item[0]) or ())
    return "\n".join(f"{prefix} = {', '.join(exts)}" for prefix, exts in ordered)


def parse_type_rules(text: str) -> Tuple[Dict[str, List[str]], List[str]]:
    """Parse lines like '2.2 = .pdf, .dwg'. Returns (rules, error messages)."""
    rules: Dict[str, List[str]] = {}
    errors: List[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        prefix, sep, types = line.partition("=")
        if not sep:
            prefix, sep, types = line.partition(":")
        prefix = prefix.strip()
        if not sep or parse_code(prefix) is None:
            errors.append(f"Line {number}: expected 'code = .ext, .ext', e.g. '2.2 = .pdf, .dwg'")
            continue
        extensions = list(parse_types(types))
        if not extensions:
            errors.append(f"Line {number}: no file types given for {prefix}")
            continue
        rules[prefix] = extensions
    return rules, errors
