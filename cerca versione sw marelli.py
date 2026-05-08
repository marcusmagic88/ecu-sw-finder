import os

def trova_stringa_ascii(percorso_directory, stringa_ascii):
    """
    Cerca una stringa specifica in formato ASCII nei file .txt
    di una directory e delle sue sottocartelle.
    """
    print(f"Sto cercando la stringa ASCII '{stringa_ascii}' nei file .txt nella directory '{percorso_directory}'...\n")

    risultati = []

    # Scansione della directory
    for root, dirs, files in os.walk(percorso_directory):
        for nome_file in files:
            # Controlla che il file abbia estensione .txt
            if nome_file.endswith(".txt"):
                percorso_file = os.path.join(root, nome_file)
                print(f"Sto esaminando il file: {percorso_file}")  # Debug: Mostra i file .txt esaminati
                
                try:
                    # Apertura del file in modalità binaria
                    with open(percorso_file, "rb") as f:  # Legge il file in modalità binaria
                        contenuto = f.read()
                        
                        # Cerca la stringa specifica in formato ASCII
                        if stringa_ascii.encode('ascii') in contenuto:
                            risultati.append(percorso_file)
                
                except FileNotFoundError:
                    print(f"File non trovato: {percorso_file}")
                except PermissionError:
                    print(f"Permesso negato: {percorso_file}")
                except Exception as e:
                    print(f"Errore con il file {percorso_file}: {e}")

    # Mostra i risultati
    if risultati:
        print("\nStringa ASCII trovata nei seguenti file:")
        for risultato in risultati:
            print(risultato)
        
    else:
        print("\nStringa ASCII non trovata.")

    return risultati

def salva_log(risultati, percorso_directory, stringa_ascii):
    """Salva il log delle ricerche effettuate."""
    try:
        with open("log.txt", "a") as log_file:
            log_file.write(f"Ricerca eseguita su {percorso_directory} per la stringa ASCII: {stringa_ascii}\n")
            if risultati:
                log_file.write("File trovati:\n")
                for risultato in risultati:
                    log_file.write(risultato + "\n")
            else:
                log_file.write("Nessun file trovato.\n")
            log_file.write("\n" + "-"*40 + "\n")
        print("\nRisultati salvati nel file 'log.txt'.")
    except Exception as e:
        print(f"Errore durante il salvataggio del log: {e}")

def eseguire_ricerca():
    """Funzione principale per eseguire ricerche ripetute."""
    while True:
        # Input per la directory e la stringa
        percorso_directory = input("Inserisci il percorso della directory: ")
        stringa_ascii = input("Inserisci la stringa o il numero in ASCII da cercare: ")

        # Esegui la ricerca
        risultati = trova_stringa_ascii(percorso_directory, stringa_ascii)

        # Salva i risultati nel log
        salva_log(risultati, percorso_directory, stringa_ascii)

        # Chiedi all'utente se desidera eseguire una nuova ricerca
        nuova_ricerca = input("\nVuoi eseguire un'altra ricerca? (sì/no): ").strip().lower()
        
        # Aggiungi un controllo più robusto per l'input
        if nuova_ricerca not in ['sì', 'si', 'yes']:
            print("Uscita dal programma.")
            break

# Esegui il ciclo delle ricerche
eseguire_ricerca()


