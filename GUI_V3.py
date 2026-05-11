import os
import re
import csv
import mmap
import ctypes
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from datetime import datetime


def open_and_select(path: str):
    path = os.path.normpath(path)
    shell32 = ctypes.windll.shell32
    ole32   = ctypes.windll.ole32
    ole32.CoInitialize(None)
    try:
        pidl  = ctypes.c_void_p(None)
        sfgao = ctypes.c_ulong(0)
        hr = shell32.SHParseDisplayName(
            ctypes.c_wchar_p(path), None,
            ctypes.byref(pidl), 0, ctypes.byref(sfgao))
        if hr == 0:
            shell32.SHOpenFolderAndSelectItems(pidl, 0, None, 0)
            ole32.CoTaskMemFree(pidl)
        else:
            os.startfile(os.path.dirname(path))
    except Exception:
        os.startfile(os.path.dirname(path))
    finally:
        ole32.CoUninitialize()


try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Colors ────────────────────────────────────────────────────────────────────
BG     = "#1e1e2e"
BG2    = "#2a2a3e"
BG3    = "#313244"
FG     = "#cdd6f4"
FG2    = "#a6adc8"
ACCENT = "#89b4fa"
GREEN  = "#a6e3a1"
RED    = "#f38ba8"
YELLOW = "#f9e2af"
SEL    = "#45475a"
BORDER = "#45475a"

# ── ECU profiles for BIN analysis ─────────────────────────────────────────────
ECU_PROFILES = {
    # All Marelli families share the same ID block structure at different bases:
    #   base+0x25 = CAL id   base+0x4A = HW string   base+0x5A = SW type
    # 9DF (4 MB int_flash) → base 0x310000
    # 8F2/8F3/8DF (2 MB int_flash) → base 0x1D0000
    # 6F3 (2 MB ext_flash) → cal at 0x0800B5, HW at 0x0800D8
    "Marelli (universale)": {
        "zones": [
            {"name": "ID 4MB flash  (9DF / 9DF-hw002)", "start": 0x310000, "end": 0x310090},
            {"name": "ID 2MB flash  (8F2 / 8F3 / 8DF)", "start": 0x1D0000, "end": 0x1D0090},
            {"name": "ID ext flash  (6F3 / 6F2)",        "start": 0x0800B0, "end": 0x0800F0},
        ],
        "preset_zones": [
            ("9DF",     "310000", "310090"),
            ("8F2/8F3", "1D0000", "1D0090"),
            ("6F3 ext", "0800B0", "0800F0"),
        ],
    },
    "Marelli 9DF  (GEN9 Diesel)": {
        "zones": [
            {"name": "ID / Part Number",  "start": 0x310000, "end": 0x310090},
            {"name": "Build Info",         "start": 0x004000, "end": 0x004050},
            {"name": "Project / Codice",   "start": 0x023280, "end": 0x023310},
            {"name": "Version Code",       "start": 0x01FF00, "end": 0x01FF40},
        ],
        "preset_zones": [
            ("ID/PartNo",   "310000", "310090"),
            ("Build Info",  "004000", "004050"),
            ("Progetto",    "023280", "023310"),
            ("Ver Code",    "01FF00", "01FF40"),
        ],
    },
    "Generico": {"zones": [], "preset_zones": []},
}

