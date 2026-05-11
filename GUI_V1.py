import os
import mmap
import re
import tkinter as tk
from tkinter import scrolledtext, messagebox, ttk, filedialog
import rarfile
from PIL import Image, ImageTk

# ─── Profili ECU ──────────────────────────────────────────────────────────────
# Ogni profilo definisce le zone da scansionare (start/end in byte)
# e i preset mostrati come bottoni rapidi nella ricerca manuale.

ECU_PROFILES = {
    "Marelli 9DF  (GEN9 Diesel)": {
        "zones": [
            {"name": "ID / Part Number",   "start": 0x310000, "end": 0x310080},
            {"name": "Build Info",          "start": 0x004000, "end": 0x004050},
            {"name": "Project / Codice",    "start": 0x023280, "end": 0x023310},
            {"name": "Version Code",        "start": 0x01FF00, "end": 0x01FF40},
        ],
        "preset_zones": [
            ("ID/PartNo",    "310000", "310080"),
            ("Build Info",   "004000", "004050"),
            ("Progetto",     "023280", "023310"),
            ("Ver Code",     "01FF00", "01FF40"),
        ],
    },
    "Generico (zona custom)": {
        "zones": [],
        "preset_zones": [],
    },
}

# ─── Utility ──────────────────────────────────────────────────────────────────

def configura_unrar():
    rarfile.UNRAR_TOOL = r"C:\\Program Files\\WinRAR\\UnRAR.exe"
    if not os.path.exists(rarfile.UNRAR_TOOL):
        raise FileNotFoundError(
            f"Strumento UnRAR non trovato: {rarfile.UNRAR_TOOL}"
        )

def aggiorna_progress_bar(progress_bar, progress_label, valore, massimo):
    pct = int((valore / max(massimo, 1)) * 100)
    progress_bar["value"]   = valore
    progress_bar["maximum"] = max(massimo, 1)
    progress_label.config(text=f"{pct}%")
    root.update_idletasks()

# ─── Tab 1 — Cerca stringa ASCII ──────────────────────────────────────────────

def trova_stringa_ascii(percorso_directory, stringa_ascii, log_text,
                        filtro_var, progress_bar, progress_label):
    log_text.delete(1.0, tk.END)
    log_text.insert(tk.END,
        f"Cercando '{stringa_ascii}' [{filtro_var.get()}] in '{percorso_directory}'...\n")
    log_text.yview(tk.END)

    stringa_lower = stringa_ascii.lower()
    risultati     = []
    percorso_directory = os.path.abspath(percorso_directory)

    if not os.path.exists(percorso_directory):
        log_text.insert(tk.END, f"Errore: directory '{percorso_directory}' non esiste.\n")
        return

    files_da_esaminare = []
    for dirpath, dirs, files in os.walk(percorso_directory):
        for nome_file in files:
            nl = nome_file.lower()
            if filtro_var.get() == "Tutti i file di testo" and nl.endswith(".txt"):
                files_da_esaminare.append(os.path.join(dirpath, nome_file))
            elif filtro_var.get() == "Solo history.txt" and nl.endswith("_history.txt"):
                files_da_esaminare.append(os.path.join(dirpath, nome_file))
            elif filtro_var.get() == "Solo file .rar" and nl.endswith(".rar"):
                files_da_esaminare.append(os.path.join(dirpath, nome_file))

    totale = len(files_da_esaminare)
    if totale == 0:
        log_text.insert(tk.END, f"Nessun file trovato con il filtro '{filtro_var.get()}'\n")
        return

    log_text.insert(tk.END, f"Totale file: {totale}\n")

    for indice, percorso_file in enumerate(files_da_esaminare, start=1):
        aggiorna_progress_bar(progress_bar, progress_label, indice, totale)
        log_text.insert(tk.END, f"Esaminando: {percorso_file}\n")
        log_text.yview(tk.END)

        try:
            if filtro_var.get() in ["Tutti i file di testo", "Solo history.txt"]:
                with open(percorso_file, "rb") as f:
                    if stringa_lower.encode("ascii") in f.read().lower():
                        risultati.append(percorso_file)
            elif filtro_var.get() == "Solo file .rar":
                with rarfile.RarFile(percorso_file) as rf:
                    for fi in rf.infolist():
                        if not fi.is_dir():
                            try:
                                if stringa_lower in rf.read(fi).decode("utf-8").lower():
                                    risultati.append(f"{percorso_file} -> {fi.filename}")
                            except UnicodeDecodeError:
                                pass
        except Exception as exc:
            log_text.insert(tk.END, f"Errore: {exc}\n")

    log_text.insert(tk.END, "\nRicerca completata.\n")
    if risultati:
        log_text.insert(tk.END, "\nTrovato in:\n")
        for r in risultati:
            log_text.insert(tk.END, f"  {r}\n")
        try:
            with open("log_risultati_ascii.txt", "w") as fout:
                fout.writelines(r + "\n" for r in risultati)
            log_text.insert(tk.END, "\nSalvato in 'log_risultati_ascii.txt'.\n")
        except Exception as exc:
            log_text.insert(tk.END, f"Errore salvataggio: {exc}\n")
    else:
        log_text.insert(tk.END, "\nStringa non trovata.\n")

    log_text.yview(tk.END)

