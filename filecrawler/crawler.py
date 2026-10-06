"""List every folder and file in a submission (a folder on disk or a .zip)."""
from __future__ import annotations

import os
import threading
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable, List, Optional, Set

from .matrix import leading_code
from .models import FoundFile
from .settings import Settings

ProgressCallback = Callable[[str], None]


class CrawlError(Exception):
    pass


class CrawlCancelled(Exception):
    pass


@dataclass
class Listing:
    root: str
    is_zip: bool
    dirs: List[PurePosixPath] = field(default_factory=list)   # relative to root
    files: List[FoundFile] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


def list_source(
    source: "str | Path",
    settings: Settings,
    cancel: Optional[threading.Event] = None,
    progress: Optional[ProgressCallback] = None,
) -> Listing:
    path = Path(source)
    if path.is_dir():
        return _list_folder(path, settings, cancel, progress)
    if path.is_file() and zipfile.is_zipfile(path):
        return _list_zip(path, settings)
    if not path.exists():
        raise CrawlError(f"Not found: {path}")
    raise CrawlError(f"{path.name} is not a folder or a .zip file.")


def _os_path(path: Path) -> str:
    """On Windows, use the \\\\?\\ prefix so paths over 260 characters still work."""
    text = os.path.abspath(str(path))
    if os.name != "nt" or text.startswith("\\\\?\\"):
        return text
    if text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + text[2:]
    return "\\\\?\\" + text


def _list_folder(
    root: Path,
    settings: Settings,
    cancel: Optional[threading.Event],
    progress: Optional[ProgressCallback],
) -> Listing:
    listing = Listing(root=str(root), is_zip=False)
    stack = [(_os_path(root), PurePosixPath())]
    scanned = 0
    while stack:
        if cancel is not None and cancel.is_set():
            raise CrawlCancelled()
        os_dir, rel_dir = stack.pop()
        scanned += 1
        try:
            with os.scandir(os_dir) as iterator:
                entries = list(iterator)
        except OSError as exc:
            listing.errors.append(f"Couldn't read '{rel_dir}': {exc.strerror or exc}")
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if settings.is_ignored_folder(entry.name):
                        continue
                    child = rel_dir / entry.name
                    listing.dirs.append(child)
                    stack.append((entry.path, child))
                elif entry.is_file():
                    if settings.is_ignored_file(entry.name):
                        continue
                    size = entry.stat().st_size
                    listing.files.append(FoundFile(str(rel_dir / entry.name), size))
            except OSError as exc:
                listing.errors.append(f"Couldn't read '{rel_dir / entry.name}': {exc.strerror or exc}")
        if progress is not None and scanned % 50 == 0:
            progress(f"Scanning... {len(listing.dirs)} folders, {len(listing.files)} files")
    return listing


def _list_zip(path: Path, settings: Settings) -> Listing:
    listing = Listing(root=str(path), is_zip=True)
    dirs: Set[PurePosixPath] = set()
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
    except (zipfile.BadZipFile, OSError) as exc:
        raise CrawlError(f"Couldn't read {path.name}: {exc}") from exc

    for info in infos:
        parts = [p for p in info.filename.replace("\\", "/").split("/") if p not in ("", ".", "..")]
        if not parts:
            continue
        folder_parts = parts if info.is_dir() else parts[:-1]
        if any(settings.is_ignored_folder(p) for p in folder_parts):
            continue
        for depth in range(1, len(folder_parts) + 1):
            dirs.add(PurePosixPath(*folder_parts[:depth]))
        if not info.is_dir() and not settings.is_ignored_file(parts[-1]):
            listing.files.append(FoundFile("/".join(parts), info.file_size))
    # Zips usually wrap everything in one top-level folder; drop it so paths read
    # the same as when the unzipped folder itself is chosen.
    tops = {d.parts[0] for d in dirs} | {PurePosixPath(f.rel_path).parts[0] for f in listing.files}
    if (len(tops) == 1 and leading_code(next(iter(tops))) is None
            and listing.files and all("/" in f.rel_path for f in listing.files)):
        top = PurePosixPath(tops.pop())
        listing.root = f"{path}/{top}"
        dirs = {d.relative_to(top) for d in dirs if d != top}
        listing.files = [FoundFile(str(PurePosixPath(f.rel_path).relative_to(top)), f.size)
                         for f in listing.files]
    listing.dirs = sorted(dirs)
    return listing
