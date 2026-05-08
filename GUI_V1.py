import os
import tkinter as tk
from tkinter import scrolledtext, messagebox
from tkinter import ttk
from tkinter import filedialog
import rarfile
from PIL import Image, ImageTk  # Importa la libreria Pillow

def configura_unrar():
    # Configura il percorso dello strumento UnRAR
    rarfile.UNRAR_TOOL = r"C:\\Program Files\\WinRAR\\UnRAR.exe"
    if not os.path.exists(rarfile.UNRAR_TOOL):
        raise FileNotFoundError(f"Strumento UnRAR non trovato al percorso: {rarfile.UNRAR_TOOL}. Verifica l'installazione di WinRAR e riprova.")

def aggiorna_progress_bar(progress_bar, progress_label, valore, massimo):
    percentuale = int((valore / massimo) * 100)
    progress_bar["value"] = valore
    progress_bar["maximum"] = massimo
    progress_label.config(text=f"{percentuale}%")
    root.update_idletasks()

def trova_stringa_ascii(percorso_directory, stringa_ascii, log_text, filtro_var, progress_bar, progress_label):
    log_text.delete(1.0, tk.END)  # Pulisci la consolle
    log_text.insert(tk.END, f"Sto cercando la stringa ASCII '{stringa_ascii}' nei file {filtro_var.get()} nella directory '{percorso_directory}'...\n")
    log_text.yview(tk.END)

    # Convertiamo la stringa ASCII in minuscolo
    stringa_ascii_lower = stringa_ascii.lower()

    risultati = []
    tutti_i_risultati = []  # Lista per memorizzare tutti i risultati trovati

    percorso_directory = os.path.abspath(percorso_directory)

    if not os.path.exists(percorso_directory):
        log_text.insert(tk.END, f"Errore: la directory '{percorso_directory}' non esiste.\n")
        log_text.yview(tk.END)
        return

    files_da_esaminare = []
    
    # Raccolta dei file da esaminare, considerando i filtri
    for root, dirs, files in os.walk(percorso_directory):
        for nome_file in files:
            nome_file_lower = nome_file.lower()
            if filtro_var.get() == "Tutti i file di testo" and nome_file_lower.endswith(".txt"):
                files_da_esaminare.append(os.path.join(root, nome_file))
            elif filtro_var.get() == "Solo history.txt" and nome_file_lower.endswith("_history.txt"):
                files_da_esaminare.append(os.path.join(root, nome_file))
            elif filtro_var.get() == "Solo file .rar" and nome_file_lower.endswith(".rar"):
                files_da_esaminare.append(os.path.join(root, nome_file))
    
    totale_file = len(files_da_esaminare)
    if totale_file == 0:
        log_text.insert(tk.END, f"Nessun file trovato da esaminare con il filtro '{filtro_var.get()}'\n")
        return

    log_text.insert(tk.END, f"Totale file da esaminare: {totale_file}\n")

    # Avvio ricerca con aggiornamento della progress bar
    for indice, percorso_file in enumerate(files_da_esaminare, start=1):
        aggiorna_progress_bar(progress_bar, progress_label, indice, totale_file)
        
        log_text.insert(tk.END, f"Sto esaminando il file: {percorso_file}\n")
        log_text.yview(tk.END)

        try:
            if filtro_var.get() in ["Tutti i file di testo", "Solo history.txt"]:
                with open(percorso_file, "rb") as f:
                    contenuto = f.read().lower()  # Contenuto convertito in minuscolo
                    if stringa_ascii_lower.encode('ascii') in contenuto:  # Confronto case-insensitive
                        risultati.append(percorso_file)
            elif filtro_var.get() == "Solo file .rar":
                try:
                    with rarfile.RarFile(percorso_file) as rf:
                        for file in rf.infolist():
                            try:
                                log_text.insert(tk.END, f"Esaminando il file all'interno dell'archivio: {file.filename}\n")
                                if not file.is_dir():
                                    file_data = rf.read(file)
                                    try:
                                        contenuto = file_data.decode('utf-8').lower()
                                        if stringa_ascii_lower in contenuto:
                                            risultati.append(f"{percorso_file} -> {file.filename}")
                                    except UnicodeDecodeError:
                                        log_text.insert(tk.END, f"File non testuale saltato: {file.filename}\n")
                            except Exception as e:
                                log_text.insert(tk.END, f"Errore nel leggere il file {file.filename} nell'archivio {percorso_file}: {e}\n")
                except Exception as e:
                    log_text.insert(tk.END, f"Errore nel leggere l'archivio {percorso_file}: {e}\n")
        except Exception as e:
            log_text.insert(tk.END, f"Errore con il file {percorso_file}: {e}\n")

    # Al termine della ricerca
    log_text.insert(tk.END, "\nRicerca completata.\n")

    if risultati:
        log_text.insert(tk.END, "\nStringa ASCII trovata nei seguenti file:\n")
        for risultato in risultati:
            log_text.insert(tk.END, f"{risultato}\n")

        try:
            with open("log_risultati_ascii.txt", "w") as log:
                for risultato in risultati:
                    log.write(risultato + "\n")
            log_text.insert(tk.END, "\nRisultati salvati nel file 'log_risultati_ascii.txt'.\n")
        except Exception as e:
            log_text.insert(tk.END, f"Errore durante il salvataggio del log: {e}\n")
    else:
        log_text.insert(tk.END, "\nStringa ASCII non trovata.\n")

    log_text.yview(tk.END)