# ─── Tab 2 — Analisi BIN ──────────────────────────────────────────────────────

def _estrai_stringhe_zona(mm_obj, start, end, min_len=4):
    """Estrae tutte le stringhe ASCII leggibili da una zona del file mappato."""
    chunk  = mm_obj[start:end]
    result = []
    buf    = []
    off    = start

    for i, b in enumerate(chunk):
        if 0x20 <= b <= 0x7E:
            if not buf:
                off = start + i
            buf.append(chr(b))
        else:
            if len(buf) >= min_len:
                s = "".join(buf).strip()
                if s:
                    result.append((off, s))
            buf = []

    if len(buf) >= min_len:
        result.append((off, "".join(buf).strip()))

    return result


# Pattern ordinati dal più specifico al più generico
_FIELD_PATTERNS = [
    (r"^MJ\w{1,8}HW\w{1,6}$",                                           "HW Part Number"),
    (r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\s+\d{4}$", "Build Date"),
    (r"^Microp\.:",                                                       "MCU"),
    (r".*ECU\s+Software.*",                                              "Project"),
    (r"^MUST_",                                                          "Internal Code"),
    (r"^\d{1,3}_[A-Z]\d_\d{2}$",                                        "Revision"),
    (r"^[A-Z0-9]{3,8}X\d{2,3}[A-Z]?$",                                  "Calibration ID"),
    (r"^\d{8,10}$",                                                      "Part Number"),
    (r"^\w{2,6}\.\w{2,8}$",                                             "SW Version"),
]

def _classifica(stringa):
    s = stringa.strip()
    for pat, label in _FIELD_PATTERNS:
        if re.match(pat, s, re.IGNORECASE):
            return label
    return None


def analizza_bin(percorso_file, profilo_nome, log_text, progress_bar, progress_label):
    """Scansiona le zone note del profilo ECU ed estrae i campi identificativi."""
    log_text.delete(1.0, tk.END)
    log_text.tag_config("match",     foreground="#4FC3F7", font=("Courier", 11, "bold"))
    log_text.tag_config("riepilogo", foreground="#A5D6A7", font=("Courier", 11, "bold"))
    log_text.tag_config("header",    foreground="#FFD54F", font=("Courier", 11, "bold"))

    log_text.insert(tk.END, f"File:    {os.path.basename(percorso_file)}\n")
    log_text.insert(tk.END, f"Size:    {os.path.getsize(percorso_file):,} byte\n")
    log_text.insert(tk.END, f"Profilo: {profilo_nome}\n\n")

    profilo = ECU_PROFILES.get(profilo_nome, {})
    zones   = profilo.get("zones", [])

    if not zones:
        log_text.insert(tk.END,
            "Nessuna zona definita per questo profilo.\n"
            "Usa la sezione 'Ricerca manuale' per specificare start/end offset.\n")
        return

    try:
        file_size = os.path.getsize(percorso_file)
        riepilogo = []

        with open(percorso_file, "rb") as fh:
            mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)

            for idx, zone in enumerate(zones):
                aggiorna_progress_bar(progress_bar, progress_label, idx + 1, len(zones))
                z_start = zone["start"]
                z_end   = min(zone["end"], file_size)

                if z_start >= file_size:
                    log_text.insert(tk.END,
                        f"[WARN] Zona '{zone['name']}' fuori dal file (0x{z_start:X})\n\n")
                    continue

                log_text.insert(tk.END, f"{'━'*62}\n", "header")
                log_text.insert(tk.END,
                    f"  {zone['name']}  [0x{z_start:06X} – 0x{z_end:06X}]\n", "header")
                log_text.insert(tk.END, f"{'━'*62}\n", "header")

                for off, s in _estrai_stringhe_zona(mm, z_start, z_end):
                    campo = _classifica(s)
                    if campo:
                        log_text.insert(tk.END,
                            f"  [0x{off:06X}]  {campo:<22}  {s}\n", "match")
                        riepilogo.append((campo, s, off))
                    else:
                        log_text.insert(tk.END,
                            f"  [0x{off:06X}]  {'':22}  {s}\n")

                log_text.insert(tk.END, "\n")

            mm.close()

        # Riepilogo finale
        log_text.insert(tk.END, f"{'═'*62}\n", "header")
        log_text.insert(tk.END, "  RIEPILOGO\n", "header")
        log_text.insert(tk.END, f"{'═'*62}\n", "header")
        for campo, valore, off in riepilogo:
            log_text.insert(tk.END, f"  {campo:<25}  {valore}\n", "riepilogo")

        log_text.insert(tk.END, "\nAnalisi completata.\n")

    except Exception as exc:
        log_text.insert(tk.END, f"\nErrore: {exc}\n")

    log_text.yview(tk.END)


