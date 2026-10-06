"""Core data types shared by the matrix loader, crawler, checker and UI."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import PurePosixPath
from typing import Dict, List, Optional, Tuple

# A document code such as "2.11" is stored as (2, 11) so that 2.10 sorts after
# 2.9 and "02.01" matches "2.1".
CodeKey = Tuple[int, ...]


class CheckMode(Enum):
    PRESENCE = "presence"
    AI_BASIC = "ai_basic"
    AI_DEEP = "ai_deep"

    @property
    def label(self) -> str:
        return _MODE_LABELS[self]

    @property
    def description(self) -> str:
        return _MODE_DESCRIPTIONS[self]

    @property
    def uses_ai(self) -> bool:
        return self is not CheckMode.PRESENCE


_MODE_LABELS = {
    CheckMode.PRESENCE: "1. Presence check (no AI)",
    CheckMode.AI_BASIC: "2. Basic AI check",
    CheckMode.AI_DEEP: "3. In-depth AI review",
}

_MODE_DESCRIPTIONS = {
    CheckMode.PRESENCE: "Checks each required folder exists and holds a usable file.",
    CheckMode.AI_BASIC: "Presence check, then AI confirms required sections aren't left blank.",
    CheckMode.AI_DEEP: "Presence check, then AI reviews the content of each document.",
}


class Bucket(Enum):
    """The three groups the results screen is organised into (plus info)."""

    MISSING = 0
    ATTENTION = 1
    PRESENT = 2
    INFO = 3

    @property
    def label(self) -> str:
        return {
            Bucket.MISSING: "Missing",
            Bucket.ATTENTION: "Needs attention",
            Bucket.PRESENT: "Present",
            Bucket.INFO: "Not required",
        }[self]


class Status(Enum):
    MISSING = "missing"            # no folder for this code
    EMPTY = "empty"                # folder exists but holds no usable file
    MISFILED = "misfiled"          # no file in its folder, but one named with the code exists elsewhere
    WRONG_TYPE = "wrong_type"      # files present, none of an allowed type
    INCOMPLETE = "incomplete"      # AI modes: present but needs changes
    PRESENT = "present"            # present; contents not verified
    OK = "ok"                      # AI modes: present and in working order
    NOT_REQUIRED = "not_required"  # not required in the selected phase

    @property
    def label(self) -> str:
        return _STATUS_LABELS[self]

    @property
    def bucket(self) -> Bucket:
        return _STATUS_BUCKETS[self]


_STATUS_LABELS = {
    Status.MISSING: "Missing",
    Status.EMPTY: "Empty folder",
    Status.MISFILED: "Misfiled",
    Status.WRONG_TYPE: "Wrong file type",
    Status.INCOMPLETE: "Needs changes",
    Status.PRESENT: "Present",
    Status.OK: "Present - OK",
    Status.NOT_REQUIRED: "Not required",
}

_STATUS_BUCKETS = {
    Status.MISSING: Bucket.MISSING,
    Status.EMPTY: Bucket.MISSING,
    Status.MISFILED: Bucket.ATTENTION,
    Status.WRONG_TYPE: Bucket.ATTENTION,
    Status.INCOMPLETE: Bucket.ATTENTION,
    Status.PRESENT: Bucket.PRESENT,
    Status.OK: Bucket.PRESENT,
    Status.NOT_REQUIRED: Bucket.INFO,
}


@dataclass
class Requirement:
    """One document row of the requirements matrix."""

    code: str
    key: CodeKey
    title: str
    allowed_types: Tuple[str, ...]
    phases: Dict[str, bool]
    row_number: int

    @property
    def category(self) -> int:
        return self.key[0]

    def required_in(self, phase: str) -> bool:
        return self.phases.get(phase, False)


@dataclass
class Matrix:
    source: str
    phases: List[str]
    requirements: List[Requirement]
    categories: Dict[int, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def required_for(self, phase: str) -> List[Requirement]:
        return [r for r in self.requirements if r.required_in(phase)]

    def category_label(self, number: int) -> str:
        name = self.categories.get(number)
        return f"{number} - {name}" if name else f"Category {number}"


@dataclass(frozen=True)
class FoundFile:
    rel_path: str  # posix-style, relative to the submission root
    size: int

    @property
    def name(self) -> str:
        return PurePosixPath(self.rel_path).name

    @property
    def extension(self) -> str:
        return PurePosixPath(self.rel_path).suffix.lower()

    @property
    def folder(self) -> str:
        parent = str(PurePosixPath(self.rel_path).parent)
        return "" if parent == "." else parent


@dataclass
class CheckResult:
    requirement: Requirement
    required: bool
    status: Status
    allowed_types: Tuple[str, ...]
    folders: List[str] = field(default_factory=list)
    files: List[FoundFile] = field(default_factory=list)        # usable files
    other_files: List[FoundFile] = field(default_factory=list)  # wrong type
    empty_files: List[FoundFile] = field(default_factory=list)  # zero bytes
    misfiled_files: List[FoundFile] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    ai_findings: List[str] = field(default_factory=list)

    @property
    def bucket(self) -> Bucket:
        return self.status.bucket

    @property
    def file_names(self) -> str:
        shown = self.files or self.other_files or self.misfiled_files or self.empty_files
        return ", ".join(f.name for f in shown)


@dataclass
class CheckReport:
    phase: str
    mode: CheckMode
    source: str
    source_is_zip: bool
    matrix_source: str
    results: List[CheckResult]
    unrecognised_folders: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    evaluation: List[str] = field(default_factory=list)     # rule-based summary
    ai_evaluation: List[str] = field(default_factory=list)  # filled by the AI summary call
    ai_status: Optional[str] = None                         # why AI output is absent, if it is
    generated_at: datetime = field(default_factory=datetime.now)

    @property
    def required_results(self) -> List[CheckResult]:
        return [r for r in self.results if r.required]

    def count(self, bucket: Bucket) -> int:
        return sum(1 for r in self.required_results if r.bucket is bucket)
