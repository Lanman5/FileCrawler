import threading

import pytest
from conftest import EXAMPLES, make_tree, make_zip, matrix_from

from filecrawler.ai_client import AIClient, AIVerdict
from filecrawler.checker import evaluate, run_check
from filecrawler.crawler import CrawlCancelled, CrawlError, list_source
from filecrawler.matrix import load_matrix
from filecrawler.models import Bucket, CheckMode, Status


def statuses(matrix, root, settings, phase="P1"):
    results, _ = evaluate(matrix, phase, list_source(root, settings), settings)
    return {r.requirement.code: r for r in results}


def test_1_1_and_1_10_are_different_folders(tmp_path, settings):
    matrix = matrix_from([["1.1", "A", "", "Y", ""], ["1.10", "B", "", "Y", ""]])
    make_tree(tmp_path, {"1.10 Register/reg.xlsx": b"x"})
    res = statuses(matrix, tmp_path, settings)
    assert res["1.1"].status is Status.MISSING
    assert res["1.10"].status is Status.PRESENT
    assert res["1.10"].files[0].name == "reg.xlsx"


def test_leading_zeros_and_nesting(tmp_path, settings):
    matrix = matrix_from([["2.1", "Basis", "", "Y", ""], ["2.2", "Drawings", "", "Y", ""]])
    make_tree(tmp_path, {
        "2 Design/02.01 Basis/basis.pdf": b"x",
        "2 Design/2.2 Drawings/Rev C/A-100.pdf": b"x",
    })
    res = statuses(matrix, tmp_path, settings)
    assert res["2.1"].status is Status.PRESENT
    assert res["2.1"].folders == ["2 Design/02.01 Basis"]
    assert res["2.2"].status is Status.PRESENT
    assert res["2.2"].files[0].rel_path == "2 Design/2.2 Drawings/Rev C/A-100.pdf"


def test_empty_zero_byte_and_junk_only_folders(tmp_path, settings):
    matrix = matrix_from([["1.1", "A", "", "Y", ""], ["1.2", "B", "", "Y", ""], ["1.3", "C", "", "Y", ""]])
    make_tree(tmp_path, {
        "1.1 A/": None,
        "1.2 B/report.docx": b"",
        "1.3 C/Thumbs.db": b"junk",
        "1.3 C/~$report.docx": b"lock",
    })
    res = statuses(matrix, tmp_path, settings)
    assert {code: r.status for code, r in res.items()} == {
        "1.1": Status.EMPTY, "1.2": Status.EMPTY, "1.3": Status.EMPTY}
    assert res["1.2"].empty_files[0].name == "report.docx"
    assert all(r.bucket is Bucket.MISSING for r in res.values())

    settings.zero_byte_is_empty = False
    assert statuses(matrix, tmp_path, settings)["1.2"].status is Status.PRESENT


def test_file_types(tmp_path, settings):
    matrix = matrix_from([
        ["1.1", "Drawings", ".pdf; .dwg", "Y", ""],
        ["1.2", "Mixed", ".pdf", "Y", ""],
        ["1.3", "Any", "", "Y", ""],
    ])
    make_tree(tmp_path, {
        "1.1 Drawings/photo.JPG": b"x",
        "1.2 Mixed/report.PDF": b"x",
        "1.2 Mixed/notes.txt": b"x",
        "1.3 Any/anything.xyz": b"x",
    })
    res = statuses(matrix, tmp_path, settings)
    assert res["1.1"].status is Status.WRONG_TYPE
    assert "found .jpg" in res["1.1"].notes[0]
    assert res["1.2"].status is Status.PRESENT  # extension check is case-insensitive
    assert [f.name for f in res["1.2"].other_files] == ["notes.txt"]
    assert res["1.3"].status is Status.PRESENT


def test_settings_type_rules_fill_blanks_but_matrix_wins(tmp_path, settings):
    matrix = matrix_from([["2.1", "Basis", "", "Y", ""], ["2.2", "Drawings", ".docx", "Y", ""]])
    make_tree(tmp_path, {"2.1 Basis/basis.docx": b"x", "2.2 Drawings/d.docx": b"x"})
    settings.type_rules = {"2": [".pdf"]}
    res = statuses(matrix, tmp_path, settings)
    assert res["2.1"].status is Status.WRONG_TYPE
    assert res["2.2"].status is Status.PRESENT