def cerca_bin_manuale(percorso_file, pattern_str, off_start_str, off_end_str,
                      tipo, log_text, progress_bar, progress_label):
    """Cerca una stringa ASCII o pattern HEX in un intervallo di offset del file BIN."""
    log_text.delete(1.0, tk.END)
    log_text.tag_config("match",     foreground="#4FC3F7", font=("Courier", 11, "bold"))
    log_text.tag_config("riepilogo", foreground="#A5D6A7", font=("Courier", 11, "bold"))

    if not percorso_file or not os.path.exists(percorso_file):
        log_text.insert(tk.END, "Errore: seleziona prima un file BIN valido.\n")
        return

    if not pattern_str.strip():
        log_text.insert(tk.END, "Errore: inserisci un pattern da cercare.\n")
        return

    try:
        file_size = os.path.getsize(percorso_file)

        try:
            start = int(off_start_str.strip() or "0",        16)
            end   = int(off_end_str.strip()   or "3FFFFF",   16)
        except ValueError:
            log_text.insert(tk.END, "Errore: offset non valido. Usa formato hex (es: 310000)\n")
            return

        end = min(end, file_size)

        if tipo == "HEX":
            try:
                pattern = bytes.fromhex(pattern_str.replace(" ", "").replace("0x", ""))
            except ValueError:
                log_text.insert(tk.END, "Errore: pattern HEX non valido (es: C1 0A 00)\n")
                return
        else:
            pattern = pattern_str.encode("ascii", errors="replace")

        log_text.insert(tk.END,
            f"Tipo:    {tipo}\n"
            f"Pattern: {pattern_str}\n"
            f"Range:   0x{start:06X} – 0x{end:06X}  ({end - start:,} byte)\n\n")

        trovati = []

        with open(percorso_file, "rb") as fh:
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
                hex_s = " ".join(f"{b:02X}" for b in ctx)
                asc_s = "".join(chr(b) if 0x20 <= b <= 0x7E else "." for b in ctx)

                log_text.insert(tk.END, f"  TROVATO @ 0x{found:06X}\n", "match")
                log_text.insert(tk.END, f"  HEX: {hex_s}\n")
                log_text.insert(tk.END, f"  ASC: {asc_s}\n\n")

                pos = found + 1

            mm.close()

        aggiorna_progress_bar(progress_bar, progress_label, 1, 1)

        if trovati:
            log_text.insert(tk.END,
                f"Trovate {len(trovati)} occorrenze.\n", "riepilogo")
        else:
            log_text.insert(tk.END, "Nessun risultato trovato.\n")

    except Exception as exc:
        log_text.insert(tk.END, f"Errore: {exc}\n")

    log_text.yview(tk.END)

