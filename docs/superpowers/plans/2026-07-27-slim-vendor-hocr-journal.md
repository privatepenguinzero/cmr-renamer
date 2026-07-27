# Vendor slimming, hOCR a passata singola, giornale rinomine Implementation Plan

> **Nota sul livello di dettaglio:** questo piano viene eseguito inline nella stessa sessione che
> l'ha scritto, con il contesto già in memoria. Riporta per esteso i design non ovvi (chiusura delle
> DLL, parsing hOCR, formato del giornale) e i casi di test; il cablaggio meccanico è descritto a
> livello di intento. Non è pensato per essere passato a un agente a freddo.

**Goal:** Ridurre l'exe da ~171 MB a ~40 MB, dimezzare le invocazioni di Tesseract, e rendere le
rinomine annullabili e i risultati dubbi visibili.

**Architecture:** Quattro blocchi indipendenti. (A) Il payload vendorizzato viene ridotto da uno
script riproducibile che calcola la chiusura transitiva delle DLL a partire dagli eseguibili
realmente invocati, strippa i simboli di debug e scarta il resto. (B) `image_to_string` +
`image_to_boxes` (due processi) diventano una sola chiamata hOCR con `hocr_char_boxes=1`, che
restituisce testo, box per carattere e confidenza per parola dallo stesso parse. (C) Ogni rinomina
viene registrata in un giornale JSON-lines, e una voce di tray annulla quelle della sessione
corrente. (D) Il calibratore acquisisce un pulsante "Prova OCR" che mostra il testo estratto dai box
sul documento a schermo.

**Tech Stack:** Python 3.10+, `objcopy` (binutils, per lo strip PE), Pillow, pytesseract,
`xml.etree.ElementTree` e `json` (entrambi stdlib), Tkinter.

## Global Constraints

- **`--onefile` resta obbligatorio.** L'utente non può distribuire una cartella. Nessuna modifica
  alla modalità di build in `.github/workflows/build_release.yml` o nel comando PyInstaller.
- **Nessuna nuova dipendenza Python.** Niente numpy/OpenCV/lxml — `xml.etree` e `json` sono stdlib.
- **Nessun test framework nel repo.** Script standalone con `assert`, eseguiti con `python3`.
- **Tesseract non è eseguibile qui** (`vendor/tesseract/bin/` contiene PE32+ Windows) e non c'è
  `tkinter`. Il parsing hOCR va testato su XML sintetico costruito dal formato reale verificato nel
  binario; tutto ciò che richiede OCR o finestre va nella checklist manuale.
