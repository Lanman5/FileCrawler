"""Load the requirements matrix from .xlsx/.xlsm or .csv.

Expected layout (see examples/example_matrix.xlsx):

    Code | Title                 | Allowed Types | Phase 1 | Phase 2 | ...
    1    | General               |               |         |         |   <- optional category heading
    1.1  | Contractor Info Form  | .pdf          | Y       | Y       |
    2.10 | Design Risk Register  | .xlsx; .pdf   |         | Y       |

* The header row is found automatically (anything above it, e.g. a project
  banner, is ignored). Only the Code column is mandatory.
* Every column that isn't Code/Title/Allowed Types/Category/Notes and whose
  cells are Y/N style marks is treated as a phase.
* A row whose code has no dot (e.g. "2") and no phase marks is a category
  heading; its title is used to label the category.
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .models import CodeKey, Matrix, Requirement


class MatrixError(Exception):
    """The matrix can't be used as-is; the message says how to fix it."""


_CODE_RE = re.compile(r"^\d+(?:\.\d+)*$")
_LEADING_CODE_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)")

CODE_HEADERS = {
    "code", "ref", "reference", "doc ref", "document ref", "document code", "doc code",
    "item code", "id", "no", "number", "doc no", "document no", "document number", "item no",
}
TITLE_HEADERS = {
    "title", "document", "document title", "document name", "description", "name",
    "deliverable", "item", "requirement",
}
TYPES_HEADERS = {
    "allowed types", "allowed file types", "file types", "file type", "types",
    "extensions", "allowed extensions", "format", "formats",
}
CATEGORY_HEADERS = {"category", "section", "category name"}
IGNORED_HEADERS = {"notes", "note", "comments", "comment", "remarks", "owner", "responsible"}

REQUIRED_MARKS = {"y", "yes", "x", "r", "req", "required", "m", "mandatory", "true", "1", "✓", "✔"}
# "Optional" counts as not required: never flagged missing, shown as provided if present.
NOT_REQUIRED_MARKS = {"", "n", "no", "o", "opt", "optional", "-", "–", "0", "false", "n/a", "na", "none"}

_HEADER_SEARCH_ROWS = 30


def parse_code(text: str) -> Optional[CodeKey]:
    """'2.11' -> (2, 11); returns None if the text isn't a dotted numeric code."""
    text = text.strip()
    if not _CODE_RE.match(text):
        return None
    return tuple(int(part) for part in text.split("."))


def leading_code(name: str) -> Optional[CodeKey]:
    """Code at the start of a folder/file name: '2.10 Risk Register' -> (2, 10).

    Parts are compared as integers, so '1.10' never matches code 1.1 and
    '01.01 Report' matches code 1.1.
    """
    match = _LEADING_CODE_RE.match(name)
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def format_key(key: CodeKey) -> str:
    return ".".join(str(part) for part in key)


def parse_types(value: Any) -> Tuple[str, ...]:
    """'pdf; .DOCX, *.xlsx' -> ('.pdf', '.docx', '.xlsx'). Blank or 'any' -> ()."""
    if value is None:
        return ()
    tokens = [t for t in re.split(r"[;,/|\s]+", str(value).strip().lower()) if t]
    if any(t in {"any", "all", "*", ".*", "*.*"} for t in tokens):
        return ()
    out: List[str] = []
    for token in tokens:
        token = token.lstrip("*")
        if not token.startswith("."):
            token = "." + token
        if token != "." and token not in out:
            out.append(token)
    return tuple(out)


def load_matrix(path: "str | Path") -> Matrix:
    path = Path(path)
    if not path.is_file():
        raise MatrixError(f"Matrix file not found: {path}")
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        return _load_xlsx(path)
    if suffix in (".csv", ".txt"):
        return parse_rows(_read_csv_rows(path), source=str(path))
    if suffix == ".xls":
        raise MatrixError("The old .xls format isn't supported. Open it in Excel and save as .xlsx.")
    raise MatrixError(f"Unsupported matrix file type '{suffix}'. Use .xlsx or .csv.")


def _load_xlsx(path: Path) -> Matrix:
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise MatrixError("Reading .xlsx needs openpyxl: pip install openpyxl") from exc

    try:
        workbook = openpyxl.load_workbook(path, data_only=True)
    except Exception as exc:
        raise MatrixError(f"Couldn't open {path.name}: {exc}") from exc

    try:
        sheets = list(workbook.worksheets)
        # Prefer a sheet called "Matrix", then try the rest in order.
        sheets.sort(key=lambda ws: ws.title.strip().lower() != "matrix")
        first_error: Optional[MatrixError] = None
        for sheet in sheets:
            rows = [list(row) for row in sheet.iter_rows(min_row=1, values_only=True)]
            try:
                matrix = parse_rows(rows, source=str(path))
            except MatrixError as exc:
                first_error = first_error or exc
                continue
            return matrix
        raise first_error or MatrixError(f"{path.name} has no worksheets.")
    finally:
        workbook.close()


def _read_csv_rows(path: Path) -> List[List[str]]:
    raw = path.read_bytes()
    text = ""
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    first_line = next((line for line in text.splitlines() if line.strip()), "")
    # Excel in many European locales saves CSV with ';'.
    delimiter = max([",", ";", "\t"], key=first_line.count)
    return [row for row in csv.reader(io.StringIO(text), delimiter=delimiter)]