# ─── GUI ──────────────────────────────────────────────────────────────────────

def avvia_app():
    global root
    root = tk.Tk()
    root.title("ECU File Analyzer")

    try:
        logo = Image.open(r"D:\Download\Test Script\logo.ico")
        root.iconphoto(True, ImageTk.PhotoImage(logo))
    except Exception:
        pass

    root.state("zoomed")

    tk.Label(root, text="ECU File Analyzer",
             font=("Helvetica", 18, "bold")).pack(pady=8)

    notebook = ttk.Notebook(root)
    notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

    # ══════════════════════════════════════════════════════════════════════════
    # TAB 1 — Cerca stringa ASCII
    # ══════════════════════════════════════════════════════════════════════════
    tab_ascii = tk.Frame(notebook)
    notebook.add(tab_ascii, text="  Cerca ASCII  ")

    fr_in = tk.Frame(tab_ascii)
    fr_in.pack(pady=8)

    tk.Label(fr_in, text="Directory:",
             font=("Helvetica", 12)).grid(row=0, column=0, padx=5, pady=5)
    entry_dir = tk.Entry(fr_in, width=60, font=("Helvetica", 12))
    entry_dir.grid(row=0, column=1, padx=5, pady=5)

    def _browse_dir():
        d = filedialog.askdirectory()
        if d:
            entry_dir.delete(0, tk.END)
            entry_dir.insert(0, d)

    tk.Button(fr_in, text="Sfoglia", command=_browse_dir,
              font=("Helvetica", 12), bg="#4CAF50", fg="white").grid(row=0, column=2, padx=5)

    tk.Label(fr_in, text="Stringa:",
             font=("Helvetica", 12)).grid(row=1, column=0, padx=5, pady=5)
    entry_str_ascii = tk.Entry(fr_in, width=40, font=("Helvetica", 12))
    entry_str_ascii.grid(row=1, column=1, padx=5, pady=5, sticky="w")

    filtro_var = tk.StringVar(value="Tutti i file di testo")

    def _cerca_ascii():
        d = entry_dir.get()
        s = entry_str_ascii.get()
        if not d or not s:
            messagebox.showwarning("Campi vuoti", "Riempi entrambi i campi.")
            return
        trova_stringa_ascii(d, s, log_ascii, filtro_var, pb_ascii, pbl_ascii)

    tk.Button(fr_in, text="Cerca", command=_cerca_ascii,
              font=("Helvetica", 12), bg="#4CAF50", fg="white").grid(row=1, column=2, padx=5)

    fr_log_ascii = tk.Frame(tab_ascii)
    fr_log_ascii.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

    log_ascii = scrolledtext.ScrolledText(fr_log_ascii, font=("Courier", 11), wrap=tk.WORD)
    log_ascii.pack(fill=tk.BOTH, expand=True)

    pb_ascii  = ttk.Progressbar(fr_log_ascii, orient="horizontal", length=400, mode="determinate")
    pb_ascii.pack(pady=4)
    pbl_ascii = tk.Label(fr_log_ascii, text="0%", font=("Helvetica", 10, "bold"))
    pbl_ascii.pack()

    fr_btm_ascii = tk.Frame(tab_ascii)
    fr_btm_ascii.pack(pady=4)
    tk.Label(fr_btm_ascii, text="Filtro:", font=("Helvetica", 11)).pack(side=tk.LEFT, padx=5)
    ttk.Combobox(fr_btm_ascii, textvariable=filtro_var,
                 values=["Tutti i file di testo", "Solo history.txt", "Solo file .rar"],
                 state="readonly", font=("Helvetica", 11), width=22).pack(side=tk.LEFT, padx=5)
    tk.Button(fr_btm_ascii, text="Pulisci Log",
              command=lambda: log_ascii.delete(1.0, tk.END),
              font=("Helvetica", 12), bg="#FFC107", fg="black").pack(side=tk.LEFT, padx=10)

    # ══════════════════════════════════════════════════════════════════════════
    # TAB 2 — Analisi BIN
    # ══════════════════════════════════════════════════════════════════════════
    tab_bin = tk.Frame(notebook)
    notebook.add(tab_bin, text="  Analisi BIN  ")

    # ── Selezione file + profilo ─────────────────────────────────────────────
    fr_file = tk.LabelFrame(tab_bin, text="File BIN",
                            font=("Helvetica", 11, "bold"), padx=8, pady=6)
    fr_file.pack(fill=tk.X, padx=10, pady=6)

    tk.Label(fr_file, text="File:", font=("Helvetica", 12)).grid(row=0, column=0, padx=5, pady=4)
    entry_bin = tk.Entry(fr_file, width=68, font=("Helvetica", 11))
    entry_bin.grid(row=0, column=1, padx=5, pady=4)

    def _browse_bin():
        f = filedialog.askopenfilename(
            filetypes=[("BIN files", "*.bin"), ("Tutti i file", "*.*")])
        if f:
            entry_bin.delete(0, tk.END)
            entry_bin.insert(0, f)

    tk.Button(fr_file, text="Sfoglia", command=_browse_bin,
              font=("Helvetica", 11), bg="#4CAF50", fg="white").grid(row=0, column=2, padx=5)

    tk.Label(fr_file, text="Profilo ECU:", font=("Helvetica", 12)).grid(row=1, column=0, padx=5, pady=4)
    profilo_var = tk.StringVar(value=list(ECU_PROFILES.keys())[0])
    ttk.Combobox(fr_file, textvariable=profilo_var, values=list(ECU_PROFILES.keys()),
                 state="readonly", font=("Helvetica", 11), width=36).grid(
                     row=1, column=1, sticky="w", padx=5)

    def _analizza():
        f = entry_bin.get()
        if not f or not os.path.exists(f):
            messagebox.showwarning("File mancante", "Seleziona un file BIN valido.")
            return
        analizza_bin(f, profilo_var.get(), log_bin, pb_bin, pbl_bin)

    tk.Button(fr_file, text="Analizza SW", command=_analizza,
              font=("Helvetica", 12, "bold"), bg="#1565C0", fg="white",
              width=14).grid(row=1, column=2, padx=5)

    # ── Ricerca manuale ──────────────────────────────────────────────────────
    fr_man = tk.LabelFrame(tab_bin, text="Ricerca manuale  (offset hex + pattern)",
                           font=("Helvetica", 11, "bold"), padx=8, pady=6)
    fr_man.pack(fill=tk.X, padx=10, pady=4)

    # Riga 0 — offset range + preset rapidi
    tk.Label(fr_man, text="Start (hex):",
             font=("Helvetica", 11)).grid(row=0, column=0, padx=5, pady=3)
    entry_off_s = tk.Entry(fr_man, width=10, font=("Courier", 11))
    entry_off_s.grid(row=0, column=1, padx=4, pady=3)
    entry_off_s.insert(0, "000000")

    tk.Label(fr_man, text="End (hex):",
             font=("Helvetica", 11)).grid(row=0, column=2, padx=5)
    entry_off_e = tk.Entry(fr_man, width=10, font=("Courier", 11))
    entry_off_e.grid(row=0, column=3, padx=4, pady=3)
    entry_off_e.insert(0, "3FFFFF")

    # Bottoni preset zone (si aggiornano al cambio profilo)
    fr_presets = tk.Frame(fr_man)
    fr_presets.grid(row=0, column=4, padx=12)

    def _aggiorna_presets(*_):
        for w in fr_presets.winfo_children():
            w.destroy()
        presets = ECU_PROFILES.get(profilo_var.get(), {}).get("preset_zones", [])
        if presets:
            tk.Label(fr_presets, text="Zona rapida:",
                     font=("Helvetica", 10)).pack(side=tk.LEFT, padx=4)
        for nome, s, e in presets:
            def _click(s=s, e=e):
                entry_off_s.delete(0, tk.END); entry_off_s.insert(0, s)
                entry_off_e.delete(0, tk.END); entry_off_e.insert(0, e)
            tk.Button(fr_presets, text=nome, command=_click,
                      font=("Helvetica", 9), bg="#546E7A", fg="white",
                      padx=4).pack(side=tk.LEFT, padx=2)

    profilo_var.trace("w", _aggiorna_presets)
    _aggiorna_presets()

    # Riga 1 — pattern + tipo + cerca
    tk.Label(fr_man, text="Pattern:",
             font=("Helvetica", 11)).grid(row=1, column=0, padx=5, pady=3)
    entry_pattern = tk.Entry(fr_man, width=34, font=("Courier", 11))
    entry_pattern.grid(row=1, column=1, columnspan=2, padx=4, pady=3, sticky="w")

    tipo_var = tk.StringVar(value="ASCII")
    ttk.Combobox(fr_man, textvariable=tipo_var, values=["ASCII", "HEX"],
                 state="readonly", font=("Helvetica", 11),
                 width=7).grid(row=1, column=3, padx=4)

    def _cerca_manuale():
        f = entry_bin.get()
        cerca_bin_manuale(f, entry_pattern.get(),
                          entry_off_s.get(), entry_off_e.get(),
                          tipo_var.get(), log_bin, pb_bin, pbl_bin)

    tk.Button(fr_man, text="Cerca", command=_cerca_manuale,
              font=("Helvetica", 11), bg="#4CAF50", fg="white",
              width=10).grid(row=1, column=4, padx=10)

    # ── Log BIN (sfondo scuro) ───────────────────────────────────────────────
    fr_log_bin = tk.Frame(tab_bin)
    fr_log_bin.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)

    log_bin = scrolledtext.ScrolledText(
        fr_log_bin, font=("Courier", 11),
        wrap=tk.NONE, bg="#1E1E1E", fg="#D4D4D4",
        insertbackground="white")
    log_bin.pack(fill=tk.BOTH, expand=True)

    sb_h = ttk.Scrollbar(fr_log_bin, orient="horizontal", command=log_bin.xview)
    sb_h.pack(fill=tk.X)
    log_bin.configure(xscrollcommand=sb_h.set)

    pb_bin  = ttk.Progressbar(fr_log_bin, orient="horizontal", length=400, mode="determinate")
    pb_bin.pack(pady=4)
    pbl_bin = tk.Label(fr_log_bin, text="0%", font=("Helvetica", 10, "bold"))
    pbl_bin.pack()

    # ── Footer comune ────────────────────────────────────────────────────────
    fr_footer = tk.Frame(root)
    fr_footer.pack(pady=6)

    def _pulisci_bin():
        log_bin.delete(1.0, tk.END)
        pb_bin["value"] = 0
        pbl_bin.config(text="0%")

    def _salva_bin():
        txt = log_bin.get(1.0, tk.END)
        if not txt.strip():
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text file", "*.txt"), ("Tutti i file", "*.*")])
        if path:
            with open(path, "w", encoding="utf-8") as fout:
                fout.write(txt)

    tk.Button(fr_footer, text="Pulisci Log BIN", command=_pulisci_bin,
              font=("Helvetica", 12), bg="#FFC107", fg="black").pack(side=tk.LEFT, padx=8)
    tk.Button(fr_footer, text="Salva Risultati", command=_salva_bin,
              font=("Helvetica", 12), bg="#0288D1", fg="white").pack(side=tk.LEFT, padx=8)
    tk.Button(fr_footer, text="Esci", command=root.quit,
              font=("Helvetica", 12), bg="#f44336", fg="white").pack(side=tk.LEFT, padx=8)

    root.mainloop()

# ─── Avvio ────────────────────────────────────────────────────────────────────

try:
    configura_unrar()
except FileNotFoundError as e:
    print(e)

avvia_app()
