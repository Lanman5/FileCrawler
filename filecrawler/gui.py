"""Tkinter front end."""
from __future__ import annotations

import copy
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont
from typing import Dict, Optional

from .checker import run_check
from .crawler import CrawlCancelled
from .matrix import MatrixError, load_matrix
from .models import Bucket, CheckMode, CheckReport, CheckResult, Matrix
from .report import BUCKET_COLOURS, export_report
from .settings import (
    DEFAULT_IGNORE_FILES,
    DEFAULT_IGNORE_FOLDERS,
    Settings,
    format_type_rules,
    parse_type_rules,
)

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
PHASES_PER_ROW = 2

MATRIX_HELP = """\
The matrix is an .xlsx (sheet named "Matrix", or the first sheet) or .csv file.

Header row (found automatically, anything above it is ignored):
  Code | Title | Allowed Types | <one column per phase>

Code (required)    Numeric code such as 1.1 or 2.11. In Excel, format this column
                   as TEXT - stored as a number, 2.10 turns into 2.1.
Title              Document name, shown in the results.
Allowed Types      Optional, e.g. ".pdf; .docx". Blank = any type, unless a rule
                   in Settings covers that code.
Phase columns      Any other column. Y / Yes / X / R / 1 = required in that phase.
                   Blank / N / No / - / 0 = not required.

A row with a single-number code (e.g. "2") and no phase marks is a category
heading - its title labels the category in the summary.

Folder matching: every folder in the submission whose name STARTS with a code is
used for that code, at any depth. "2.10 Risk Register" matches 2.10 (never 2.1),
and "02.01" matches 2.1. Files in sub-folders count towards the code folder.
"""