def _norm_header(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip().lower().rstrip(":.")


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _required_mark(value: Any) -> Optional[bool]:
    """True/False for a recognised mark, None if unrecognised."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in REQUIRED_MARKS:
        return True
    if text in NOT_REQUIRED_MARKS:
        return False
    return None


def _find_header(rows: Sequence[Sequence[Any]]) -> int:
    for index, row in enumerate(rows[:_HEADER_SEARCH_ROWS]):
        if any(_norm_header(cell) in CODE_HEADERS for cell in row):
            return index
    raise MatrixError(
        f"Couldn't find a header row with a 'Code' column in the first {_HEADER_SEARCH_ROWS} rows."
    )


def parse_rows(rows: Sequence[Sequence[Any]], source: str = "") -> Matrix:
    """Build a Matrix from raw cell values (first index = row, second = column)."""
    header_index = _find_header(rows)
    header = list(rows[header_index])
    body = [list(r) + [None] * (len(header) - len(r)) for r in rows[header_index + 1:]]
    warnings: List[str] = []

    code_col = title_col = types_col = category_col = None
    phase_candidates: List[int] = []
    for col, cell in enumerate(header):
        name = _norm_header(cell)
        if not name:
            continue
        if name in CODE_HEADERS and code_col is None:
            code_col = col
        elif name in TITLE_HEADERS and title_col is None:
            title_col = col
        elif name in TYPES_HEADERS and types_col is None:
            types_col = col
        elif name in CATEGORY_HEADERS and category_col is None:
            category_col = col
        elif name in IGNORED_HEADERS:
            continue
        else:
            phase_candidates.append(col)
    assert code_col is not None  # guaranteed by _find_header

    # A phase column is one whose filled cells are (mostly) Y/N style marks.
    # This keeps columns like "Owner" or "Revision" out of the phase list.
    phases: List[Tuple[int, str]] = []
    for col in phase_candidates:
        values = [row[col] for row in body if _cell_text(row[code_col]) and _cell_text(row[col])]
        recognised = sum(1 for v in values if _required_mark(v) is not None)
        if values and recognised / len(values) < 0.5:
            warnings.append(f"Column '{_cell_text(header[col])}' ignored - its cells aren't Y/N marks. "
                            "If it is a phase, mark required rows with Y.")
            continue
        phases.append((col, _cell_text(header[col])))
    names = [name for _, name in phases]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise MatrixError(f"Duplicate phase column(s): {', '.join(duplicates)}")
    if not phases:
        raise MatrixError("No phase columns found. Add one column per phase with Y in the rows required.")

    requirements: List[Requirement] = []
    categories: Dict[int, str] = {}
    seen: Dict[CodeKey, int] = {}
    duplicate_rows: List[str] = []
    numeric_codes = 0
    for offset, row in enumerate(body):
        row_number = header_index + offset + 2  # 1-based, as shown in Excel
        raw_code = row[code_col]
        code = _cell_text(raw_code)
        if not code:
            continue
        if isinstance(raw_code, float) and not raw_code.is_integer():
            numeric_codes += 1
        key = parse_code(code)
        if key is None:
            warnings.append(f"Row {row_number}: '{code}' isn't a numeric code like 2.11 - row skipped.")
            continue

        title = _cell_text(row[title_col]) if title_col is not None else ""
        marks: Dict[str, bool] = {}
        for col, phase in phases:
            mark = _required_mark(row[col])
            if mark is None:
                warnings.append(
                    f"Row {row_number} ({code}): '{_cell_text(row[col])}' under '{phase}' "
                    "isn't a recognised mark - treated as required."
                )
                mark = True
            marks[phase] = mark

        if len(key) == 1 and not any(marks.values()):
            categories[key[0]] = title
            continue
        if category_col is not None and _cell_text(row[category_col]):
            categories.setdefault(key[0], _cell_text(row[category_col]))

        if key in seen:
            duplicate_rows.append(f"{code} (rows {seen[key]} and {row_number})")
            continue
        seen[key] = row_number
        requirements.append(
            Requirement(
                code=code,
                key=key,
                title=title,
                allowed_types=parse_types(row[types_col]) if types_col is not None else (),
                phases=marks,
                row_number=row_number,
            )
        )

    excel_hint = (
        " Some codes are stored as numbers in Excel, so e.g. 2.10 becomes 2.1."
        " Format the Code column as Text and re-type the affected codes."
        if numeric_codes else ""
    )
    if duplicate_rows:
        raise MatrixError("Duplicate codes: " + "; ".join(duplicate_rows) + "." + excel_hint)
    if numeric_codes:
        warnings.append(
            f"{numeric_codes} code(s) are stored as numbers, not text. Codes like 2.10 can't be told "
            "apart from 2.1 that way - format the Code column as Text."
        )
    if not requirements:
        raise MatrixError("No document rows found under the header row.")

    requirements.sort(key=lambda r: r.key)
    return Matrix(
        source=source,
        phases=[name for _, name in phases],
        requirements=requirements,
        categories=categories,
        warnings=warnings,
    )