# Pattern ordered most-specific first; value = internal field key
# CAL: matches 4F42P228, 4F42X125, 4I40A224, 2841X144 etc. (letter + 2-4 digits suffix)
# Optional leading digit handles binary artifact from 0x33 magic byte (e.g. "34F42P228")
_BIN_PATTERNS = [
    (r"^MJ\w{1,8}HW\w{1,6}$",                                                "hw"),
    (r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\s+\d{4}$","build_date"),
    (r"^Microp\.:",                                                            "mcu"),
    (r".*ECU\s+Software.*",                                                   "project"),
    (r"^MUST_",                                                               "internal"),
    (r"^\d{1,3}_[A-Z]\d_\d{2}$",                                             "revision"),
    (r"^\d?[A-Z0-9]{2,7}[A-Z]\d{2,4}$",                                      "cal"),
    (r"^\d{8,10}$",                                                           "fal_pn"),
    (r"^\w{2,6}\.\w{2,8}$",                                                  "sw"),
]

# Chars that appear as binary artifacts at end of extracted strings
_TRAIL_JUNK = re.compile(r"[^A-Za-z0-9._]+$")


def _str_zone(mm_obj, start, end, min_len=4):
    """Extract printable ASCII strings from a mmap slice.
    Trailing binary-artifact characters (e.g. stray quote from next byte) are stripped."""
    chunk, result, buf, off = mm_obj[start:end], [], [], start
    for i, b in enumerate(chunk):
        if 0x20 <= b <= 0x7E:
            if not buf:
                off = start + i
            buf.append(chr(b))
        else:
            if len(buf) >= min_len:
                s = _TRAIL_JUNK.sub("", "".join(buf)).strip()
                if s:
                    result.append((off, s))
            buf = []
    if len(buf) >= min_len:
        s = _TRAIL_JUNK.sub("", "".join(buf)).strip()
        if s:
            result.append((off, s))
    return result


def _classify_bin(s):
    for pat, label in _BIN_PATTERNS:
        if re.match(pat, s.strip(), re.IGNORECASE):
            return label
    return None


def _clean_cal(s: str) -> str:
    """Strip the stray leading digit caused by the 0x33 magic-byte artifact.
    E.g. '34F42P228' → '4F42P228', '34F42X125' → '4F42X125'."""
    if s and s[0].isdigit() and len(s) >= 5:
        candidate = s[1:]
        # Cal IDs are like 4F42P228, 4I40A224 — alphanumeric ending in letter+digits
        if re.match(r"^[A-Z0-9]{2,7}[A-Z]\d{2,4}$", candidate, re.I):
            return candidate
    return s


def parse_bin_file(path: str, profilo_nome: str) -> dict | None:
    """Scan only the known zones of a BIN file and return an ECU record dict."""
    zones = ECU_PROFILES.get(profilo_nome, {}).get("zones", [])
    if not zones:
        return None
    try:
        file_size = os.path.getsize(path)
        fields = {}
        with open(path, "rb") as fh:
            mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
            for zone in zones:
                zs = zone["start"]
                ze = min(zone["end"], file_size)
                if zs >= file_size:
                    continue
                for _, s in _str_zone(mm, zs, ze):
                    lbl = _classify_bin(s)
                    if lbl and lbl not in fields:
                        fields[lbl] = s
            mm.close()

        if not any(fields.get(k) for k in ("sw", "hw", "cal", "fal_pn")):
            return None

        mcu_raw = fields.get("mcu", "")
        mcu     = mcu_raw.replace("Microp.:", "").strip() if mcu_raw else ""

        # Strip the stray leading '3' caused by 0x33 magic byte in binary zone
        cal = _clean_cal(fields.get("cal", ""))

        rec = {k: "" for k in ("date", "operation", "vin", "hw", "sw", "sw2",
                                "cal", "fal_pn", "type", "mileage", "file", "path")}
        rec.update({
            "path":      path,
            "file":      os.path.basename(path),
            "date":      datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M"),
            "sw":        fields.get("sw", ""),
            "hw":        fields.get("hw", ""),
            "cal":       cal,
            "fal_pn":    fields.get("fal_pn", ""),
            "type":      mcu,
            "operation": fields.get("project", fields.get("internal", "")),
            "_source":   "bin",
        })
        return rec
    except Exception:
        return None


# ── History-file parsing ───────────────────────────────────────────────────────
FIELD_PATTERNS = {
    "hw":        re.compile(r"^HW:\s*([^\r\n]+)",                               re.I | re.M),
    # "SW number:" = Marelli 8DF/8F2; "SW version:" = PSA/Valeo secondary ID → sw2
    "sw":        re.compile(r"^SW(?:\s+number)?:\s*([^\r\n]+)",                 re.I | re.M),
    "sw2":       re.compile(r"^(?:SW2|SW\s+version):\s*([^\r\n]+)",             re.I | re.M),
    # "Calibrazione:" = Continental SID Italian; "Calibration:" = generic
    "cal":       re.compile(r"^(?:CAL|Calibrazione|Calibration):\s*([^\r\n]+)", re.I | re.M),
    "fal_pn":    re.compile(r"^FAL PN:\s*([^\r\n]+)",                           re.I | re.M),
    # "Modello:" = Continental SID Italian
    "type":      re.compile(r"^(?:Type|Modello):\s*([^\r\n]+)",                  re.I | re.M),
    "vin":       re.compile(r"^VIN:[^\S\r\n]*(\S+)",                             re.I | re.M),
    # "Odometro:" = Italian mileage field
    "mileage":   re.compile(r"(?:Total mileage|Odometro):\s*(\d+)",              re.I),
    "operation": re.compile(r"[-]{5,}\s+((?!Connect|Connessione)\w[\w\s/]+?)\s+(?:Started|Iniziata)", re.I),
}


def clean_field(s: str) -> str:
    return re.sub(r"[^\x20-\x7E -￿]", "", s).strip()


DATE_RE = re.compile(r"_(\d{14})[_.]")


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
    rec["path"]    = path
    rec["file"]    = os.path.basename(path)
    rec["_source"] = "txt"

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
                rec["operation"] = clean_field(ops[-1])
        else:
            fm = pat.search(text)
            if fm:
                rec[key] = clean_field(fm.group(1))

    # Discard records that contain no ECU identification data at all
    # (pure connection logs from Bosch/Audi/BMW tools without SW/HW fields)
    if not any(rec.get(k) for k in ("sw", "hw", "cal", "fal_pn", "type", "vin", "mileage")):
        return None

    return rec


# ── Column definitions ─────────────────────────────────────────────────────────
COLUMNS = [
    ("date",      "Data",        130, "center"),
    ("operation", "Operazione",  150, "w"),
    ("sw",        "SW",          105, "w"),
    ("sw2",       "SW2",          70, "w"),
    ("hw",        "HW",          115, "w"),
    ("cal",       "CAL",          70, "w"),
    ("fal_pn",    "FAL PN",       90, "w"),
    ("type",      "Type/MCU",     90, "w"),
    ("vin",       "VIN",         150, "w"),
    ("mileage",   "Km",           65, "e"),
    ("file",      "File",        280, "w"),
]


# ── App ───────────────────────────────────────────────────────────────────────
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("ECU Session Browser")
        self.root.state("zoomed")
        self.root.configure(bg=BG)

        self._records     : list[dict] = []
        self._filtered    : list[dict] = []
        self._iid_map     : dict[str, str] = {}
        self._sort_col    = "date"
        self._sort_rev    = False
        self._queue       = queue.Queue()
        self._stop_evt    = threading.Event()
        self._debounce_id = None

        self._scan_mode   = tk.StringVar(value="Solo history.txt")
        self._bin_profile = tk.StringVar(value="Marelli (universale)")

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
        s.configure("TFrame",    background=BG)
        s.configure("TLabel",    background=BG, foreground=FG)
        s.configure("TSeparator", background=BORDER)
        s.configure("TEntry",
                    fieldbackground=BG2, foreground=FG,
                    insertcolor=FG, borderwidth=1, relief="flat")
        s.configure("TCombobox",
                    fieldbackground=BG2, foreground=FG,
                    selectbackground=SEL, selectforeground=FG, arrowcolor=FG)
        s.map("TCombobox",
              fieldbackground=[("readonly", BG2)],
              selectbackground=[("readonly", BG2)])
        s.configure("TButton",
                    background=BG3, foreground=FG,
                    relief="flat", padding=(10, 5), borderwidth=0)
        s.map("TButton",
              background=[("active", SEL), ("disabled", BG2)],
              foreground=[("disabled", FG2)])
        s.configure("Accent.TButton",
                    background=ACCENT, foreground=BG,
                    font=("Helvetica", 11, "bold"), padding=(12, 6))
        s.map("Accent.TButton", background=[("active", "#74aee8")])
        s.configure("TProgressbar",
                    troughcolor=BG2, background=ACCENT, borderwidth=0, thickness=20)
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
        s.configure("TNotebook",
                    background=BG, tabmargins=[2, 5, 2, 0])
        s.configure("TNotebook.Tab",
                    background=BG3, foreground=FG2,
                    padding=[12, 5], font=("Helvetica", 10))
        s.map("TNotebook.Tab",
              background=[("selected", BG), ("active", SEL)],
              foreground=[("selected", ACCENT)])

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        nb = ttk.Notebook(self.root)
        nb.pack(fill=tk.BOTH, expand=True)

        tab1 = ttk.Frame(nb)
        nb.add(tab1, text="  Sessioni ECU  ")

        tab2 = ttk.Frame(nb)
        nb.add(tab2, text="  Analisi BIN  ")

        self._build_tab_sessioni(tab1)
        self._build_tab_bin(tab2)

    # ══════════════════════════════════════════════════════════════════════════
    # TAB 1 — Sessioni ECU
    # ══════════════════════════════════════════════════════════════════════════
    def _build_tab_sessioni(self, parent):
        # Header
        hdr = ttk.Frame(parent, padding=(14, 10, 14, 6))
        hdr.pack(fill=tk.X)
        ttk.Label(hdr, text="ECU Session Browser",
                  font=("Helvetica", 17, "bold"), foreground=ACCENT).pack(side=tk.LEFT)

        # Directory row
        dir_row = ttk.Frame(parent, padding=(14, 4, 14, 2))
        dir_row.pack(fill=tk.X)
        dir_row.columnconfigure(1, weight=1)
        ttk.Label(dir_row, text="Directory:", width=10).grid(row=0, column=0, sticky="e", padx=(0, 6))
        self.entry_dir = ttk.Entry(dir_row)
        self.entry_dir.grid(row=0, column=1, sticky="ew")
        ttk.Button(dir_row, text="Sfoglia", command=self._browse,
                   width=9).grid(row=0, column=2, padx=(6, 0))

        # Controls row
        ctrl = ttk.Frame(parent, padding=(14, 6, 14, 6))
        ctrl.pack(fill=tk.X)

        self.btn_scan = ttk.Button(ctrl, text="⟳  Scansiona",
                                   style="Accent.TButton", command=self._start_scan)
        self.btn_scan.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_stop = ttk.Button(ctrl, text="◼  Stop",
                                   command=self._stop_scan, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 10))

        ttk.Separator(ctrl, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))

        # Scan mode selector
        ttk.Label(ctrl, text="Modalità:").pack(side=tk.LEFT, padx=(0, 4))
        self._mode_cb = ttk.Combobox(
            ctrl, textvariable=self._scan_mode,
            values=["Solo history.txt", "Solo .bin", "Tutti (txt + bin)"],
            state="readonly", width=18)
        self._mode_cb.pack(side=tk.LEFT, padx=(0, 6))

        # BIN profile (shown/hidden by mode)
        self._lbl_profile = ttk.Label(ctrl, text="Profilo ECU:")
        self._lbl_profile.pack(side=tk.LEFT, padx=(4, 4))
        self._profile_cb = ttk.Combobox(
            ctrl, textvariable=self._bin_profile,
            values=list(ECU_PROFILES.keys()),
            state="readonly", width=26)
        self._profile_cb.pack(side=tk.LEFT, padx=(0, 10))

        self._scan_mode.trace_add("write", self._on_mode_change)
        self._on_mode_change()   # set initial visibility

        ttk.Separator(ctrl, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))

        # Search box
        ttk.Label(ctrl, text="Cerca:").pack(side=tk.LEFT, padx=(0, 4))
        self.entry_search = ttk.Entry(ctrl, width=28)
        self.entry_search.pack(side=tk.LEFT, padx=(0, 6))
        self.entry_search.bind("<KeyRelease>", self._on_search_key)

        ttk.Label(ctrl, text="in:").pack(side=tk.LEFT, padx=(0, 4))
        self.filter_col = tk.StringVar(value="Tutti")
        filter_labels   = ["Tutti"] + [c[1] for c in COLUMNS if c[0] != "file"]
        ttk.Combobox(ctrl, textvariable=self.filter_col, values=filter_labels,
                     state="readonly", width=12).pack(side=tk.LEFT, padx=(0, 10))
        self.filter_col.trace_add("write", lambda *_: self._apply_filter())

        ttk.Separator(ctrl, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        ttk.Button(ctrl, text="Pulisci",       command=self._clear).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(ctrl, text="Esporta CSV",   command=self._export_csv).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(ctrl, text="Apri cartella", command=self._open_folder).pack(side=tk.LEFT)

        # Treeview
        tree_frame = ttk.Frame(parent, padding=(14, 0, 14, 0))
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

        self.tree.tag_configure("odd",     background="#252535")
        self.tree.tag_configure("even",    background=BG2)
        self.tree.tag_configure("bin_odd",  background="#1a2a1a")
        self.tree.tag_configure("bin_even", background="#1e2e1e")

        # Progress bar
        prog = ttk.Frame(parent, padding=(14, 3, 14, 3))
        prog.pack(fill=tk.X)
        self.pct_label = ttk.Label(prog, text="", width=5, anchor="e",
                                   foreground=ACCENT, font=("Helvetica", 9, "bold"))
        self.pct_label.pack(side=tk.RIGHT)
        self.progress_bar = ttk.Progressbar(prog, orient="horizontal", mode="determinate")
        self.progress_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        # Status bar
        self.status_var = tk.StringVar(value="Pronto — seleziona una directory e premi Scansiona.")
        ttk.Label(parent, textvariable=self.status_var,
                  style="Status.TLabel").pack(fill=tk.X)

    def _on_mode_change(self, *_):
        mode = self._scan_mode.get()
        show_profile = mode != "Solo history.txt"
        state = "normal" if show_profile else "disabled"
        self._lbl_profile.configure(foreground=ACCENT if show_profile else FG2)
        self._profile_cb.configure(state="readonly" if show_profile else "disabled")

    # ══════════════════════════════════════════════════════════════════════════
    # TAB 2 — Analisi BIN (singolo file, dettagliata)
    # ══════════════════════════════════════════════════════════════════════════
    def _build_tab_bin(self, parent):
        # ── File + profilo ────────────────────────────────────────────────────
        fr_file = ttk.LabelFrame(parent, text="  File BIN  ", padding=(10, 6))
        fr_file.pack(fill=tk.X, padx=14, pady=8)

        ttk.Label(fr_file, text="File:").grid(row=0, column=0, sticky="e", padx=(0, 8), pady=4)
        self._bin_entry = ttk.Entry(fr_file)
        self._bin_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=4)
        ttk.Button(fr_file, text="Sfoglia", command=self._browse_bin,
                   width=9).grid(row=0, column=2, pady=4)

        ttk.Label(fr_file, text="Profilo ECU:").grid(row=1, column=0, sticky="e", padx=(0, 8), pady=4)
        self._bin_prof2 = tk.StringVar(value=list(ECU_PROFILES.keys())[0])
        ttk.Combobox(fr_file, textvariable=self._bin_prof2,
                     values=list(ECU_PROFILES.keys()),
                     state="readonly", width=38).grid(row=1, column=1, sticky="w", pady=4)

        ttk.Button(fr_file, text="Analizza SW", style="Accent.TButton",
                   command=self._analizza_bin).grid(row=1, column=2, pady=4)

        fr_file.columnconfigure(1, weight=1)

        # ── Ricerca manuale ───────────────────────────────────────────────────
        fr_man = ttk.LabelFrame(parent, text="  Ricerca manuale  (offset hex + pattern)  ", padding=(10, 6))
        fr_man.pack(fill=tk.X, padx=14, pady=4)

        ttk.Label(fr_man, text="Start (hex):").grid(row=0, column=0, sticky="e", padx=(0, 4), pady=3)
        self._off_s = ttk.Entry(fr_man, width=10, font=("Courier", 11))
        self._off_s.grid(row=0, column=1, padx=(0, 10), pady=3)
        self._off_s.insert(0, "000000")

        ttk.Label(fr_man, text="End (hex):").grid(row=0, column=2, sticky="e", padx=(0, 4))
        self._off_e = ttk.Entry(fr_man, width=10, font=("Courier", 11))
        self._off_e.grid(row=0, column=3, padx=(0, 14), pady=3)
        self._off_e.insert(0, "3FFFFF")

        # Preset zone buttons (update when profile changes)
        self._fr_presets = ttk.Frame(fr_man)
        self._fr_presets.grid(row=0, column=4, padx=4)
        self._bin_prof2.trace_add("write", self._update_presets)
        self._update_presets()

        ttk.Label(fr_man, text="Pattern:").grid(row=1, column=0, sticky="e", padx=(0, 4), pady=3)
        self._pattern_e = ttk.Entry(fr_man, width=36, font=("Courier", 11))
        self._pattern_e.grid(row=1, column=1, columnspan=2, sticky="w", pady=3)

        self._tipo_var = tk.StringVar(value="ASCII")
        ttk.Combobox(fr_man, textvariable=self._tipo_var,
                     values=["ASCII", "HEX"],
                     state="readonly", width=7).grid(row=1, column=3, padx=(0, 14))

        ttk.Button(fr_man, text="Cerca", command=self._cerca_bin_man,
                   width=10).grid(row=1, column=4, padx=4)

        # ── Log BIN ───────────────────────────────────────────────────────────
        fr_log = ttk.Frame(parent, padding=(14, 4, 14, 4))
        fr_log.pack(fill=tk.BOTH, expand=True)
        fr_log.columnconfigure(0, weight=1)
        fr_log.rowconfigure(0, weight=1)

        self._log_bin = tk.Text(
            fr_log, font=("Courier", 11), wrap=tk.NONE,
            bg="#1E1E1E", fg="#D4D4D4", insertbackground="white",
            relief="flat", borderwidth=0)
        self._log_bin.grid(row=0, column=0, sticky="nsew")
        self._log_bin.tag_config("match",    foreground="#4FC3F7", font=("Courier", 11, "bold"))
        self._log_bin.tag_config("header",   foreground=YELLOW,    font=("Courier", 11, "bold"))
        self._log_bin.tag_config("riepilog", foreground=GREEN,      font=("Courier", 11, "bold"))

        vsb2 = ttk.Scrollbar(fr_log, orient="vertical",   command=self._log_bin.yview)
        hsb2 = ttk.Scrollbar(fr_log, orient="horizontal", command=self._log_bin.xview)
        self._log_bin.configure(yscrollcommand=vsb2.set, xscrollcommand=hsb2.set)
        vsb2.grid(row=0, column=1, sticky="ns")
        hsb2.grid(row=1, column=0, sticky="ew")

        # Progress + footer
        prog2 = ttk.Frame(parent, padding=(14, 2, 14, 2))
        prog2.pack(fill=tk.X)
        self._pct2 = ttk.Label(prog2, text="", width=5, anchor="e",
                                foreground=ACCENT, font=("Helvetica", 9, "bold"))
        self._pct2.pack(side=tk.RIGHT)
        self._pb2 = ttk.Progressbar(prog2, orient="horizontal", mode="determinate")
        self._pb2.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        fr_foot = ttk.Frame(parent, padding=(14, 4, 14, 6))
        fr_foot.pack(fill=tk.X)
        ttk.Button(fr_foot, text="Pulisci Log", command=self._clear_bin_log).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(fr_foot, text="Salva risultati", command=self._save_bin_log).pack(side=tk.LEFT)

    def _update_presets(self, *_):
        for w in self._fr_presets.winfo_children():
            w.destroy()
        presets = ECU_PROFILES.get(self._bin_prof2.get(), {}).get("preset_zones", [])
        if not presets:
            return
        ttk.Label(self._fr_presets, text="Zona rapida:").pack(side=tk.LEFT, padx=(0, 6))
        for nome, s, e in presets:
            def _click(s=s, e=e):
                self._off_s.delete(0, tk.END); self._off_s.insert(0, s)
                self._off_e.delete(0, tk.END); self._off_e.insert(0, e)
            tk.Button(self._fr_presets, text=nome, command=_click,
                      bg="#546E7A", fg="white", font=("Helvetica", 9),
                      relief="flat", padx=6, pady=2).pack(side=tk.LEFT, padx=2)

    # ── BIN tab actions ───────────────────────────────────────────────────────
    def _browse_bin(self):
        f = filedialog.askopenfilename(
            filetypes=[("BIN files", "*.bin"), ("Tutti i file", "*.*")])
        if f:
            self._bin_entry.delete(0, tk.END)
            self._bin_entry.insert(0, f)

    def _bin_log(self, msg, tag=None):
        if tag:
            self._log_bin.insert(tk.END, msg, tag)
        else:
            self._log_bin.insert(tk.END, msg)
        self._log_bin.yview(tk.END)

    def _analizza_bin(self):
        path = self._bin_entry.get().strip()
        if not path or not os.path.exists(path):
            messagebox.showwarning("File mancante", "Seleziona un file BIN valido.")
            return
        self._log_bin.delete(1.0, tk.END)
        profilo_nome = self._bin_prof2.get()
        zones = ECU_PROFILES.get(profilo_nome, {}).get("zones", [])

        self._bin_log(f"File:    {os.path.basename(path)}\n")
        self._bin_log(f"Size:    {os.path.getsize(path):,} byte\n")
        self._bin_log(f"Profilo: {profilo_nome}\n\n")

        if not zones:
            self._bin_log("Nessuna zona definita. Usa la ricerca manuale.\n")
            return

        try:
            file_size = os.path.getsize(path)
            riepilogo = []

            with open(path, "rb") as fh:
                mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)

                for idx, zone in enumerate(zones):
                    self._pb2["maximum"] = len(zones)
                    self._pb2["value"]   = idx + 1
                    self._pct2.config(text=f"{int((idx+1)/len(zones)*100)}%")
                    self.root.update_idletasks()

                    zs = zone["start"]
                    ze = min(zone["end"], file_size)
                    if zs >= file_size:
                        self._bin_log(f"[WARN] Zona '{zone['name']}' fuori dal file\n\n")
                        continue

                    self._bin_log(f"{'━'*62}\n", "header")
                    self._bin_log(f"  {zone['name']}  [0x{zs:06X} – 0x{ze:06X}]\n", "header")
                    self._bin_log(f"{'━'*62}\n", "header")

                    for off, s in _str_zone(mm, zs, ze):
                        campo = _classify_bin(s)
                        if campo:
                            self._bin_log(f"  [0x{off:06X}]  {campo:<22}  {s}\n", "match")
                            riepilogo.append((campo, s, off))
                        else:
                            self._bin_log(f"  [0x{off:06X}]  {'':22}  {s}\n")
                    self._bin_log("\n")

                mm.close()

            self._bin_log(f"{'═'*62}\n", "header")
            self._bin_log("  RIEPILOGO\n", "header")
            self._bin_log(f"{'═'*62}\n", "header")
            for campo, valore, _ in riepilogo:
                self._bin_log(f"  {campo:<25}  {valore}\n", "riepilog")
            self._bin_log("\nAnalisi completata.\n")

        except Exception as exc:
            self._bin_log(f"\nErrore: {exc}\n")

    def _cerca_bin_man(self):
        path = self._bin_entry.get().strip()
        if not path or not os.path.exists(path):
            messagebox.showwarning("File mancante", "Seleziona prima un file BIN.")
            return
        pattern_str = self._pattern_e.get()
        if not pattern_str.strip():
            messagebox.showwarning("Pattern vuoto", "Inserisci un pattern da cercare.")
            return

        self._log_bin.delete(1.0, tk.END)
        tipo = self._tipo_var.get()

        try:
            file_size = os.path.getsize(path)
            try:
                start = int(self._off_s.get().strip() or "0",      16)
                end   = int(self._off_e.get().strip() or "3FFFFF", 16)
            except ValueError:
                self._bin_log("Errore: offset non valido (usa hex, es: 310000)\n")
                return
            end = min(end, file_size)

            if tipo == "HEX":
                try:
                    pattern = bytes.fromhex(pattern_str.replace(" ", "").replace("0x", ""))
                except ValueError:
                    self._bin_log("Errore: HEX non valido (es: C1 0A 00)\n")
                    return
            else:
                pattern = pattern_str.encode("ascii", errors="replace")

            self._bin_log(f"Tipo:    {tipo}\n")
            self._bin_log(f"Pattern: {pattern_str}\n")
            self._bin_log(f"Range:   0x{start:06X} – 0x{end:06X}  ({end-start:,} byte)\n\n")

            trovati = []
            with open(path, "rb") as fh:
                mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
                pos = start
                while pos < end:
                    found = mm.find(pattern, pos, end)
                    if found == -1:
                        break
                    trovati.append(found)
                    ctx_s = max(0, found - 8)
                    ctx_e = min(file_size, found + len(pattern) + 8)
                    ctx   = mm[ctx_s:ctx_e]
                    self._bin_log(f"  TROVATO @ 0x{found:06X}\n", "match")
                    self._bin_log(f"  HEX: {' '.join(f'{b:02X}' for b in ctx)}\n")
                    self._bin_log(f"  ASC: {''.join(chr(b) if 0x20<=b<=0x7E else '.' for b in ctx)}\n\n")
                    pos = found + 1
                mm.close()

            self._pb2["value"] = self._pb2["maximum"] = 1
            self._pct2.config(text="100%")
            msg = f"Trovate {len(trovati)} occorrenze.\n" if trovati else "Nessun risultato.\n"
            self._bin_log(msg, "riepilog")

        except Exception as exc:
            self._bin_log(f"Errore: {exc}\n")

    def _clear_bin_log(self):
        self._log_bin.delete(1.0, tk.END)
        self._pb2["value"] = 0
        self._pct2.config(text="")

    def _save_bin_log(self):
        txt = self._log_bin.get(1.0, tk.END)
        if not txt.strip():
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text file", "*.txt"), ("Tutti i file", "*.*")])
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(txt)

    # ── Tab 1 actions ─────────────────────────────────────────────────────────
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
        self.btn_stop.config(text="Arresto...", state=tk.DISABLED)

    def _scan_worker(self, directory: str):
        mode    = self._scan_mode.get()
        profilo = self._bin_profile.get()

        files_txt, files_bin = [], []
        for r, _, names in os.walk(directory):
            for n in names:
                nl = n.lower()
                if nl.endswith("history.txt") and mode != "Solo .bin":
                    files_txt.append(os.path.join(r, n))
                elif nl.endswith(".bin") and mode != "Solo history.txt":
                    files_bin.append(os.path.join(r, n))

        all_files = files_txt + files_bin
        total = len(all_files)
        self._queue.put(("status",
            f"Trovati {len(files_txt)} history.txt + {len(files_bin)} .bin da analizzare..."))

        for i, path in enumerate(all_files, 1):
            if self._stop_evt.is_set():
                break
            self._queue.put(("progress", i, total))

            rec = parse_bin_file(path, profilo) if path.lower().endswith(".bin") \
                  else parse_history_file(path)

            if rec:
                self._queue.put(("record", rec))

        self._queue.put(("done", total))

    def _scan_done(self, total: int):
        self.btn_scan.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED, text="◼  Stop")
        self._apply_filter(silent=True)
        n  = len(self._records)
        nf = len(self._filtered)
        self.status_var.set(
            f"Scansione completata — {n} record trovati su {total} file.")
        if total > 0:
            self.progress_bar["maximum"] = total
            self.progress_bar["value"]   = total
        self.pct_label.config(text="100%" if total > 0 else "")
        self.root.after(100, lambda: self._show_done_popup(total, n, nf))

    def _show_done_popup(self, total, n, nf):
        self.root.lift()
        self.root.focus_force()
        if total == 0:
            messagebox.showinfo("Nessun file", "Nessun file trovato.", parent=self.root)
        elif n == 0:
            messagebox.showinfo("Nessun record",
                f"Trovati {total} file ma nessuno contiene dati ECU validi.", parent=self.root)
        elif nf == 0:
            q = self.entry_search.get().strip()
            messagebox.showinfo("Nessun risultato",
                f"Trovate {n} sessioni ma nessuna corrisponde a '{q}'.", parent=self.root)

    def _poll_queue(self):
        try:
            for _ in range(20):
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                tag = item[0]
                if tag == "status":
                    self.status_var.set(item[1])
                elif tag == "progress":
                    _, val, total = item
                    self.progress_bar["maximum"] = max(total, 1)
                    self.progress_bar["value"]   = val
                    self.pct_label.config(text=f"{int(val/max(total,1)*100)}%")
                elif tag == "record":
                    self._records.append(item[1])
                elif tag == "done":
                    self._scan_done(item[1])
                    return
        except Exception as e:
            self.btn_scan.config(state=tk.NORMAL)
            self.btn_stop.config(state=tk.DISABLED)
            self.status_var.set(f"Errore: {e}")
            return
        self.root.after(100, self._poll_queue)

    # ── Filter & sort ─────────────────────────────────────────────────────────
    def _on_search_key(self, _):
        self._apply_filter()
        if self._debounce_id:
            self.root.after_cancel(self._debounce_id)
            self._debounce_id = None
        query = self.entry_search.get().strip()
        if query and self._records:
            self._debounce_id = self.root.after(700, self._notify_if_empty, query)

    def _notify_if_empty(self, query):
        self._debounce_id = None
        if (self.entry_search.get().strip().lower() == query.lower()
                and len(self._filtered) == 0):
            lbl = self.filter_col.get()
            campo = f" nel campo '{lbl}'" if lbl != "Tutti" else ""
            messagebox.showinfo("Nessun risultato",
                                f"Nessuna sessione trovata per '{query}'{campo}.")

    def _apply_filter(self, silent=False):
        query     = clean_field(self.entry_search.get()).lower()
        col_label = self.filter_col.get()
        col_id    = None
        if col_label != "Tutti":
            for cid, label, *_ in COLUMNS:
                if label == col_label:
                    col_id = cid
                    break

        if not query:
            self._filtered = list(self._records)
        elif col_id:
            self._filtered = [r for r in self._records
                              if query in r.get(col_id, "").lower()]
        else:
            self._filtered = [r for r in self._records
                              if any(query in str(v).lower() for v in r.values())]

        self._refresh_tree()
        if not silent:
            self.status_var.set(
                f"{len(self._filtered)} risultati  (totale: {len(self._records)})")

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
            is_bin = rec.get("_source") == "bin"
            tag  = ("bin_odd" if i % 2 else "bin_even") if is_bin \
                   else ("odd"     if i % 2 else "even")
            self.tree.insert("", tk.END, values=vals, iid=iid, tags=(tag,))
            self._iid_map[iid] = rec["path"]

    # ── Double-click / open ───────────────────────────────────────────────────
    def _on_double_click(self, event):
        iid  = self.tree.identify_row(event.y)
        path = self._iid_map.get(iid)
        if path and os.path.exists(path):
            open_and_select(path)

    def _open_folder(self):
        iid  = self.tree.focus()
        path = self._iid_map.get(iid)
        if path and os.path.exists(path):
            open_and_select(path)
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
            filetypes=[("CSV", "*.csv"), ("Tutti i file", "*.*")])
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
