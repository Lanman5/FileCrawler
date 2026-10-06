"""Match a submission listing against the matrix and work out each document's status."""
from __future__ import annotations

import threading
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Tuple

from .ai_client import AIClient, AIClientError, AINotConfiguredError
from .crawler import CrawlCancelled, Listing, ProgressCallback, list_source
from .matrix import leading_code
from .models import CheckMode, CheckReport, CheckResult, CodeKey, FoundFile, Matrix, Status
from .report import build_evaluation, sort_results
from .settings import Settings


def run_check(
    matrix: Matrix,
    phase: str,
    source: "str | Path",
    mode: CheckMode,
    settings: Settings,
    ai_client: Optional[AIClient] = None,
    progress: Optional[ProgressCallback] = None,
    cancel: Optional[threading.Event] = None,
) -> CheckReport:
    if phase not in matrix.phases:
        raise ValueError(f"Unknown phase '{phase}'. Matrix phases: {', '.join(matrix.phases)}")
    _say(progress, "Scanning submission...")
    listing = list_source(source, settings, cancel=cancel, progress=progress)
    _say(progress, f"Matching {len(listing.files)} files against the matrix...")
    results, unrecognised = evaluate(matrix, phase, listing, settings)

    warnings = list(matrix.warnings)
    if listing.errors:
        warnings.extend(listing.errors[:20])
        if len(listing.errors) > 20:
            warnings.append(f"...and {len(listing.errors) - 20} more unreadable items.")

    report = CheckReport(
        phase=phase,
        mode=mode,
        source=str(source),
        source_is_zip=listing.is_zip,
        matrix_source=matrix.source,
        results=sort_results(results),
        unrecognised_folders=unrecognised,
        warnings=warnings,
    )
    if mode.uses_ai:
        _run_ai(report, listing, ai_client or AIClient(), progress, cancel)
        report.results = sort_results(report.results)
    report.evaluation = build_evaluation(report, matrix)
    _say(progress, "Done.")
    return report


def evaluate(
    matrix: Matrix, phase: str, listing: Listing, settings: Settings
) -> Tuple[List[CheckResult], List[str]]:
    """Return a result for every matrix row, plus code-like folders the matrix doesn't know."""
    known = {r.key for r in matrix.requirements}

    # Folders whose name starts with a known code, e.g. "2.10 Design Risk Register".
    code_dirs: Dict[CodeKey, List[PurePosixPath]] = defaultdict(list)
    dir_code: Dict[PurePosixPath, CodeKey] = {}
    for folder in listing.dirs:
        key = leading_code(folder.name)
        if key in known:
            code_dirs[key].append(folder)
            dir_code[folder] = key

    # Each file belongs to its nearest code-folder ancestor, so sub-folders such as
    # "2.2 Drawings/Rev B/" count towards 2.2.
    assigned: Dict[CodeKey, List[FoundFile]] = defaultdict(list)
    named: Dict[CodeKey, List[FoundFile]] = defaultdict(list)
    for found in listing.files:
        path = PurePosixPath(found.rel_path)
        owner = next((dir_code[p] for p in path.parents if p in dir_code), None)
        if owner is not None:
            assigned[owner].append(found)
        name_key = leading_code(found.name)
        if name_key in known:
            named[name_key].append(found)

    results = [
        _evaluate_one(req, phase, settings, code_dirs.get(req.key, []), assigned.get(req.key, []),
                      named.get(req.key, []))
        for req in matrix.requirements
    ]

    unrecognised = sorted(
        str(folder) for folder in listing.dirs
        if folder not in dir_code
        and len(leading_code(folder.name) or ()) >= 2
        and not any(p in dir_code for p in folder.parents)
    )
    return results, unrecognised