def test_misfiled_loose_and_in_other_folder(tmp_path, settings):
    matrix = matrix_from([["2.7", "A", "", "Y", ""], ["2.8", "Fire", "", "Y", ""], ["2.9", "Geo", "", "Y", ""]])
    make_tree(tmp_path, {
        "2 Design/2.8 Fire Strategy.pdf": b"x",
        "2 Design/2.7 Plumbing/2.9 Geotech.pdf": b"x",
    })
    res = statuses(matrix, tmp_path, settings)
    assert res["2.8"].status is Status.MISFILED
    assert res["2.8"].misfiled_files[0].rel_path == "2 Design/2.8 Fire Strategy.pdf"
    assert res["2.9"].status is Status.MISFILED
    assert res["2.7"].status is Status.PRESENT
    assert res["2.8"].bucket is Bucket.ATTENTION


def test_duplicate_folders_noted(tmp_path, settings):
    matrix = matrix_from([["3.1", "Planning", "", "Y", ""]])
    make_tree(tmp_path, {"3.1 Planning/a.pdf": b"x", "old/3.1 Planning copy/b.pdf": b"x"})
    res = statuses(matrix, tmp_path, settings)["3.1"]
    assert res.status is Status.PRESENT
    assert len(res.files) == 2
    assert any("2 folders" in n for n in res.notes)


def test_not_required_and_unrecognised(tmp_path, settings):
    matrix = matrix_from([["1.1", "A", "", "Y", ""], ["1.2", "B", "", "", "Y"]])
    make_tree(tmp_path, {
        "1.1 A/a.pdf": b"x",
        "1.1 A/1.1.1 Appendix/app.pdf": b"x",  # nested, belongs to 1.1
        "1.2 B/b.pdf": b"x",
        "4.7 Extra/x.pdf": b"x",
        "2024 Photos/p.jpg": b"x",               # not code-like enough to report
    })
    listing = list_source(tmp_path, settings)
    results, unrecognised = evaluate(matrix, "P1", listing, settings)
    res = {r.requirement.code: r for r in results}
    assert len(res["1.1"].files) == 2
    assert res["1.2"].status is Status.NOT_REQUIRED and not res["1.2"].required
    assert unrecognised == ["4.7 Extra"]


def test_ignored_folders(tmp_path, settings):
    matrix = matrix_from([["1.1", "A", "", "Y", ""]])
    make_tree(tmp_path, {"1.1 A/Superseded/old.pdf": b"x"})
    assert statuses(matrix, tmp_path, settings)["1.1"].status is Status.PRESENT
    settings.ignore_folder_names.append("superseded")
    assert statuses(matrix, tmp_path, settings)["1.1"].status is Status.EMPTY


SPEC = {
    "1 General/1.1 Form/form.pdf": b"x",
    "1 General/1.2 Plan/": None,
    "1 General/1.3 Spec/spec.docx": b"",
    "2.1 Basis.pdf": b"x",
}


def test_zip_and_folder_give_same_results(tmp_path, settings):
    matrix = matrix_from([["1.1", "", "", "Y", ""], ["1.2", "", "", "Y", ""],
                          ["1.3", "", "", "Y", ""], ["2.1", "", "", "Y", ""]])
    folder = make_tree(tmp_path / "folder", SPEC)
    zipped = make_zip(tmp_path / "sub.zip", SPEC, wrapper="Contractor Submission")

    def summary(source):
        return {r.requirement.code: (r.status, [f.rel_path for f in r.files + r.misfiled_files], r.folders)
                for r in statuses(matrix, source, settings).values()}

    assert summary(folder) == summary(zipped)
    assert summary(folder)["1.2"][0] is Status.EMPTY  # explicit empty dir entry in the zip


