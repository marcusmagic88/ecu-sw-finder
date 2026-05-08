import os
import queue
import threading
import tkinter as tk
from tkinter import scrolledtext, messagebox, ttk, filedialog

try:
    import rarfile
    rarfile.UNRAR_TOOL = r"C:\Program Files\WinRAR\UnRAR.exe"
    RAR_AVAILABLE = os.path.exists(rarfile.UNRAR_TOOL)
except ImportError:
    RAR_AVAILABLE = False

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

FILTERS = [
    "Tutti i file di testo (.txt)",
    "Solo _history.txt",
    "File binari (.bin)",
    "Archivi RAR (.rar)",
    "Tutti i file",
]


def get_files(directory, filtro):
    result = []
    for root, _, files in os.walk(directory):
        for f in files:
            fl = f.lower()
            if filtro == "Tutti i file di testo (.txt)" and fl.endswith(".txt"):
                result.append(os.path.join(root, f))
            elif filtro == "Solo _history.txt" and fl.endswith("_history.txt"):
                result.append(os.path.join(root, f))
            elif filtro == "File binari (.bin)" and fl.endswith(".bin"):
                result.append(os.path.join(root, f))
            elif filtro == "Archivi RAR (.rar)" and fl.endswith(".rar"):
                result.append(os.path.join(root, f))
            elif filtro == "Tutti i file":
                result.append(os.path.join(root, f))
    return result


def search_text_file(path, strings):
    """Returns list of (string, line_no, line_text) — or ("ERROR", 0, msg)."""
    hits = []
    try:
        with open(path, "rb") as f:
            for line_no, raw in enumerate(f, 1):
                raw_lower = raw.lower()
                for s in strings:
                    if s.lower().encode("ascii", errors="ignore") in raw_lower:
                        line_text = raw.decode("utf-8", errors="replace").strip()
                        hits.append((s, line_no, line_text))
    except Exception as e:
        hits.append(("ERROR", 0, str(e)))
    return hits


def search_bin_file(path, strings):
    """Returns list of (string, offset) — or ("ERROR", msg)."""
    hits = []
    try:
        with open(path, "rb") as f:
            data = f.read()
        data_lower = data.lower()
        for s in strings:
            encoded = s.lower().encode("ascii", errors="ignore")
            if not encoded:
                continue
            pos = 0
            while True:
                idx = data_lower.find(encoded, pos)
                if idx == -1:
                    break
                hits.append((s, idx))
                pos = idx + 1
    except Exception as e:
        hits.append(("ERROR", str(e)))
    return hits