class FileCrawlerApp(ttk.Frame):
    def __init__(self, master: tk.Tk, settings: Settings):
        super().__init__(master, padding=10)
        self.settings = settings
        self.matrix: Optional[Matrix] = None
        self.report: Optional[CheckReport] = None
        self._row_results: Dict[str, CheckResult] = {}
        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._cancel = threading.Event()
        self._worker: Optional[threading.Thread] = None

        self.matrix_var = tk.StringVar()
        self.matrix_info_var = tk.StringVar(value="No matrix loaded.")
        self.source_var = tk.StringVar(value=settings.last_source)
        self.phase_var = tk.StringVar()
        self.mode_var = tk.StringVar(value=settings.last_mode)
        self.status_var = tk.StringVar(value="Load a matrix, pick a phase and a submission, then Run check.")
        self.show_not_required_var = tk.BooleanVar(value=settings.show_not_required)
        if self.mode_var.get() not in {m.value for m in CheckMode}:
            self.mode_var.set(CheckMode.PRESENCE.value)

        self._configure_styles()
        self._build_menu()
        self._build_inputs()
        self._build_results()
        self.pack(fill="both", expand=True)

        if settings.last_matrix and Path(settings.last_matrix).is_file():
            self.load_matrix(settings.last_matrix, quiet=True)

    # ------------------------------------------------------------------ layout
    def _configure_styles(self) -> None:
        style = ttk.Style(self.master)
        if sys.platform not in ("win32", "darwin") and "clam" in style.theme_names():
            style.theme_use("clam")
        base = tkfont.nametofont("TkDefaultFont")
        self.bold_font = base.copy()
        self.bold_font.configure(weight="bold")
        self.heading_font = base.copy()
        self.heading_font.configure(weight="bold", size=base.cget("size") + 2)
        style.configure("Treeview", rowheight=int(base.metrics("linespace") * 1.6))
        style.configure("Treeview.Heading", font=self.bold_font)
        style.configure("Run.TButton", font=self.bold_font, padding=(16, 6))
        style.configure("Hint.TLabel", foreground="#6c757d")
        for bucket, (background, text) in BUCKET_COLOURS.items():
            style.configure(f"{bucket.name}.Chip.TLabel", background="#" + background,
                            foreground="#" + text, font=self.bold_font, padding=(10, 4))
        # Tk 8.6.9 drops Treeview tag colours under some themes; this restores them.
        for option in ("foreground", "background"):
            style.map("Treeview", **{option: [
                spec for spec in style.map("Treeview", query_opt=option) if spec[:2] != ("!disabled", "!selected")
            ]})

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.master)
        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="Open matrix...", command=self.browse_matrix)
        file_menu.add_command(label="Choose submission folder...", command=self.browse_folder)
        file_menu.add_command(label="Choose submission .zip...", command=self.browse_zip)
        file_menu.add_separator()
        file_menu.add_command(label="Export results...", command=self.export)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.on_close)
        menubar.add_cascade(label="File", menu=file_menu)
        menubar.add_command(label="Settings", command=self.open_settings)
        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="Matrix format", command=self.show_matrix_help)
        if EXAMPLES_DIR.is_dir():
            help_menu.add_command(label="Load example matrix + submission", command=self.load_example)
        menubar.add_cascade(label="Help", menu=help_menu)
        self.master.config(menu=menubar)

    def _build_inputs(self) -> None:
        inputs = ttk.Frame(self)
        inputs.pack(fill="x")
        inputs.columnconfigure(0, weight=3, uniform="inputs")
        inputs.columnconfigure(1, weight=2, uniform="inputs")

        matrix_box = ttk.LabelFrame(inputs, text=" 1  Requirements matrix ", padding=8)
        matrix_box.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 6))
        matrix_box.columnconfigure(0, weight=1)
        ttk.Entry(matrix_box, textvariable=self.matrix_var, state="readonly").grid(row=0, column=0, sticky="ew")
        ttk.Button(matrix_box, text="Browse...", command=self.browse_matrix).grid(row=0, column=1, padx=(6, 0))
        ttk.Label(matrix_box, textvariable=self.matrix_info_var, style="Hint.TLabel").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        self.phase_box = ttk.LabelFrame(inputs, text=" 2  Current phase ", padding=8)
        self.phase_box.grid(row=0, column=1, sticky="nsew", pady=(0, 6))
        self.phase_placeholder = ttk.Label(self.phase_box, text="Load a matrix to see its phases.",
                                           style="Hint.TLabel")
        self.phase_placeholder.grid(row=0, column=0, sticky="w")

        source_box = ttk.LabelFrame(inputs, text=" 3  Submission to check ", padding=8)
        source_box.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        source_box.columnconfigure(0, weight=1)
        ttk.Entry(source_box, textvariable=self.source_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(source_box, text="Folder...", command=self.browse_folder).grid(row=0, column=1, padx=(6, 0))
        ttk.Button(source_box, text=".zip...", command=self.browse_zip).grid(row=0, column=2, padx=(6, 0))
        ttk.Label(source_box, text="Pick the top-level folder - all sub-folders are searched.",
                  style="Hint.TLabel").grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))

        mode_box = ttk.LabelFrame(inputs, text=" 4  Check mode ", padding=8)
        mode_box.grid(row=1, column=1, sticky="nsew")
        for row, mode in enumerate(CheckMode):
            ttk.Radiobutton(mode_box, text=mode.label, value=mode.value, variable=self.mode_var).grid(
                row=row, column=0, sticky="w")
        ttk.Label(mode_box, text="AI modes run the presence check until the API is connected.",
                  style="Hint.TLabel").grid(row=3, column=0, sticky="w", pady=(4, 0))

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(10, 6))
        self.run_button = ttk.Button(actions, text="Run check", style="Run.TButton", command=self.run)
        self.run_button.pack(side="left")
        self.cancel_button = ttk.Button(actions, text="Cancel", command=self.cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=(6, 0))
        self.progress = ttk.Progressbar(actions, mode="determinate", length=140)
        self.progress.pack(side="left", padx=10)
        ttk.Label(actions, textvariable=self.status_var).pack(side="left", fill="x", expand=True)

    def _build_results(self) -> None:
        results = ttk.LabelFrame(self, text=" Results ", padding=8)
        results.pack(fill="both", expand=True)

        header = ttk.Frame(results)
        header.pack(fill="x", pady=(0, 6))
        self.summary_label = ttk.Label(header, text="", font=self.heading_font)
        self.summary_label.pack(side="left")
        self.chips: Dict[Bucket, ttk.Label] = {}
        for bucket in (Bucket.MISSING, Bucket.ATTENTION, Bucket.PRESENT):
            chip = ttk.Label(header, text="", style=f"{bucket.name}.Chip.TLabel")
            self.chips[bucket] = chip
        ttk.Button(header, text="Export...", command=self.export).pack(side="right")
        ttk.Checkbutton(header, text="Show not-required items", variable=self.show_not_required_var,
                        command=self._refresh_tree).pack(side="right", padx=10)

        panes = ttk.PanedWindow(results, orient="vertical")
        panes.pack(fill="both", expand=True)

        tree_frame = ttk.Frame(panes)
        columns = ("status", "code", "title", "files", "location", "notes")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse")
        for column, heading, width, stretch in (
            ("status", "Status", 120, False),
            ("code", "Code", 60, False),
            ("title", "Document", 230, True),
            ("files", "File(s) found", 220, True),
            ("location", "Folder", 220, True),
            ("notes", "Notes", 300, True),
        ):
            self.tree.heading(column, text=heading, anchor="w")
            self.tree.column(column, width=width, stretch=stretch, anchor="w")
        for bucket, (background, text) in BUCKET_COLOURS.items():
            self.tree.tag_configure(bucket.name, background="#" + background, foreground="#" + text)
        y_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        x_scroll = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._on_double_click)
        panes.add(tree_frame, weight=3)

        self.notebook = ttk.Notebook(panes)
        self.evaluation_text = self._make_text(self.notebook)
        self.details_text = self._make_text(self.notebook)
        self.notebook.add(self.evaluation_text.master, text="Evaluation")
        self.notebook.add(self.details_text.master, text="Selected document")
        panes.add(self.notebook, weight=2)
        ttk.Label(results, text="Double-click a row to open its folder.", style="Hint.TLabel").pack(
            anchor="w", pady=(4, 0))

    def _make_text(self, parent: tk.Widget) -> tk.Text:
        frame = ttk.Frame(parent)
        text = tk.Text(frame, wrap="word", height=8, relief="flat", padx=8, pady=6,
                       font=tkfont.nametofont("TkDefaultFont"))
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set, state="disabled")
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        text.tag_configure("h", font=self.bold_font, spacing1=6, spacing3=2)
        text.tag_configure("bullet", lmargin1=8, lmargin2=22, spacing1=2)
        text.tag_configure("muted", foreground="#6c757d")
        text.tag_configure("warn", foreground="#842029")
        return text

    # ----------------------------------------------------------------- inputs
    def browse_matrix(self) -> None:
        path = filedialog.askopenfilename(
            title="Open requirements matrix",
            filetypes=[("Matrix files", "*.xlsx *.xlsm *.csv"), ("All files", "*.*")],
            initialdir=self._initial_dir(self.matrix_var.get()),
        )
        if path:
            self.load_matrix(path)

    def load_matrix(self, path: str, quiet: bool = False) -> bool:
        try:
            matrix = load_matrix(path)
        except MatrixError as exc:
            if not quiet:
                messagebox.showerror("Can't use this matrix", str(exc), parent=self.master)
            return False
        self.matrix = matrix
        self.matrix_var.set(path)
        self.settings.last_matrix = path
        categories = len({r.category for r in matrix.requirements})
        self.matrix_info_var.set(
            f"{len(matrix.requirements)} documents in {categories} categories, {len(matrix.phases)} phases"
            + (f" - {len(matrix.warnings)} warning(s)" if matrix.warnings else "")
        )
        self._build_phase_buttons()
        if matrix.warnings and not quiet:
            shown = "\n".join(f"- {w}" for w in matrix.warnings[:10])
            more = f"\n...and {len(matrix.warnings) - 10} more" if len(matrix.warnings) > 10 else ""
            messagebox.showwarning("Matrix loaded with warnings", shown + more, parent=self.master)
        return True

    def _build_phase_buttons(self) -> None:
        for child in self.phase_box.winfo_children():
            child.destroy()
        assert self.matrix is not None
        phases = self.matrix.phases
        current = self.phase_var.get() or self.settings.last_phase
        self.phase_var.set(current if current in phases else phases[0])
        for index, phase in enumerate(phases):
            count = len(self.matrix.required_for(phase))
            ttk.Radiobutton(self.phase_box, text=f"{phase}  ({count})", value=phase,
                            variable=self.phase_var).grid(
                row=index // PHASES_PER_ROW, column=index % PHASES_PER_ROW, sticky="w", padx=(0, 12))

    def browse_folder(self) -> None:
        path = filedialog.askdirectory(title="Choose the submission's top-level folder",
                                       initialdir=self._initial_dir(self.source_var.get()), mustexist=True)
        if path:
            self.source_var.set(path)

    def browse_zip(self) -> None:
        path = filedialog.askopenfilename(title="Choose a zipped submission",
                                          filetypes=[("Zip archives", "*.zip"), ("All files", "*.*")],
                                          initialdir=self._initial_dir(self.source_var.get()))
        if path:
            self.source_var.set(path)

    @staticmethod
    def _initial_dir(current: str) -> str:
        if current:
            path = Path(current)
            return str(path if path.is_dir() else path.parent)
        return str(Path.home())

    def load_example(self) -> None:
        if self.load_matrix(str(EXAMPLES_DIR / "example_matrix.xlsx")):
            self.source_var.set(str(EXAMPLES_DIR / "sample_submission.zip"))
            if "Phase 2 - Design" in self.matrix.phases:
                self.phase_var.set("Phase 2 - Design")
            self.status_var.set("Example loaded - press Run check.")

    # -------------------------------------------------------------- run check
    def run(self) -> None:
        if self._worker is not None:
            return
        if self.matrix is None:
            messagebox.showinfo("No matrix", "Load a requirements matrix first (step 1).", parent=self.master)
            return
        source = self.source_var.get().strip()
        if not source or not Path(source).exists():
            messagebox.showinfo("No submission", "Choose the submission folder or .zip (step 3).",
                                parent=self.master)
            return
        phase = self.phase_var.get()
        mode = CheckMode(self.mode_var.get())
        self.settings.last_source, self.settings.last_phase, self.settings.last_mode = source, phase, mode.value

        self._cancel.clear()
        self.run_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        matrix = self.matrix
        settings_snapshot = copy.deepcopy(self.settings)

        def work() -> None:
            try:
                report = run_check(matrix, phase, source, mode, settings_snapshot,
                                   progress=lambda message: self._queue.put(("progress", message)),
                                   cancel=self._cancel)
                self._queue.put(("done", report))
            except CrawlCancelled:
                self._queue.put(("cancelled", None))
            except Exception as exc:  # CrawlError, OSError, ... - report it, keep the UI alive
                self._queue.put(("error", exc))

        self._worker = threading.Thread(target=work, daemon=True)
        self._worker.start()
        self.after(100, self._poll)

    def cancel(self) -> None:
        self._cancel.set()
        self.status_var.set("Cancelling...")

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "progress":
                    self.status_var.set(payload)
                    continue
                self._finish()
                if kind == "done":
                    self.show_report(payload)
                elif kind == "cancelled":
                    self.status_var.set("Check cancelled.")
                else:
                    self.status_var.set("Check failed.")
                    messagebox.showerror("Check failed", str(payload), parent=self.master)
                return
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _finish(self) -> None:
        self._worker = None
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self.run_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")

    # ---------------------------------------------------------------- results
    def show_report(self, report: CheckReport) -> None:
        self.report = report
        required = len(report.required_results)
        self.summary_label.configure(text=f"{report.phase}: {required} required")
        for bucket, chip in self.chips.items():
            chip.configure(text=f"{report.count(bucket)}  {bucket.label}")
            chip.pack(side="left", padx=(10, 0))
        self._refresh_tree()
        self._render_evaluation(report)
        self.notebook.select(0)
        self.status_var.set(
            f"Checked {Path(report.source).name} at {report.generated_at:%H:%M:%S} ({report.mode.label})."
        )

    def _refresh_tree(self) -> None:
        self.settings.show_not_required = self.show_not_required_var.get()
        self.tree.delete(*self.tree.get_children())
        self._row_results.clear()
        if self.report is None:
            return
        for result in self.report.results:
            if not result.required and not self.show_not_required_var.get():
                continue
            req = result.requirement
            location = "; ".join(result.folders) or (result.misfiled_files[0].folder if result.misfiled_files else "")
            iid = self.tree.insert("", "end", tags=(result.bucket.name,), values=(
                result.status.label, req.code, req.title, result.file_names, location,
                "; ".join(result.notes + result.ai_findings),
            ))
            self._row_results[iid] = result

    def _write(self, widget: tk.Text, chunks) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        for text, tag in chunks:
            widget.insert("end", text, tag)
        widget.configure(state="disabled")

    def _render_evaluation(self, report: CheckReport) -> None:
        chunks = [("Summary\n", "h")]
        chunks += [(f"•  {line}\n", "bullet") for line in report.evaluation]
        chunks.append(("AI evaluation\n", "h"))
        if report.ai_evaluation:
            chunks += [(f"•  {line}\n", "bullet") for line in report.ai_evaluation]
        elif report.ai_status:
            chunks.append((report.ai_status + "\n", "muted"))
        else:
            chunks.append(("Not run - choose check mode 2 or 3 for an AI evaluation.\n", "muted"))
        if report.warnings:
            chunks.append((f"Warnings ({len(report.warnings)})\n", "h"))
            chunks += [(f"•  {w}\n", ("bullet", "warn")) for w in report.warnings]
        self._write(self.evaluation_text, chunks)

    def _on_select(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        result = self._row_results[selection[0]]
        req = result.requirement
        chunks = [
            (f"{req.code}  {req.title}\n", "h"),
            (f"Status: {result.status.label}    Required this phase: {'yes' if result.required else 'no'}"
             f"    Allowed types: {', '.join(result.allowed_types) or 'any'}\n", ()),
        ]

        def section(title, items, warn=False):
            if items:
                chunks.append((title + "\n", "h"))
                tags = ("bullet", "warn") if warn else ("bullet",)
                chunks.extend((f"•  {item}\n", tags) for item in items)

        section("Folder(s)", result.folders)
        section("Files", [f"{f.rel_path}  ({_size(f.size)})" for f in result.files])
        section("Files of another type", [f.rel_path for f in result.other_files], warn=True)
        section("Zero-byte files", [f.rel_path for f in result.empty_files], warn=True)
        section("Possible match outside its folder", [f.rel_path for f in result.misfiled_files], warn=True)
        section("Notes", result.notes)
        section("AI findings", result.ai_findings)
        self._write(self.details_text, chunks)
        self.notebook.select(1)

    def _on_double_click(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if not iid or self.report is None:
            return
        if self.report.source_is_zip:
            messagebox.showinfo("Zip submission", "Folders inside a .zip can't be opened directly.",
                                parent=self.master)
            return
        result = self._row_results[iid]
        relative = (result.folders[0] if result.folders
                    else result.misfiled_files[0].folder if result.misfiled_files else "")
        _open_in_file_manager(Path(self.report.source) / relative)

    def export(self) -> None:
        if self.report is None:
            messagebox.showinfo("Nothing to export", "Run a check first.", parent=self.master)
            return
        default = f"check_{self.report.phase}_{self.report.generated_at:%Y%m%d_%H%M}".replace(" ", "_")
        path = filedialog.asksaveasfilename(
            title="Export results", defaultextension=".xlsx", initialfile=default,
            filetypes=[("Excel workbook", "*.xlsx"), ("CSV", "*.csv")],
        )
        if not path:
            return
        try:
            export_report(self.report, path)
        except OSError as exc:
            messagebox.showerror("Export failed", str(exc), parent=self.master)
            return
        self.status_var.set(f"Exported to {path}")

    # ------------------------------------------------------------ misc dialogs
    def open_settings(self) -> None:
        SettingsDialog(self.master, self.settings, on_save=self._settings_saved)

    def _settings_saved(self) -> None:
        self._save_settings()
        self.status_var.set("Settings saved - run the check again to apply them.")

    def show_matrix_help(self) -> None:
        window = tk.Toplevel(self.master)
        window.title("Matrix format")
        text = tk.Text(window, wrap="none", width=86, height=28, padx=10, pady=10,
                       font=tkfont.nametofont("TkFixedFont"))
        text.insert("1.0", MATRIX_HELP)
        text.configure(state="disabled")
        text.pack(fill="both", expand=True)

    def _save_settings(self) -> None:
        try:
            self.settings.save()
        except OSError:
            pass  # remembering paths is a convenience, not worth an error dialog

    def on_close(self) -> None:
        self._cancel.set()
        self._save_settings()
        self.master.destroy()


class SettingsDialog(tk.Toplevel):
    def __init__(self, master: tk.Tk, settings: Settings, on_save):
        super().__init__(master)
        self.title("Settings")
        self.transient(master)
        self.settings = settings
        self.on_save = on_save
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)

        ttk.Label(body, text="Required file types by code").grid(row=0, column=0, sticky="w")
        ttk.Label(body, style="Hint.TLabel", text=(
            "One rule per line: code = types. A category ('2') covers all its codes; the most specific rule\n"
            "wins. Only used where the matrix's Allowed Types cell is blank. Example:  2.2 = .pdf, .dwg"
        )).grid(row=1, column=0, sticky="w")
        rules = format_type_rules(settings.type_rules) or "# 2 = .pdf\n# 2.2 = .pdf, .dwg"
        self.rules_text = self._text(body, 2, rules, height=7)

        ttk.Label(body, text="Ignore files matching (one pattern per line, * wildcards)").grid(
            row=3, column=0, sticky="w", pady=(10, 0))
        self.files_text = self._text(body, 4, "\n".join(settings.ignore_file_patterns), height=5)

        ttk.Label(body, text="Skip folders named (e.g. Superseded, Archive)").grid(
            row=5, column=0, sticky="w", pady=(10, 0))
        self.folders_text = self._text(body, 6, "\n".join(settings.ignore_folder_names), height=3)

        self.zero_var = tk.BooleanVar(value=settings.zero_byte_is_empty)
        ttk.Checkbutton(body, text="Treat zero-byte files as missing", variable=self.zero_var).grid(
            row=7, column=0, sticky="w", pady=(10, 0))

        buttons = ttk.Frame(body)
        buttons.grid(row=8, column=0, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Restore defaults", command=self.restore_defaults).pack(side="left")
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="left", padx=6)
        ttk.Button(buttons, text="Save", command=self.save).pack(side="left")
        self.grab_set()

    @staticmethod
    def _text(parent, row: int, value: str, height: int) -> tk.Text:
        text = tk.Text(parent, width=70, height=height, wrap="none", font=tkfont.nametofont("TkFixedFont"))
        text.insert("1.0", value)
        text.grid(row=row, column=0, sticky="nsew", pady=(4, 0))
        return text

    @staticmethod
    def _lines(text: tk.Text):
        return [line.strip() for line in text.get("1.0", "end").splitlines() if line.strip()]

    def restore_defaults(self) -> None:
        for widget, value in ((self.files_text, DEFAULT_IGNORE_FILES), (self.folders_text, DEFAULT_IGNORE_FOLDERS)):
            widget.delete("1.0", "end")
            widget.insert("1.0", "\n".join(value))
        self.zero_var.set(True)

    def save(self) -> None:
        rules, errors = parse_type_rules(self.rules_text.get("1.0", "end"))
        if errors:
            messagebox.showerror("Check the file type rules", "\n".join(errors), parent=self)
            return
        self.settings.type_rules = rules
        self.settings.ignore_file_patterns = self._lines(self.files_text)
        self.settings.ignore_folder_names = self._lines(self.folders_text)
        self.settings.zero_byte_is_empty = self.zero_var.get()
        self.on_save()
        self.destroy()


def _size(size: int) -> str:
    value = float(size)
    for unit in ("bytes", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)} {unit}" if unit == "bytes" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} bytes"


def _open_in_file_manager(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError as exc:
        messagebox.showerror("Couldn't open folder", str(exc))


def launch(matrix_path: Optional[str] = None) -> None:
    if sys.platform == "win32":
        try:  # crisp text on high-DPI screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    root.title("FileCrawler - submission checker")
    root.geometry("1200x820")
    root.minsize(900, 600)
    app = FileCrawlerApp(root, Settings.load())
    if matrix_path:
        app.load_matrix(matrix_path)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