def test_zip_whose_top_folder_is_a_code_folder_is_not_unwrapped(tmp_path, settings):
    matrix = matrix_from([["2.4", "Drawings", "", "Y", ""]])
    zipped = make_zip(tmp_path / "one.zip", {"2.4 Structural Drawings/S-100.pdf": b"x"})
    assert statuses(matrix, zipped, settings)["2.4"].status is Status.PRESENT


def test_bad_source(tmp_path, settings):
    (tmp_path / "notes.txt").write_text("hi")
    with pytest.raises(CrawlError):
        list_source(tmp_path / "notes.txt", settings)
    with pytest.raises(CrawlError):
        list_source(tmp_path / "nope", settings)


def test_cancel(tmp_path, settings):
    matrix = matrix_from([["1.1", "A", "", "Y", ""]])
    make_tree(tmp_path, {"1.1 A/a.pdf": b"x"})
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(CrawlCancelled):
        run_check(matrix, "P1", tmp_path, CheckMode.PRESENCE, settings, cancel=cancel)


def test_example_submission_end_to_end(settings):
    matrix = load_matrix(EXAMPLES / "example_matrix.xlsx")
    report = run_check(matrix, "Phase 2 - Design", EXAMPLES / "sample_submission.zip", CheckMode.PRESENCE, settings)
    assert (report.count(Bucket.MISSING), report.count(Bucket.ATTENTION), report.count(Bucket.PRESENT)) == (5, 3, 17)
    by_code = {r.requirement.code: r.status for r in report.required_results}
    assert by_code["2.5"] is Status.MISSING
    assert by_code["2.6"] is Status.EMPTY
    assert by_code["2.4"] is Status.WRONG_TYPE
    assert by_code["2.8"] is Status.MISFILED
    assert by_code["2.9"] is Status.PRESENT and by_code["2.10"] is Status.PRESENT
    assert report.unrecognised_folders == ["7 Quality/7.9 Misc Correspondence"]
    # Missing first, then needs attention, then present.
    buckets = [r.bucket.value for r in report.results]
    assert buckets == sorted(buckets)
    assert report.results[0].requirement.code == "1.3"


def test_ai_mode_without_integration_falls_back_to_presence(tmp_path, settings):
    matrix = matrix_from([["1.1", "A", "", "Y", ""]])
    make_tree(tmp_path, {"1.1 A/a.pdf": b"x"})
    report = run_check(matrix, "P1", tmp_path, CheckMode.AI_BASIC, settings)
    assert report.results[0].status is Status.PRESENT
    assert "isn't configured" in report.ai_status
    assert report.ai_evaluation == []


class FakeAI(AIClient):
    configured = True

    def __init__(self):
        super().__init__()
        self.calls = []

    def check_completeness(self, file_path, requirement):
        self.calls.append(file_path.name)
        if requirement.code == "1.2":
            return AIVerdict(False, ["Section 3 is blank"])
        return AIVerdict(True)

    def review_content(self, file_path, requirement, phase=""):
        return AIVerdict(True, ["Looks fine"])

    def summarize(self, report):
        return ["Overall good"]


def test_ai_mode_with_client_sets_ok_and_incomplete(tmp_path, settings):
    matrix = matrix_from([["1.1", "A", "", "Y", ""], ["1.2", "B", "", "Y", ""], ["1.3", "C", "", "Y", ""]])
    make_tree(tmp_path, {"1.1 A/a.pdf": b"x", "1.2 B/b.pdf": b"x"})
    client = FakeAI()
    report = run_check(matrix, "P1", tmp_path, CheckMode.AI_BASIC, settings, ai_client=client)
    res = {r.requirement.code: r for r in report.results}
    assert res["1.1"].status is Status.OK
    assert res["1.2"].status is Status.INCOMPLETE and res["1.2"].ai_findings == ["Section 3 is blank"]
    assert res["1.3"].status is Status.MISSING
    assert sorted(client.calls) == ["a.pdf", "b.pdf"]  # missing documents aren't sent
    assert report.ai_evaluation == ["Overall good"]
    assert [r.requirement.code for r in report.results] == ["1.3", "1.2", "1.1"]
