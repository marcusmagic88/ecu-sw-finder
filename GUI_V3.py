import os
import re
import csv
import queue
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Colors ────────────────────────────────────────────────────────────────────
BG      = "#1e1e2e"
BG2     = "#2a2a3e"
BG3     = "#313244"
FG      = "#cdd6f4"
FG2     = "#a6adc8"
ACCENT  = "#89b4fa"
GREEN   = "#a6e3a1"
RED     = "#f38ba8"
SEL     = "#45475a"
BORDER  = "#45475a"

# ── Parsing ───────────────────────────────────────────────────────────────────
FIELD_PATTERNS = {
    "hw":        re.compile(r"^HW:\s*(.+)$",         re.I | re.M),
    "sw":        re.compile(r"^SW:\s*(.+)$",          re.I | re.M),
    "sw2":       re.compile(r"^SW2:\s*(.+)$",         re.I | re.M),
    "cal":       re.compile(r"^CAL:\s*(.+)$",         re.I | re.M),
    "fal_pn":    re.compile(r"^FAL PN:\s*(.+)$",      re.I | re.M),
    "type":      re.compile(r"^Type:\s*(.+)$",         re.I | re.M),
    "vin":       re.compile(r"^VIN:\s*(\S+)",          re.I | re.M),
    "mileage":   re.compile(r"Total mileage:\s*(\d+)", re.I),
    "operation": re.compile(r"[-]{5,}\s+((?!Connect)\w[\w\s/]+?)\s+Started", re.I),
}
DATE_RE = re.compile(r"_(\d{14})[_.]")

COLUMNS = [
    ("date",      "Data",        130, "center"),
    ("operation", "Operazione",  150, "w"),
    ("sw",        "SW",          105, "w"),
    ("sw2",       "SW2",          70, "w"),
    ("hw",        "HW",          115, "w"),
    ("cal",       "CAL",          70, "w"),
    ("fal_pn",    "FAL PN",       90, "w"),
    ("type",      "Type",         75, "w"),
    ("vin",       "VIN",         150, "w"),
    ("mileage",   "Km",           65, "e"),
    ("file",      "File",        280, "w"),
]


