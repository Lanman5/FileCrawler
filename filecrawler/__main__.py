"""Entry point.

    python -m filecrawler                       # open the window
    python -m filecrawler --matrix M.xlsx --phase "Phase 2" --source D:\\Submission [--export out.xlsx]

Exit code in command-line mode: 0 = everything present, 1 = something missing or
needing attention, 2 = couldn't run.
"""
from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from .checker import run_check
from .crawler import CrawlError
from .matrix import MatrixError, load_matrix
from .models import Bucket, CheckMode, CheckReport
from .report import export_report
from .settings import Settings


def _pick_phase(phases: List[str], wanted: str) -> str:
    for phase in phases:
        if phase.lower() == wanted.lower():
            return phase
    partial = [p for p in phases if wanted.lower() in p.lower()]
    if len(partial) == 1:
        return partial[0]
    raise SystemExit(f"Phase '{wanted}' not found. Choose from: {', '.join(phases)}")


def _print_report(report: CheckReport, show_not_required: bool) -> None:
    print(f"\nPhase: {report.phase}    Mode: {report.mode.label}")
    print(f"Submission: {report.source}\n")
    for bucket in Bucket:
        rows = [r for r in report.results if r.bucket is bucket and (r.required or show_not_required)]
        if not rows:
            continue
        print(f"== {bucket.label.upper()} ({len(rows)}) ==")
        for r in rows:
            detail = r.file_names or "; ".join(r.notes)
            print(f"  {r.requirement.code:<7} {r.requirement.title[:40]:<40} {r.status.label:<16} {detail}")
        print()
    print("Evaluation:")
    for bullet in report.evaluation:
        print(f"  - {bullet}")
    if report.ai_status:
        print(f"  - {report.ai_status}")
    for bullet in report.ai_evaluation:
        print(f"  - (AI) {bullet}")
    if report.warnings:
        print("\nWarnings:")
        for warning in report.warnings:
            print(f"  ! {warning}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="filecrawler", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--matrix", help="requirements matrix (.xlsx or .csv)")
    parser.add_argument("--phase", help="phase column to check against (name or unique part of it)")
    parser.add_argument("--source", help="submission folder or .zip; omit to open the window")
    parser.add_argument("--mode", choices=[m.value for m in CheckMode], default=CheckMode.PRESENCE.value)
    parser.add_argument("--export", help="write results to .xlsx or .csv")
    parser.add_argument("--show-not-required", action="store_true")
    args = parser.parse_args(argv)

    if not args.source:
        from .gui import launch
        launch(matrix_path=args.matrix)
        return 0
    if not args.matrix:
        parser.error("--matrix is required with --source")

    settings = Settings.load()
    try:
        matrix = load_matrix(args.matrix)
        phase = _pick_phase(matrix.phases, args.phase) if args.phase else matrix.phases[0]
        report = run_check(matrix, phase, args.source, CheckMode(args.mode), settings)
    except (MatrixError, CrawlError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    _print_report(report, args.show_not_required)
    if args.export:
        print(f"\nExported to {export_report(report, args.export)}")
    needs_action = report.count(Bucket.MISSING) + report.count(Bucket.ATTENTION)
    return 1 if needs_action else 0


if __name__ == "__main__":
    sys.exit(main())
