"""Smoke test of the Tk window. Skipped where tkinter or a display isn't available
(on Linux CI, run under xvfb-run)."""
import os
import sys
import time

import pytest

tk = pytest.importorskip("tkinter")
if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
    pytest.skip("no display", allow_module_level=True)

from filecrawler.gui import FileCrawlerApp  # noqa: E402
from filecrawler.models import Bucket  # noqa: E402
from filecrawler.settings import Settings  # noqa: E402


def test_example_run_in_window():
    root = tk.Tk()
    try:
        app = FileCrawlerApp(root, Settings())
        app.load_example()
        assert app.phase_var.get() == "Phase 2 - Design"
        app.run()
        deadline = time.time() + 15
        while app._worker is not None and time.time() < deadline:
            root.update()
            time.sleep(0.02)
        root.update()
        assert app.report is not None
        assert len(app.tree.get_children()) == 25
        first = app._row_results[app.tree.get_children()[0]]
        assert first.bucket is Bucket.MISSING
        assert app.chips[Bucket.MISSING].cget("text") == "5  Missing"

        app.show_not_required_var.set(True)
        app._refresh_tree()
        assert len(app.tree.get_children()) == 61

        app.tree.selection_set(app.tree.get_children()[0])
        app._on_select()
        assert "1.3" in app.details_text.get("1.0", "end")
    finally:
        root.destroy()