- **Comportamento su OCR a vuoto invariato** (scelta esplicita dell'utente): resta
  `documento_senza_nome.pdf` con `(1)`, `(2)` sui conflitti. Non toccare.
- **Confidenza bassa = solo avviso nel log**, mai un cambio di flusso (scelta esplicita).
- **Retrocompatibilità:** un `config.ini` esistente deve funzionare invariato; ogni nuova chiave con
  `.get()`/`fallback=`.
- **Convenzione linguistica:** stringhe utente e helper in italiano, commenti/docstring in inglese.
- `SCRATCH` = `/tmp/claude-1000/-var-home-icenoir-coding-test-cmr-renamer/f4ff8060-a19d-44d6-9011-0d44e4a696a6/scratchpad`
- `WT` = `/var/home/icenoir/coding/test/cmr-renamer/.claude/worktrees/slim-vendor-hocr-journal`

## Misure di partenza (verificate)

| | Ora | Dopo |
|---|---|---|
| `vendor/tesseract` | 241.1 MB | ~29.6 MB |
| `vendor/poppler` | 24.6 MB | ~21.0 MB |
| di cui simboli di debug | 153 MB (62%) | 0 |
| `libtesseract-5.dll` | 96.8 MB | 4.0 MB (9012 export intatte) |
| eseguibili di training | 17 file, 63.4 MB | 0 |
| DLL non raggiungibili | 25 file, 43.9 MB (ICU: 34 MB) | 0 |
| `osd.traineddata` | 10.1 MB | rimosso (nessun uso di OSD nel codice) |

---

## Blocco A — Vendor slimming

### Task A1: script `tools/slim_vendor.py`

**Files:** Create `tools/slim_vendor.py`

Perché uno script e non tagli a mano: al prossimo aggiornamento di Tesseract o Poppler il payload
tornerebbe gonfio, e nessuno si ricorderebbe quali dei 69 file servono. Lo script è la
documentazione eseguibile di questa decisione.

**Design:**

- Entry point dichiarati per ogni vendor: tesseract → `tesseract.exe`; poppler → `pdftoppm.exe`,
  `pdfinfo.exe`, `pdftocairo.exe` (i tre che `pdf2image` invoca — verificato leggendo il suo
  sorgente).
- `_imports(path)` legge `objdump -p` e raccoglie le righe `DLL Name:`.
- Chiusura transitiva a partire dagli entry point, considerando solo le DLL **presenti nella
  cartella vendor** (le altre sono DLL di sistema Windows).
- Strip con `objcopy --strip-debug src dst` sui file mantenuti.
- Cancellazione di tutto il resto (exe e dll) più `osd.traineddata`.
- `--dry-run` che stampa il report senza toccare nulla; default è dry-run, serve `--apply`.

**La verifica di sicurezza che sostituisce il test su Windows** (senza questa non si merge):
dopo lo slimming, per ogni file mantenuto si rileggono gli import e si controlla che ogni DLL
importata sia (a) ancora presente nella cartella, oppure (b) **mai stata** nella cartella vendor
originale — in quel caso è una DLL di sistema e va bene. Un import che *era* in vendor e ora non c'è
più è un errore fatale. Questo non richiede allowlist di DLL di sistema, che sarebbe fonte di falsi
allarmi.

**Limite noto, da scrivere nel docstring dello script e in CLAUDE.md:** la chiusura si basa sulle
import table *statiche*. Una DLL caricata a runtime con `LoadLibrary` non comparirebbe. È il motivo
per cui la beta va provata sull'exe reale prima di considerare chiuso il lavoro.

- [ ] Scrivere lo script con `--dry-run`/`--apply` e la verifica di consistenza
- [ ] Eseguirlo in dry-run e confrontare il report con le misure di partenza qui sopra
- [ ] Commit dello script da solo (così il diff dei binari resta separato e leggibile)

### Task A2: applicare lo slimming

- [ ] `python3 tools/slim_vendor.py --apply`
- [ ] Verificare che la consistenza degli import passi senza errori
- [ ] `du -sh vendor/` → attesa ~50 MB totali contro 266 MB
- [ ] Verificare che `git status` mostri solo cancellazioni e modifiche dentro `vendor/`
- [ ] Verificare che i file mantenuti siano ancora PE validi (`file` su un campione) e che
      `libtesseract-5.dll` conservi 9012 export
- [ ] Commit dei binari (messaggio che spiega il 62% di simboli di debug e cita il limite delle
      import statiche)

### Task A3: documentazione dello slimming

- [ ] `CLAUDE.md`: nella sezione "Bundled native dependencies" spiegare che il vendor è
      **deliberatamente ridotto** da `tools/slim_vendor.py`, che un ri-vendoring va seguito da
      `--apply`, e che `objcopy` (binutils) è richiesto per rieseguirlo
- [ ] `README.md`: aggiornare la riga sui language data (`eng`/`ita`/`deu`, senza `osd`)
- [ ] Commit

---

## Blocco B — hOCR a passata singola

### Task B1: parser hOCR

**Files:** Modify `cmr_renamer/watcher.py`; Test `$SCRATCH/test_hocr.py`

**Formato verificato** nei literal di `libtesseract-5.dll`:

```html
<span class='ocr_line' title='bbox 10 20 300 50; ...'>
  <span class='ocrx_word' title='bbox 10 20 80 50; x_wconf 92'>
    <span class='ocrx_cinfo' title='x_bboxes 10 20 30 50'>C</span>
    <span class='ocrx_cinfo' title='x_bboxes 31 20 50 50'>0</span>
  </span>
</span>
```

Due dettagli che contano:
- le coordinate hOCR hanno **origine in alto a sinistra**, al contrario di `makebox` che le ha in
  basso a sinistra. Quindi `altezza = y1 - y0`, non `top - bottom`. Sbagliare questo inverte il
  rapporto e rompe la discriminazione O/0.
- l'XML è namespaced (XHTML). `ElementTree` restituisce tag come
  `{http://www.w3.org/1999/xhtml}span`, quindi il match va fatto sul suffisso del tag, non
  sull'uguaglianza.

**Produce:**

```python
def _parse_hocr(xml_bytes: bytes) -> dict:
    """{'testo': str, 'glifi': [(char, larghezza, altezza)], 'confidenze': [float]}"""
```

- `testo`: parole di una riga unite da un singolo spazio, righe unite da `\n`
- `glifi`: un elemento per ogni carattere non-spazio di `testo`, **nello stesso ordine** — allineato
  per costruzione, non per ricostruzione a posteriori
- `confidenze`: un `x_wconf` per parola
- Se un `ocrx_word` non ha figli `ocrx_cinfo` (char boxes assenti), il suo testo entra comunque in
  `testo` e si aggiungono placeholder `(char, 0, 0)`, che `_correggi_o_zero` salta grazie al
  controllo `altezza <= 0` già presente

**Test (`$SCRATCH/test_hocr.py`), su XML sintetico costruito nel formato sopra:**

- parole e righe ricostruite con gli spazi e i `\n` giusti
- `glifi` allineato ai caratteri non-spazio di `testo` (stessa lunghezza, stesso ordine)
- altezza calcolata come `y1 - y0` (origine in alto): un glifo `bbox 0 10 20 30` deve dare
  larghezza 20, altezza 20 → rapporto 1.0
- `confidenze` estratte da `x_wconf`
- namespace XHTML gestito
- `ocrx_word` senza `ocrx_cinfo` → placeholder a altezza 0, nessun crash
- XML malformato → solleva, gestito dal chiamante (Task B2)
- documento vuoto → `{'testo': '', 'glifi': [], 'confidenze': []}`

### Task B2: sostituire le due passate con una

**Produce:**

```python
OCR_MIN_CONFIDENCE_DEFAULT = 60.0   # x_wconf va da 0 a 100

def _ocr_box(crop, ocr_cfg) -> dict:
    """{'testo': str, 'confidenza_min': float | None}

    Una sola invocazione hOCR con hocr_char_boxes=1: testo, box per carattere e
    confidenza per parola vengono dallo stesso parse, quindi la correzione O/0 non
    ha più bisogno di riallineare due letture indipendenti.
    """
```

- config passata: `_ocr_config(psm)` + ` -c hocr_char_boxes=1`
- chiamata: `pytesseract.image_to_pdf_or_hocr(crop, lang=..., config=..., extension='hocr')`
- il testo passa per `_correggi_o_zero(testo, glifi, soglia, log_forme)` — invariata
- **fallback obbligatorio:** se la chiamata hOCR o il parsing sollevano, si ripiega su
  `pytesseract.image_to_string` con un avviso nel log, senza correzione O/0. Un'API più ricca non
  deve rendere il programma più fragile di prima.
- confidenza riportata = **minimo** delle `x_wconf` (la parola peggiore rovina il nome, non la media)

In `_rinomina_pdf`: sostituire il blocco `image_to_string` + `_rifinisci_o_zero` con `_ocr_box`, e
loggare un avviso quando `confidenza_min` è sotto `ocr_cfg['min_confidence']`. Rimuovere
`_rifinisci_o_zero` (il suo ruolo di orchestrazione passa a `_ocr_box`) mantenendo
`_parse_glyph_boxes` **solo se ancora usata** — altrimenti va rimossa anch'essa, insieme al suo
test, per non lasciare codice morto.

Nuove chiavi `[OCR]` opzionali: `min_confidence` (default `OCR_MIN_CONFIDENCE_DEFAULT`).

**Test:** estendere `$SCRATCH/test_pipeline.py` con `image_to_pdf_or_hocr` simulata:
- una sola invocazione per box (non due): contare le chiamate
- `--psm 6` e `hocr_char_boxes=1` entrambi presenti nella config passata
- correzione O/0 ancora corretta end-to-end: `0` ovale resta cifra, `0` tondo diventa lettera, `3M` intatto
- confidenza sotto soglia → avviso nel log ma **file rinominato comunque**
- hOCR che solleva → fallback a `image_to_string`, file rinominato, avviso nel log

### Task B3: aggiornare CLAUDE.md sul percorso OCR

- [ ] Riscrivere la sezione "Processing pipeline" e "O-vs-0 disambiguation by glyph shape": una sola
      passata, origine delle coordinate in alto a sinistra, allineamento per costruzione,
      fallback su `image_to_string`, confidenza dal medesimo parse
- [ ] Documentare `min_confidence` tra le chiavi opzionali

---

## Blocco C — Giornale rinomine e undo dalla tray

### Task C1: giornale JSON-lines

**Files:** Modify `cmr_renamer/watcher.py`; Test `$SCRATCH/test_giornale.py`

**Formato:** un oggetto JSON per riga in `rinomine.log`, accanto a `config.ini`
(`_get_config_dir()`). JSON e non TSV perché i nomi file possono contenere qualsiasi carattere,
tabulazioni incluse dopo un OCR sfortunato.

```json
{"sessione": "2026-07-27T11:03:12", "ts": "2026-07-27T11:04:01", "da": "DOC0042.pdf", "a": "00123 ROSSI & FIGLI.pdf"}
{"sessione": "2026-07-27T11:03:12", "tipo": "undo", "ts": "2026-07-27T11:10:00"}
```

- `sessione` = timestamp ISO dell'avvio del processo, calcolato una volta in `run()` e tenuto in un
  modulo-level `_SESSIONE_ID`. Permette di individuare "le rinomine di questa sessione" anche dopo
  un riavvio dell'exe, senza tenere stato in memoria.
- una riga `{"tipo": "undo"}` marca una sessione come già annullata, così un secondo click non
  ritenta nulla.

**Produce:**

```python
def _percorso_giornale() -> str
def _registra_rinomina(originale: str, nuovo: str) -> None   # append, mai bloccante
def _leggi_giornale() -> list                                # righe malformate ignorate
def _rinomine_annullabili() -> list                          # ultima sessione non ancora annullata
def _annulla_rinomine() -> tuple   # (ripristinati, saltati)
```

`_annulla_rinomine` procede **in ordine inverso** e ripristina una voce solo se il file esiste col
nome nuovo *e* il nome originale è libero; altrimenti la salta e la conta. Un `os.rename` che
solleva viene assorbito e contato come saltato: un undo parziale è meglio di un undo che si ferma a
metà lasciando stato incoerente e nessun messaggio.

`_registra_rinomina` non deve mai far fallire una rinomina già avvenuta: ogni errore di scrittura
del giornale viene loggato e ignorato.

**Test:**
- append e rilettura round-trip, con nomi contenenti `&`, apostrofi, tab e caratteri non-ASCII
- righe malformate/troncate nel mezzo → ignorate, le altre lette
- `_rinomine_annullabili` restituisce solo l'ultima sessione
- una sessione con marcatore `undo` non è più annullabile
- undo che ripristina; undo con file mancante → saltato, contato; undo con nome originale già
  occupato → saltato, contato
- ordine inverso rispettato (verificabile con due rinomine a catena A→B, B→C)

### Task C2: cablaggio e voce di tray

- [ ] `_rinomina_pdf` chiama `_registra_rinomina` dopo ogni `os.rename` riuscito (entrambi i rami:
      con e senza conflitto)
- [ ] `_SESSIONE_ID` inizializzato in `run()`
- [ ] Voce di tray "Annulla ultime rinomine" prima di "Esci", che:
      - se non c'è nulla da annullare → avviso nel log e nessuna azione
      - se `tkinter` è disponibile → `messagebox.askyesno` con il numero di file coinvolti, perché è
        un'azione che tocca file su una condivisione di rete e un click in un menu è troppo facile
      - se `tkinter` non è disponibile → procede e logga
      - riporta nel log quanti ripristinati e quanti saltati
- [ ] Test: firma della voce di menu e comportamento a giornale vuoto (senza tray reale)

### Task C3: documentare giornale e undo in CLAUDE.md

- [ ] Nuovo paragrafo su `rinomine.log`, il concetto di sessione, il marcatore `undo`, e il fatto
      che l'undo è parziale-tollerante per scelta

---

## Blocco D — "Prova OCR" nel calibratore

### Task D1: pulsante e visualizzazione

**Files:** Modify `cmr_renamer/watcher.py`

**Cambio di firma:** `_calibra_box(pdf_paths, initial_path, boxes, ocr_cfg)` — `dpi` sparisce come
parametro separato perché è già in `ocr_cfg`, e servono anche `lang`/`psm`/soglie per la prova.
Aggiornare i due call site (`_rinomina_pdf`, tray `_recalibra`).

**Comportamento:** un pulsante "Prova OCR" accanto a Salva/Annulla che, sulla pagina attualmente a
schermo e con i box attualmente disegnati:
- applica lo stesso `preview_shift` già usato per il disegno, così la prova misura esattamente i
  ritagli che il programma userebbe su quel file
- esegue `_ocr_box` su ogni box
- mostra il risultato in un `Label` multiriga: una riga per box con testo estratto e confidenza,
  più il nome file che ne risulterebbe (passando da `_pulisci_nome` e dal join con spazio, cioè la
  stessa catena di `_rinomina_pdf`)
- prima di partire scrive "Prova in corso..." e forza un `update_idletasks()`, perché l'OCR è
  sincrono e blocca la finestra per qualche secondo
- gli errori vengono mostrati nel Label, non sollevati: la calibrazione non deve morire perché una
  prova è andata male

Questo chiude il ciclo: oggi i box si posizionano alla cieca e il risultato si scopre solo dopo che
un file è stato rinominato.

- [ ] Implementare, aggiornare i call site
- [ ] Test: firma a 4 parametri, fallback pulito senza `tkinter`

### Task D2: documentare in CLAUDE.md

- [ ] Aggiungere il pulsante alla descrizione del calibratore e la nuova firma

---

## Task finale: verifica manuale (richiede Windows)

Niente di quanto segue è verificabile qui: manca `tkinter` e i binari Tesseract/Poppler sono PE
Windows. **Il primo punto è bloccante per il resto.**

- [ ] **L'exe della beta parte e rinomina un file.** È il test che sostituisce l'analisi statica
      delle import table: se una DLL veniva caricata dinamicamente, lo slimming l'ha rimossa e si
      scopre qui. Un errore tipo "impossibile trovare il modulo" indica esattamente questo.
- [ ] Dimensione dell'exe scaricato: attesa ~40 MB contro 171 MB
- [ ] Tempo di elaborazione per file confrontato con beta 4: atteso più veloce (una invocazione
      Tesseract invece di due sui box con glifi ambigui)
- [ ] Discriminazione O/0 ancora corretta dopo il passaggio a hOCR — **attenzione particolare qui**,
      perché il cambio di origine delle coordinate (da basso-sinistra a alto-sinistra) è il punto
      dove un errore silenzioso invertirebbe i rapporti. Con `log_forme = true` i rapporti loggati
      devono restare ≈1.00 per le lettere e ≈0.68 per le cifre, come in beta 4. Se comparissero
      invertiti, è quello il bug.
- [ ] Avviso di confidenza: verificare che compaia su un documento di scansione scadente e che il
      file venga comunque rinominato
- [ ] "Prova OCR" nel calibratore mostra testo e confidenza coerenti con la rinomina reale
- [ ] "Annulla ultime rinomine": conferma, ripristino corretto, secondo click che non fa nulla
- [ ] `rinomine.log` leggibile e con i nomi originali corretti
- [ ] Verificare che un `config.ini` di beta 4 si carichi invariato

## Self-Review

**Copertura:** slimming (A1-A3), hOCR passata singola (B1-B3), confidenza come avviso (B2),
giornale + undo da tray (C1-C3), Prova OCR (D1-D2), `--onefile` intatto (Global Constraints),
comportamento a vuoto invariato (Global Constraints). Riduzione delle lingue: **deliberatamente
esclusa** — richiede misure sui documenti reali dell'utente, che qui non ho; va proposta come
esperimento dopo la beta, non decisa a scatola chiusa.

**Rischi in ordine di gravità:**
1. **Una DLL caricata dinamicamente** rimossa dallo slimming → exe che non parte. Mitigazione: la
   verifica di consistenza degli import, e il primo punto della checklist manuale.
2. **Origine delle coordinate hOCR invertita** → discriminazione O/0 che sbaglia sistematicamente,
   in modo silenzioso. Mitigazione: test esplicito sull'altezza in B1, e il punto dedicato nella
   checklist manuale con i valori attesi.
3. **Undo su cartella di rete** → conferma via messagebox, tolleranza ai file mancanti, marcatore di
   sessione per impedire il doppio undo.
4. **Regressione di robustezza** passando a un'API più ricca → fallback obbligatorio a
   `image_to_string` in B2.