def search_rar_file(path, strings):
    """Returns list of (string, inner_file, line_no, line_text) — or ("ERROR", ...)."""
    hits = []
    if not RAR_AVAILABLE:
        return [("ERROR", "", 0, "UnRAR non trovato — installa WinRAR")]
    try:
        with rarfile.RarFile(path) as rf:
            for info in rf.infolist():
                if info.is_dir():
                    continue
                try:
                    data = rf.read(info)
                    text = data.decode("utf-8", errors="replace")
                    for line_no, line in enumerate(text.splitlines(), 1):
                        line_lower = line.lower()
                        for s in strings:
                            if s.lower() in line_lower:
                                hits.append((s, info.filename, line_no, line.strip()))
                except Exception as e:
                    hits.append(("ERROR", info.filename, 0, str(e)))
    except Exception as e:
        hits.append(("ERROR", "", 0, str(e)))
    return hits


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Cerca Versione SW — v2")
        self.root.state("zoomed")
        self._stop_event = threading.Event()
        self._queue = queue.Queue()
        self._set_icon()
        self._build_ui()

    def _set_icon(self):
        ico = os.path.join(BASE_DIR, "Logo.ico")
        if not os.path.exists(ico):
            return
        if PIL_AVAILABLE:
            try:
                img = Image.open(ico)
                self._icon_ref = ImageTk.PhotoImage(img)
                self.root.iconphoto(True, self._icon_ref)
                return
            except Exception:
                pass
        try:
            self.root.iconbitmap(ico)
        except Exception:
            pass

    def _build_ui(self):
        tk.Label(self.root, text="Cerca Versione SW", font=("Helvetica", 18, "bold")).pack(pady=8)

        frm = tk.Frame(self.root)
        frm.pack(fill=tk.X, padx=15, pady=4)
        frm.columnconfigure(1, weight=1)

        tk.Label(frm, text="Directory:", font=("Helvetica", 11)).grid(row=0, column=0, sticky="e", padx=5, pady=4)
        self.entry_dir = tk.Entry(frm, font=("Helvetica", 11))
        self.entry_dir.grid(row=0, column=1, sticky="ew", padx=5, pady=4)
        tk.Button(frm, text="Sfoglia", command=self._browse_dir,
                  bg="#4CAF50", fg="white", font=("Helvetica", 11)).grid(row=0, column=2, padx=5)

        tk.Label(frm, text="Stringhe (separate da virgola):", font=("Helvetica", 11)).grid(row=1, column=0, sticky="e", padx=5, pady=4)
        self.entry_strings = tk.Entry(frm, font=("Helvetica", 11))
        self.entry_strings.grid(row=1, column=1, sticky="ew", padx=5, pady=4)

        tk.Label(frm, text="Filtro file:", font=("Helvetica", 11)).grid(row=2, column=0, sticky="e", padx=5, pady=4)
        self.filtro_var = tk.StringVar(value=FILTERS[0])
        ttk.Combobox(frm, textvariable=self.filtro_var, values=FILTERS,
                     state="readonly", font=("Helvetica", 11)).grid(row=2, column=1, sticky="w", padx=5, pady=4)

        btn_frm = tk.Frame(self.root)
        btn_frm.pack(pady=8)
        self.btn_cerca = tk.Button(btn_frm, text="Cerca", command=self._start_search,
                                   bg="#4CAF50", fg="white", font=("Helvetica", 12, "bold"), width=12)
        self.btn_cerca.pack(side=tk.LEFT, padx=6)
        self.btn_stop = tk.Button(btn_frm, text="Stop", command=self._stop_search,
                                  bg="#f44336", fg="white", font=("Helvetica", 12, "bold"), width=12, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=6)
        tk.Button(btn_frm, text="Pulisci Log", command=self._clear_log,
                  bg="#FFC107", fg="black", font=("Helvetica", 12), width=12).pack(side=tk.LEFT, padx=6)
        tk.Button(btn_frm, text="Esporta", command=self._export,
                  bg="#2196F3", fg="white", font=("Helvetica", 12), width=12).pack(side=tk.LEFT, padx=6)
        tk.Button(btn_frm, text="Esci", command=self.root.quit,
                  bg="#607D8B", fg="white", font=("Helvetica", 12), width=12).pack(side=tk.LEFT, padx=6)

        prog_frm = tk.Frame(self.root)
        prog_frm.pack(fill=tk.X, padx=15, pady=2)
        self.progress_bar = ttk.Progressbar(prog_frm, orient="horizontal", mode="determinate")
        self.progress_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        self.progress_label = tk.Label(prog_frm, text="0%", font=("Helvetica", 10, "bold"), width=5)
        self.progress_label.pack(side=tk.LEFT)

        log_frm = tk.Frame(self.root)
        log_frm.pack(fill=tk.BOTH, expand=True, padx=15, pady=6)
        self.log_text = scrolledtext.ScrolledText(log_frm, font=("Courier", 11), wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.tag_config("hit",   foreground="#007700")
        self.log_text.tag_config("error", foreground="#CC0000")
        self.log_text.tag_config("info",  foreground="#555555")

    def _browse_dir(self):
        d = filedialog.askdirectory()
        if d:
            self.entry_dir.delete(0, tk.END)
            self.entry_dir.insert(0, d)

    def _clear_log(self):
        self.log_text.delete(1.0, tk.END)

    def _export(self):
        content = self.log_text.get(1.0, tk.END)
        if not content.strip():
            messagebox.showinfo("Esporta", "Nessun contenuto da esportare.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("File di testo", "*.txt"), ("Tutti i file", "*.*")],
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            messagebox.showinfo("Esporta", f"Salvato in:\n{path}")

    def _log(self, msg, tag=None):
        self._queue.put(("log", msg, tag))

    def _start_search(self):
        directory = self.entry_dir.get().strip()
        raw = self.entry_strings.get().strip()
        if not directory or not raw:
            messagebox.showwarning("Campi vuoti", "Inserisci directory e almeno una stringa.")
            return
        strings = [s.strip() for s in raw.split(",") if s.strip()]

        self._stop_event.clear()
        self.btn_cerca.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.progress_bar["value"] = 0
        self.progress_label.config(text="0%")

        t = threading.Thread(
            target=self._worker,
            args=(directory, strings, self.filtro_var.get()),
            daemon=True,
        )
        t.start()
        self.root.after(100, self._poll_queue)

    def _stop_search(self):
        self._stop_event.set()

    def _worker(self, directory, strings, filtro):
        self._log(f"Directory : {directory}\n", "info")
        self._log(f"Stringhe  : {', '.join(strings)}\n", "info")
        self._log(f"Filtro    : {filtro}\n\n", "info")

        files = get_files(directory, filtro)
        total = len(files)
        if total == 0:
            self._log("Nessun file trovato con il filtro selezionato.\n", "error")
            self._queue.put(("done",))
            return

        self._log(f"File da esaminare: {total}\n\n", "info")
        found_count = 0

        for i, path in enumerate(files, 1):
            if self._stop_event.is_set():
                self._log("\nRicerca interrotta dall'utente.\n", "error")
                break

            self._queue.put(("progress", i, total))
            ext = os.path.splitext(path)[1].lower()

            if ext == ".bin":
                hits = search_bin_file(path, strings)
                if hits:
                    self._log(f"[BIN] {path}\n", "hit")
                    for item in hits:
                        if item[0] == "ERROR":
                            self._log(f"       ERRORE: {item[1]}\n", "error")
                        else:
                            s, offset = item
                            self._log(f"       '{s}'  @  0x{offset:08X}  (dec {offset})\n", "hit")
                    found_count += 1

            elif ext == ".rar":
                hits = search_rar_file(path, strings)
                if hits:
                    self._log(f"[RAR] {path}\n", "hit")
                    for item in hits:
                        s, inner, line_no, line_text = item
                        if s == "ERROR":
                            self._log(f"       ERRORE in '{inner}': {line_text}\n", "error")
                        else:
                            self._log(f"       '{s}'  in {inner}  riga {line_no}: {line_text}\n", "hit")
                    found_count += 1

            else:
                hits = search_text_file(path, strings)
                if hits:
                    self._log(f"[TXT] {path}\n", "hit")
                    for s, line_no, line_text in hits:
                        if s == "ERROR":
                            self._log(f"       ERRORE: {line_no}\n", "error")
                        else:
                            self._log(f"       '{s}'  riga {line_no}: {line_text}\n", "hit")
                    found_count += 1

        self._log(f"\nRicerca completata — file con corrispondenze: {found_count} / {total}\n", "info")
        self._queue.put(("done",))

    def _poll_queue(self):
        try:
            while True:
                item = self._queue.get_nowait()
                if item[0] == "log":
                    _, msg, tag = item
                    self.log_text.insert(tk.END, msg, tag or "")
                    self.log_text.yview(tk.END)
                elif item[0] == "progress":
                    _, val, total = item
                    pct = int(val / total * 100)
                    self.progress_bar["maximum"] = total
                    self.progress_bar["value"] = val
                    self.progress_label.config(text=f"{pct}%")
                elif item[0] == "done":
                    self.btn_cerca.config(state=tk.NORMAL)
                    self.btn_stop.config(state=tk.DISABLED)
                    return
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
