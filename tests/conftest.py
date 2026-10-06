import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from filecrawler.matrix import parse_rows  # noqa: E402
from filecrawler.settings import Settings  # noqa: E402

EXAMPLES = ROOT / "examples"


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Never read or write the real user's settings file."""
    monkeypatch.setenv("FILECRAWLER_SETTINGS", str(tmp_path / "settings.json"))


def make_tree(root: Path, spec: dict) -> Path:
    """spec maps 'a/b/file.pdf' -> bytes, or 'a/empty/' -> None for an empty folder."""
    for rel, content in spec.items():
        path = root / rel
        if content is None:
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    return root


def make_zip(path: Path, spec: dict, wrapper: str = "") -> Path:
    prefix = f"{wrapper}/" if wrapper else ""
    with zipfile.ZipFile(path, "w") as archive:
        for rel, content in spec.items():
            archive.writestr(prefix + rel, b"" if content is None else content)
    return path


def matrix_from(rows, header=("Code", "Title", "Allowed Types", "P1", "P2")):
    return parse_rows([list(header)] + [list(r) for r in rows], source="test")


@pytest.fixture
def settings():
    return Settings()
