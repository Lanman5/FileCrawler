import pytest
from conftest import EXAMPLES, matrix_from

from filecrawler.matrix import MatrixError, leading_code, load_matrix, parse_code, parse_rows, parse_types


@pytest.mark.parametrize("name, expected", [
    ("1.1", (1, 1)),
    ("1.10 Risk Register", (1, 10)),
    ("01.01 Report", (1, 1)),
    ("2.10_Design", (2, 10)),
    ("1.1.1 Appendix", (1, 1, 1)),
    ("1. General", (1,)),
    ("2.8 Fire Strategy.pdf", (2, 8)),
    ("Phase 2", None),
    ("Archive", None),
])
def test_leading_code(name, expected):
    assert leading_code(name) == expected


def test_parse_code_rejects_non_codes():
    assert parse_code(" 2.11 ") == (2, 11)
    assert parse_code("TBC") is None
    assert parse_code("2.1a") is None


@pytest.mark.parametrize("text, expected", [
    ("pdf; .DOCX, *.xlsx", (".pdf", ".docx", ".xlsx")),
    (".pdf/.dwg", (".pdf", ".dwg")),
    ("", ()),
    (None, ()),
    ("Any", ()),
    (".pdf .pdf", (".pdf",)),
])
def test_parse_types(text, expected):
    assert parse_types(text) == expected


def test_example_xlsx_loads():
    matrix = load_matrix(EXAMPLES / "example_matrix.xlsx")
    assert matrix.phases == ["Phase 1 - Tender", "Phase 2 - Design", "Phase 3 - Construction", "Phase 4 - Handover"]
    assert len(matrix.requirements) == 61
    assert len(matrix.categories) == 9
    assert matrix.categories[2] == "Design"
    assert len(matrix.required_for("Phase 2 - Design")) == 25
    codes = [r.code for r in matrix.requirements if r.category == 2]
    assert codes[-3:] == ["2.9", "2.10", "2.11"]  # numeric ordering, 2.10 kept distinct from 2.1
    assert matrix.warnings == []


def test_example_csv_matches_xlsx():
    xlsx = load_matrix(EXAMPLES / "example_matrix.xlsx")
    csv_ = load_matrix(EXAMPLES / "example_matrix.csv")
    assert [(r.code, r.title, r.allowed_types, r.phases) for r in xlsx.requirements] == \
           [(r.code, r.title, r.allowed_types, r.phases) for r in csv_.requirements]


def test_header_found_below_banner_and_marks_vocabulary():
    rows = [
        ["Project X matrix", None, None],
        [None, None, None],
        ["Ref", "Document", "Stage A", "Stage B", "Stage C", "Notes"],
        ["1.1", "Form", "Yes", "x", "N", "anything"],
        ["1.2", "Plan", 1, True, None, ""],
        ["1.3", "Spec", "-", "R", 0, ""],
    ]
    matrix = parse_rows(rows)
    assert matrix.phases == ["Stage A", "Stage B", "Stage C"]
    by_code = {r.code: r.phases for r in matrix.requirements}
    assert by_code["1.1"] == {"Stage A": True, "Stage B": True, "Stage C": False}
    assert by_code["1.2"] == {"Stage A": True, "Stage B": True, "Stage C": False}
    assert by_code["1.3"] == {"Stage A": False, "Stage B": True, "Stage C": False}


def test_unrecognised_mark_is_required_with_warning():
    matrix = matrix_from([["1.1", "Form", "", "Maybe", ""], ["1.2", "Plan", "", "Y", ""]])
    assert matrix.requirements[0].phases["P1"] is True
    assert any("Maybe" in w for w in matrix.warnings)


def test_free_text_column_is_not_a_phase():
    rows = [["Code", "Title", "Revision", "Owner", "P1", "P2"],
            ["1.1", "Form", "Rev A", "Alice", "Y", "M"],
            ["1.2", "Plan", "Rev B", "Bob", "", "Optional"]]
    matrix = parse_rows(rows)
    assert matrix.phases == ["P1", "P2"]
    assert any("Revision" in w for w in matrix.warnings)
    assert not any("Owner" in w for w in matrix.warnings)  # known non-phase header, dropped quietly
    assert [r.phases["P2"] for r in matrix.requirements] == [True, False]


def test_category_rows_and_invalid_codes():
    matrix = matrix_from([
        ["1", "General", "", "", ""],
        ["1.1", "Form", "", "Y", ""],
        ["TBC", "Something", "", "Y", ""],
        ["", "", "", "", ""],
    ])
    assert matrix.categories == {1: "General"}
    assert [r.code for r in matrix.requirements] == ["1.1"]
    assert any("TBC" in w for w in matrix.warnings)


def test_numeric_codes_that_collide_are_rejected():
    # What Excel does to a General-formatted Code column: 2.1 and 2.10 both become 2.1.
    with pytest.raises(MatrixError, match="Text"):
        matrix_from([[2.1, "Basis", "", "Y", ""], [2.1, "Risk register", "", "Y", ""]])


def test_numeric_codes_without_collision_warn():
    matrix = matrix_from([[1.1, "Form", "", "Y", ""], [1.2, "Plan", "", "Y", ""]])
    assert [r.key for r in matrix.requirements] == [(1, 1), (1, 2)]
    assert any("stored as numbers" in w for w in matrix.warnings)


def test_duplicate_text_codes_rejected():
    with pytest.raises(MatrixError, match="Duplicate codes"):
        matrix_from([["1.1", "A", "", "Y", ""], ["1.1", "B", "", "Y", ""]])


def test_missing_code_header():
    with pytest.raises(MatrixError, match="Code"):
        parse_rows([["Title", "P1"], ["Form", "Y"]])


def test_no_phase_columns():
    with pytest.raises(MatrixError, match="phase"):
        parse_rows([["Code", "Title"], ["1.1", "Form"]])


def test_semicolon_csv(tmp_path):
    path = tmp_path / "m.csv"
    path.write_text("Code;Title;Phase 1\n1.1;Form;Y\n2.10;Register;Y\n", encoding="utf-8")
    matrix = load_matrix(path)
    assert [r.code for r in matrix.requirements] == ["1.1", "2.10"]


def test_xls_rejected_with_advice(tmp_path):
    path = tmp_path / "old.xls"
    path.write_bytes(b"\xd0\xcf")
    with pytest.raises(MatrixError, match="xlsx"):
        load_matrix(path)


def test_xlsx_prefers_matrix_sheet(tmp_path):
    from openpyxl import Workbook
    workbook = Workbook()
    workbook.active.title = "Cover"
    workbook.active["A1"] = "Nothing to see here"
    sheet = workbook.create_sheet("Matrix")
    sheet.append(["Code", "Title", "P1"])
    sheet.append(["3.2", "Permit", "Y"])
    path = tmp_path / "m.xlsx"
    workbook.save(path)
    assert [r.code for r in load_matrix(path).requirements] == ["3.2"]