def detect_encoding(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    return "utf-8"


def parse_history_file(path: str) -> dict | None:
    try:
        with open(path, "rb") as f:
            raw = f.read()
        enc = detect_encoding(raw)
        try:
            text = raw.decode(enc, errors="replace")
        except Exception:
            text = raw.decode("latin-1", errors="replace")
    except Exception:
        return None

    rec = {k: "" for k in ("date", "operation", "vin", "hw", "sw", "sw2",
                            "cal", "fal_pn", "type", "mileage", "file", "path")}
    rec["path"] = path
    rec["file"] = os.path.basename(path)

    m = DATE_RE.search(os.path.basename(path))
    if m:
        try:
            rec["date"] = datetime.strptime(m.group(1), "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass

    for key, pat in FIELD_PATTERNS.items():
        if key == "operation":
            ops = pat.findall(text)
            if ops:
                rec["operation"] = ops[-1].strip()
        else:
            fm = pat.search(text)
            if fm:
                rec[key] = fm.group(1).strip()

    return rec


# ── App ───────────────────────────────────────────────────────────────────────
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("ECU Session Browser")
        self.root.state("zoomed")
        self.root.configure(bg=BG)

        self._records:  list[dict] = []
        self._filtered: list[dict] = []
        self._iid_map:  dict[str, str] = {}   # iid -> full path
        self._sort_col  = "date"
        self._sort_rev  = False
        self._queue     = queue.Queue()
        self._stop_evt  = threading.Event()

        self._set_icon()
        self._apply_theme()
        self._build_ui()

    # ── Icon ──────────────────────────────────────────────────────────────────
    def _set_icon(self):
        ico = os.path.join(BASE_DIR, "Logo.ico")
        if not os.path.exists(ico):
            return
        if PIL_AVAILABLE:
            try:
                img = Image.open(ico)
                self._ico_ref = ImageTk.PhotoImage(img)
                self.root.iconphoto(True, self._ico_ref)
                return
            except Exception:
                pass
        try:
            self.root.iconbitmap(ico)
        except Exception:
            pass

    # ── Theme ─────────────────────────────────────────────────────────────────
    def _apply_theme(self):
        s = ttk.Style(self.root)
        s.theme_use("clam")

        s.configure(".",
                    background=BG, foreground=FG,
                    fieldbackground=BG2, bordercolor=BORDER,
                    troughcolor=BG2, insertcolor=FG)

        s.configure("TFrame",   background=BG)
        s.configure("TLabel",   background=BG, foreground=FG)
        s.configure("TSeparator", background=BORDER)

        s.configure("TEntry",
                    fieldbackground=BG2, foreground=FG,
                    insertcolor=FG, borderwidth=1, relief="flat")

        s.configure("TCombobox",
                    fieldbackground=BG2, foreground=FG,
                    selectbackground=SEL, selectforeground=FG,
                    arrowcolor=FG)
        s.map("TCombobox", fieldbackground=[("readonly", BG2)],
              selectbackground=[("readonly", BG2)])

        s.configure("TButton",
                    background=BG3, foreground=FG,
                    relief="flat", padding=(10, 5), borderwidth=0)
        s.map("TButton", background=[("active", SEL), ("disabled", BG2)],
              foreground=[("disabled", FG2)])

        s.configure("Accent.TButton",
                    background=ACCENT, foreground=BG,
                    font=("Helvetica", 11, "bold"), padding=(12, 6))
        s.map("Accent.TButton", background=[("active", "#74aee8")])

        s.configure("TProgressbar", troughcolor=BG2, background=ACCENT, borderwidth=0)

        s.configure("Treeview",
                    background=BG2, foreground=FG,
                    fieldbackground=BG2, rowheight=26,
                    font=("Consolas", 10), borderwidth=0)
        s.configure("Treeview.Heading",
                    background=BG3, foreground=ACCENT,
                    relief="flat", font=("Helvetica", 10, "bold"))
        s.map("Treeview",
              background=[("selected", SEL)],
              foreground=[("selected", FG)])
        s.map("Treeview.Heading", background=[("active", SEL)])

        s.configure("TScrollbar",
                    background=BG3, troughcolor=BG2,
                    arrowcolor=FG2, borderwidth=0, relief="flat")

        s.configure("Status.TLabel",
                    background=BG3, foreground=FG2,
                    font=("Helvetica", 9), padding=(10, 4))

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        # ── Header ──
        hdr = ttk.Frame(self.root, padding=(14, 10, 14, 6))
        hdr.pack(fill=tk.X)

        ttk.Label(hdr, text="ECU Session Browser",
                  font=("Helvetica", 17, "bold"),
                  foreground=ACCENT).pack(side=tk.LEFT)

        # ── Directory row ──
        dir_row = ttk.Frame(self.root, padding=(14, 4, 14, 2))
        dir_row.pack(fill=tk.X)
        dir_row.columnconfigure(1, weight=1)

        ttk.Label(dir_row, text="Directory:", width=10).grid(row=0, column=0, sticky="e", padx=(0, 6))
        self.entry_dir = ttk.Entry(dir_row)
        self.entry_dir.grid(row=0, column=1, sticky="ew")
        ttk.Button(dir_row, text="Sfoglia", command=self._browse,
                   width=9).grid(row=0, column=2, padx=(6, 0))

        # ── Controls row ──
        ctrl = ttk.Frame(self.root, padding=(14, 6, 14, 6))
        ctrl.pack(fill=tk.X)

        self.btn_scan = ttk.Button(ctrl, text="⟳  Scansiona",
                                   style="Accent.TButton", command=self._start_scan)
        self.btn_scan.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_stop = ttk.Button(ctrl, text="◼  Stop",
                                   command=self._stop_scan, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 16))

        ttk.Separator(ctrl, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=(0, 16))

        ttk.Label(ctrl, text="Cerca:").pack(side=tk.LEFT, padx=(0, 4))
        self.entry_search = ttk.Entry(ctrl, width=34)
        self.entry_search.pack(side=tk.LEFT, padx=(0, 6))
        self.entry_search.bind("<KeyRelease>", lambda _: self._apply_filter())

        ttk.Label(ctrl, text="in:").pack(side=tk.LEFT, padx=(0, 4))
        self.filter_col = tk.StringVar(value="Tutti")
        filter_labels = ["Tutti"] + [c[1] for c in COLUMNS if c[0] != "file"]
        ttk.Combobox(ctrl, textvariable=self.filter_col, values=filter_labels,
                     state="readonly", width=12).pack(side=tk.LEFT, padx=(0, 16))
        self.filter_col.trace_add("write", lambda *_: self._apply_filter())

        ttk.Separator(ctrl, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=(0, 16))

        ttk.Button(ctrl, text="Pulisci",       command=self._clear).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(ctrl, text="Esporta CSV",   command=self._export_csv).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(ctrl, text="Apri cartella", command=self._open_folder).pack(side=tk.LEFT)

        # ── Progress bar ──
        prog = ttk.Frame(self.root, padding=(14, 0, 14, 4))
        prog.pack(fill=tk.X)
        self.progress_bar = ttk.Progressbar(prog, orient="horizontal", mode="determinate")
        self.progress_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        self.pct_label = ttk.Label(prog, text="", width=5, anchor="e")
        self.pct_label.pack(side=tk.LEFT)

        # ── Treeview ──
        tree_frame = ttk.Frame(self.root, padding=(14, 0, 14, 0))
        tree_frame.pack(fill=tk.BOTH, expand=True)
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        col_ids = [c[0] for c in COLUMNS]
        self.tree = ttk.Treeview(tree_frame, columns=col_ids,
                                 show="headings", selectmode="browse")

        for cid, label, width, anchor in COLUMNS:
            self.tree.heading(cid, text=label, anchor="center",
                              command=lambda c=cid: self._sort_by(c))
            self.tree.column(cid, width=width, minwidth=50,
                             anchor=anchor, stretch=False)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical",   command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        self.tree.bind("<Double-1>", self._on_double_click)

        # alternating row colors
        self.tree.tag_configure("odd",  background="#252535")
        self.tree.tag_configure("even", background=BG2)

        # ── Status bar ──
        self.status_var = tk.StringVar(value="Pronto — seleziona una directory e premi Scansiona.")
        ttk.Label(self.root, textvariable=self.status_var,
                  style="Status.TLabel").pack(fill=tk.X, side=tk.BOTTOM)

    # ── Actions ───────────────────────────────────────────────────────────────
    def _browse(self):
        d = filedialog.askdirectory()
        if d:
            self.entry_dir.delete(0, tk.END)
            self.entry_dir.insert(0, d)

    def _clear(self):
        self.entry_search.delete(0, tk.END)
        self._records.clear()
        self._filtered.clear()
        self._refresh_tree()
        self.progress_bar["value"] = 0
        self.pct_label.config(text="")
        self.status_var.set("Pronto — seleziona una directory e premi Scansiona.")

    def _start_scan(self):
        directory = self.entry_dir.get().strip()
        if not directory or not os.path.isdir(directory):
            messagebox.showwarning("Directory", "Seleziona una directory valida.")
            return
        self._records.clear()
        self._filtered.clear()
        self._refresh_tree()
        self._stop_evt.clear()
        self.btn_scan.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.progress_bar["value"] = 0
        self.pct_label.config(text="")
        threading.Thread(target=self._scan_worker, args=(directory,), daemon=True).start()
        self.root.after(100, self._poll_queue)

    def _stop_scan(self):
        self._stop_evt.set()

    def _scan_worker(self, directory: str):
        files = [
            os.path.join(r, n)
            for r, _, names in os.walk(directory)
            for n in names
            if n.lower().endswith("history.txt")
        ]
        total = len(files)
        self._queue.put(("status", f"Trovati {total} file history da analizzare..."))

        for i, path in enumerate(files, 1):
            if self._stop_evt.is_set():
                break
            self._queue.put(("progress", i, total))
            rec = parse_history_file(path)
            if rec:
                self._queue.put(("record", rec))

        self._queue.put(("done", total))

    def _poll_queue(self):
        try:
            while True:
                item = self._queue.get_nowait()
                tag = item[0]
                if tag == "status":
                    self.status_var.set(item[1])
                elif tag == "progress":
                    _, val, total = item
                    self.progress_bar["maximum"] = total
                    self.progress_bar["value"]   = val
                    self.pct_label.config(text=f"{int(val / total * 100)}%")
                elif tag == "record":
                    self._records.append(item[1])
                    self._apply_filter(silent=True)
                elif tag == "done":
                    total = item[1]
                    self.btn_scan.config(state=tk.NORMAL)
                    self.btn_stop.config(state=tk.DISABLED)
                    n = len(self._records)
                    self.status_var.set(
                        f"Scansione completata — {n} sessioni trovate su {total} file."
                    )
                    return
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    # ── Filter & sort ─────────────────────────────────────────────────────────
    def _apply_filter(self, silent=False):
        query     = self.entry_search.get().strip().lower()
        col_label = self.filter_col.get()

        col_id = None
        if col_label != "Tutti":
            for cid, label, *_ in COLUMNS:
                if label == col_label:
                    col_id = cid
                    break

        if not query:
            self._filtered = list(self._records)
        elif col_id:
            self._filtered = [r for r in self._records if query in r.get(col_id, "").lower()]
        else:
            self._filtered = [r for r in self._records
                              if any(query in str(v).lower() for v in r.values())]

        self._refresh_tree()
        if not silent:
            self.status_var.set(
                f"{len(self._filtered)} risultati  (totale: {len(self._records)})"
            )

    def _sort_by(self, col: str):
        self._sort_rev = (col == self._sort_col) and not self._sort_rev
        self._sort_col = col
        self._filtered.sort(key=lambda r: r.get(col, "").lower(), reverse=self._sort_rev)
        self._refresh_tree()

    def _refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        self._iid_map.clear()
        for i, rec in enumerate(self._filtered):
            iid  = str(i)
            vals = tuple(rec.get(c[0], "") for c in COLUMNS)
            tag  = "odd" if i % 2 else "even"
            self.tree.insert("", tk.END, values=vals, iid=iid, tags=(tag,))
            self._iid_map[iid] = rec["path"]

    # ── Double-click / open ───────────────────────────────────────────────────
    def _on_double_click(self, event):
        iid  = self.tree.identify_row(event.y)
        path = self._iid_map.get(iid)
        print(f"[DBG] iid={iid!r}  path={path!r}  exists={os.path.exists(path) if path else 'N/A'}")
        if path and os.path.exists(path):
            subprocess.Popen(f'explorer /select,"{path}"', shell=True)

    def _open_folder(self):
        iid  = self.tree.focus()
        path = self._iid_map.get(iid)
        if path and os.path.exists(path):
            subprocess.Popen(f'explorer /select,"{path}"', shell=True)
        else:
            d = self.entry_dir.get().strip()
            if d and os.path.isdir(d):
                os.startfile(d)

    # ── Export ────────────────────────────────────────────────────────────────
    def _export_csv(self):
        if not self._filtered:
            messagebox.showinfo("Esporta", "Nessun dato da esportare.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("Tutti i file", "*.*")],
        )
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=[c[0] for c in COLUMNS])
            writer.writeheader()
            for rec in self._filtered:
                writer.writerow({c[0]: rec.get(c[0], "") for c in COLUMNS})
        messagebox.showinfo("Esporta", f"Salvato:\n{path}")


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