def _evaluate_one(
    requirement,
    phase: str,
    settings: Settings,
    folders: List[PurePosixPath],
    files: List[FoundFile],
    named_files: List[FoundFile],
) -> CheckResult:
    allowed = settings.effective_types(requirement)
    files = sorted(files, key=lambda f: f.rel_path)
    empty = [f for f in files if settings.zero_byte_is_empty and f.size == 0]
    non_empty = [f for f in files if f not in empty]
    good = [f for f in non_empty if not allowed or f.extension in allowed]
    wrong = [f for f in non_empty if allowed and f.extension not in allowed]
    in_folder = {f.rel_path for f in files}
    misfiled = [f for f in named_files
                if f.rel_path not in in_folder and not (settings.zero_byte_is_empty and f.size == 0)]

    notes: List[str] = []
    if good:
        status = Status.PRESENT
        if wrong:
            notes.append(f"Also holds {len(wrong)} file(s) of another type: {_names(wrong)}")
    elif wrong:
        status = Status.WRONG_TYPE
        found_types = sorted({f.extension or "(no extension)" for f in wrong})
        notes.append(f"Expected {', '.join(allowed)}; found {', '.join(found_types)}")
    elif misfiled:
        status = Status.MISFILED
        where = "Folder is empty" if folders else f"No '{requirement.code}' folder"
        notes.append(f"{where} - matching file found at {misfiled[0].rel_path}"
                     + (f" (+{len(misfiled) - 1} more)" if len(misfiled) > 1 else ""))
    elif folders:
        status = Status.EMPTY
        notes.append(f"Only zero-byte file(s): {_names(empty)}" if empty else "Folder has no files")
    else:
        status = Status.MISSING
        notes.append(f"No folder starting with '{requirement.code}'")

    if good and empty:
        notes.append(f"Ignored {len(empty)} zero-byte file(s): {_names(empty)}")
    if len(folders) > 1:
        notes.append(f"Code used by {len(folders)} folders: " + "; ".join(str(f) for f in sorted(folders)))

    required = requirement.required_in(phase)
    if not required:
        provided = bool(good or wrong or misfiled)
        notes = ["Provided, though not required this phase" if provided else "Not provided"]
        status = Status.NOT_REQUIRED

    return CheckResult(
        requirement=requirement,
        required=required,
        status=status,
        allowed_types=allowed,
        folders=[str(f) for f in sorted(folders)],
        files=good,
        other_files=wrong,
        empty_files=empty,
        misfiled_files=misfiled,
        notes=notes,
    )


def _names(files: List[FoundFile], limit: int = 3) -> str:
    names = [f.name for f in files[:limit]]
    if len(files) > limit:
        names.append(f"+{len(files) - limit} more")
    return ", ".join(names)


def _run_ai(
    report: CheckReport,
    listing: Listing,
    client: AIClient,
    progress: Optional[ProgressCallback],
    cancel: Optional[threading.Event],
) -> None:
    if not client.configured:
        report.ai_status = (
            "AI checks skipped: the AI integration isn't configured yet "
            "(see filecrawler/ai_client.py). Results below are presence-only."
        )
        return
    if listing.is_zip:
        # TODO: extract to a temp folder if AI checks on zip submissions are needed.
        report.ai_status = "AI checks need an unzipped folder; this zip was presence-checked only."
        return

    candidates = [r for r in report.results if r.required and r.status is Status.PRESENT]
    for index, result in enumerate(candidates, start=1):
        if cancel is not None and cancel.is_set():
            raise CrawlCancelled()
        _say(progress, f"AI checking {result.requirement.code} ({index}/{len(candidates)})...")
        verdicts = []
        for found in result.files:
            path = Path(listing.root) / found.rel_path
            try:
                if report.mode is CheckMode.AI_BASIC:
                    verdict = client.check_completeness(path, result.requirement)
                else:
                    verdict = client.review_content(path, result.requirement, report.phase)
            except AIClientError as exc:
                result.notes.append(f"AI check failed for {found.name}: {exc}")
                continue
            verdicts.append(verdict)
            prefix = f"{found.name}: " if len(result.files) > 1 else ""
            result.ai_findings.extend(prefix + finding for finding in verdict.findings)
        if verdicts:
            result.status = Status.OK if all(v.complete for v in verdicts) else Status.INCOMPLETE

    _say(progress, "Asking AI for an overall evaluation...")
    try:
        report.ai_evaluation = client.summarize(report)
    except AINotConfiguredError:
        report.ai_status = "AI summary not available yet."
    except AIClientError as exc:
        report.ai_status = f"AI summary failed: {exc}"


def _say(progress: Optional[ProgressCallback], message: str) -> None:
    if progress is not None:
        progress(message)