def avvia_app():
    global root
    root = tk.Tk()
    root.title("Cerca Stringa ASCII")

    # Usa Pillow per caricare l'immagine ICO
    logo = Image.open(r'D:\Download\Test Script\logo.ico')
    logo = ImageTk.PhotoImage(logo)
    root.iconphoto(True, logo)  # Imposta l'icona con Pillow

    # Imposta la finestra a schermo intero
    root.state('zoomed')

    # Aggiungi un titolo
    title_label = tk.Label(root, text="Cerca Versione SW", font=("Helvetica", 18, "bold"))
    title_label.pack(pady=10)

    # Aggiungi etichette e campi di input
    frame_input = tk.Frame(root)
    frame_input.pack(pady=10)

    label_dir = tk.Label(frame_input, text="Percorso della directory:", font=("Helvetica", 12))
    label_dir.grid(row=0, column=0, padx=5, pady=5)

    entry_dir = tk.Entry(frame_input, width=60, font=("Helvetica", 12))
    entry_dir.grid(row=0, column=1, padx=5, pady=5)

    def seleziona_directory():
        directory = filedialog.askdirectory()
        if directory:
            entry_dir.delete(0, tk.END)
            entry_dir.insert(0, directory)

    button_browse = tk.Button(frame_input, text="Sfoglia", command=seleziona_directory, font=("Helvetica", 12), bg="#4CAF50", fg="white")
    button_browse.grid(row=0, column=2, padx=5, pady=5)

    label_stringa = tk.Label(frame_input, text="Stringa o numero da cercare in ASCII:", font=("Helvetica", 12))
    label_stringa.grid(row=1, column=0, padx=5, pady=5)

    entry_stringa = tk.Entry(frame_input, width=40, font=("Helvetica", 12))
    entry_stringa.grid(row=1, column=1, padx=5, pady=5, sticky="w")

    def cerca_button_click():
        percorso_directory = entry_dir.get()
        stringa_ascii = entry_stringa.get()
        if not percorso_directory or not stringa_ascii:
            messagebox.showwarning("Campi vuoti", "Per favore, riempi entrambi i campi.")
            return
        trova_stringa_ascii(percorso_directory, stringa_ascii, log_text, filtro_var, progress_bar, progress_label)

    button_cerca = tk.Button(frame_input, text="Cerca", command=cerca_button_click, font=("Helvetica", 12), bg="#4CAF50", fg="white")
    button_cerca.grid(row=1, column=2, padx=5, pady=5)

    # Frame per il log e la progress bar
    frame_log = tk.Frame(root)
    frame_log.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

    log_text = scrolledtext.ScrolledText(frame_log, font=("Courier", 12), wrap=tk.WORD)
    log_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

    # Progress bar posizionata dentro il frame del log
    progress_bar = ttk.Progressbar(frame_log, orient="horizontal", length=400, mode="determinate")
    progress_bar.pack(pady=5)

    # Etichetta per la percentuale
    progress_label = tk.Label(frame_log, text="0%", font=("Helvetica", 10, "bold"))
    progress_label.pack(pady=5)

    filtro_var = tk.StringVar(value="Tutti i file di testo")
    filtro_label = tk.Label(root, text="Filtri", font=("Helvetica", 18))
    filtro_label.pack(pady=5)

    filtro_combobox = ttk.Combobox(root, textvariable=filtro_var, values=["Tutti i file di testo", "Solo history.txt", "Solo file .rar"], state="readonly", font=("Helvetica", 12))
    filtro_combobox.pack(pady=5)

    def pulisci_log():
        log_text.delete(1.0, tk.END)

    button_pulisci_log = tk.Button(root, text="Pulisci Log", command=pulisci_log, font=("Helvetica", 14), bg="#FFC107", fg="black")
    button_pulisci_log.pack(pady=5)

    button_exit = tk.Button(root, text="Esci", command=root.quit, font=("Helvetica", 14), bg="#f44336", fg="white")
    button_exit.pack(pady=10)

    root.mainloop()

# Configura lo strumento UnRAR
try:
    configura_unrar()
except FileNotFoundError as e:
    print(e)
    exit(1)

# Avvio dell'app
avvia_app()
