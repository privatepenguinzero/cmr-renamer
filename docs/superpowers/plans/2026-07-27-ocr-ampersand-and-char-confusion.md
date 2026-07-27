# OCR: `&` preservata e confusione O/0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Far sì che la `&` (e l'apostrofo) sopravvivano fino al nome del file, e che le lettere `O` non vengano più scritte come `0` (né viceversa) nei nomi generati dall'OCR.

**Architecture:** Tre interventi indipendenti su `cmr_renamer/watcher.py`. (1) La whitelist di `_pulisci_nome` viene allargata a `&` e `'`, entrambi legali nei nomi file Windows. (2) La chiamata a Tesseract passa da segmentazione automatica di pagina (`--psm 3`, il default implicito che pytesseract non sovrascrive) a `--psm 6` "blocco uniforme di testo", che è la modalità corretta per un ritaglio, e ogni crop riceve un bordo bianco prima dell'OCR — entrambe raccomandazioni della documentazione ufficiale per regioni ritagliate strette. (3) Ogni box acquisisce un *tipo di carattere* (`misto`/`testo`/`numeri`) scelto nel calibratore e persistito in `config.ini`, che applica una correzione deterministica post-OCR delle coppie confondibili.

**Perché non `tessedit_char_whitelist`:** è la soluzione "ovvia" ma non funziona in modo affidabile col motore LSTM di Tesseract 4/5 (regressione nota dal 4.0, ripristinata solo parzialmente; i fallimenti peggiori riguardano proprio i set con caratteri accentati, cioè il nostro `ita+deu`). Il vendoring usa `tessdata_fast`, che non contiene i dati del motore legacy `--oem 0` dove la whitelist funzionerebbe. La correzione post-OCR ottiene lo stesso risultato in modo deterministico, indipendente dal motore, e — differenza pratica non trascurabile — è testabile senza il binario Tesseract.

**Tech Stack:** Python 3.10+, Pillow (`ImageOps`), pytesseract, Tkinter, `configparser`.

## Global Constraints

- **Nessuna nuova dipendenza.** Solo Pillow — niente numpy, niente OpenCV (vincolo deliberato per tenere leggero il bundle PyInstaller).
- **Nessun framework di test nel repo.** Le verifiche sono script Python standalone con `assert`, eseguiti con `python3`. Non assumere `pytest`/`ruff`.
- **Tesseract non è eseguibile in questo ambiente** (`vendor/tesseract/bin/` contiene binari Windows PE32+). Ogni test automatizzato deve poter girare senza il binario; ciò che richiede OCR reale va nella checklist manuale (Task 8).
- **Convenzione linguistica:** stringhe rivolte all'utente e nomi degli helper in italiano; docstring e commenti in inglese.
- **Pattern chiavi opzionali:** ogni nuova chiave di `config.ini` va letta con `.get()`/`fallback=`, mai con subscript diretto — i `config.ini` esistenti non la avranno (stesso pattern di `prefix`, `box1..5`, `anchor_x/y`, `show_rects`).
- **Retrocompatibilità:** un `config.ini` scritto da v3.1.0b3 deve continuare a funzionare senza modifiche, con comportamento `misto` (nessuna correzione) su tutti i box.
- `SCRATCH` = `/tmp/claude-1000/-var-home-icenoir-coding-test-cmr-renamer/f4ff8060-a19d-44d6-9011-0d44e4a696a6/scratchpad`

## File Structure

| File | Responsabilità | Modifica |
|---|---|---|
| `cmr_renamer/watcher.py` | Tutta l'applicazione: pulizia nome, preprocessing, OCR, persistenza config, calibratore Tk, tray, watcher | Modificato (unico file di codice toccato) |
| `CLAUDE.md` | Documentazione architetturale per agenti | Modificato (Task 7) |
| `$SCRATCH/test_*.py` | Script di verifica usa-e-getta | Creati, non committati |

`watcher.py` è già un file unico da ~900 righe per scelta del progetto (documentata in CLAUDE.md: "`watcher.py` is the whole application"). Non va splittato in questo lavoro: sarebbe una ristrutturazione non richiesta e slegata dai due bug.

---

### Task 1: `_pulisci_nome` preserva `&` e `'`

**Files:**
- Modify: `cmr_renamer/watcher.py:175-182` (`_pulisci_nome`)
- Test: `$SCRATCH/test_pulisci.py`

**Interfaces:**
- Consumes: niente.
- Produces: `_pulisci_nome(testo: str, max_len: int, rimuovi_zeri: bool) -> str` — firma invariata, cambia solo l'insieme di caratteri conservati.

**Contesto:** la regex attuale `r'[^\w\s.-]'` cancella tutto ciò che non è alfanumerico/underscore, spazio, punto o trattino. La `&` viene quindi eliminata *anche quando Tesseract la legge correttamente*. `&` e `'` sono entrambi legali nei nomi file Windows (i vietati sono `\ / : * ? " < > |`). La virgola è stata esplicitamente esclusa dall'utente.

- [ ] **Step 1: Scrivere il test che fallisce**

```python
# $SCRATCH/test_pulisci.py
import re, sys
sys.path.insert(0, '/var/home/icenoir/coding/test/cmr-renamer')
from cmr_renamer.watcher import _pulisci_nome

# & e ' devono sopravvivere
assert _pulisci_nome("ROSSI & FIGLI SRL", 100, False) == "ROSSI & FIGLI SRL"
assert _pulisci_nome("L'OREAL S.p.A.", 100, False) == "L'OREAL S.p.A."
assert _pulisci_nome("A & B", 100, False) == "A & B"

# i caratteri illegali su Windows devono continuare a sparire
assert _pulisci_nome('DOC/123:45*6?7"8<9>0|1\\2', 100, False) == "DOC123456789012"

# la virgola resta esclusa (scelta esplicita dell'utente)
assert _pulisci_nome("ROSSI, MILANO", 100, False) == "ROSSI MILANO"

# comportamenti preesistenti invariati
assert _pulisci_nome("  12345\n", 100, False) == "12345"
assert _pulisci_nome("000123", 100, True) == "123"
assert _pulisci_nome("ABCDEF", 3, False) == "ABC"
# accentate e umlaut passano da \w (unicode-aware in Python 3)
assert _pulisci_nome("MÜLLER PERUGIÀ", 100, False) == "MÜLLER PERUGIÀ"

print("OK test_pulisci")
```

- [ ] **Step 2: Eseguire il test e verificare che fallisca**

Run: `python3 $SCRATCH/test_pulisci.py`
Expected: `AssertionError` alla prima asserzione (`_pulisci_nome("ROSSI & FIGLI SRL", ...)` restituisce `"ROSSI  FIGLI SRL"`, con doppio spazio).

- [ ] **Step 3: Implementare**

In `cmr_renamer/watcher.py`, sostituire il corpo di `_pulisci_nome`:

```python
# Caratteri non alfanumerici mantenuti nel nome file: tutti legali su Windows
# (i vietati sono \ / : * ? " < > |). La & compare spesso nelle ragioni sociali
# ("ROSSI & FIGLI"), l'apostrofo nei nomi italiani ("L'OREAL").
_EXTRA_NAME_CHARS = r".&'-"


def _pulisci_nome(testo: str, max_len: int, rimuovi_zeri: bool) -> str:
    """Pulisce il testo OCR per usarlo come nome file."""
    clean = re.sub(rf'[^\w\s{_EXTRA_NAME_CHARS}]', '', testo).replace('\n', ' ').strip()
    if len(clean) > max_len:
        clean = clean[:max_len]
    if rimuovi_zeri:
        clean = re.sub(r'^0+', '', clean)
    return clean
```

Nota sull'ordine dentro la classe di caratteri: il `-` deve restare **ultimo** in `_EXTRA_NAME_CHARS`, altrimenti verrebbe interpretato come intervallo (es. `.-&` significherebbe "da `.` a `&`").

- [ ] **Step 4: Eseguire il test e verificare che passi**

Run: `python3 $SCRATCH/test_pulisci.py`
Expected: `OK test_pulisci`

- [ ] **Step 5: Commit**

```bash
git add cmr_renamer/watcher.py
git commit -m "Preserve & and ' in OCR-derived filenames"
```

---

### Task 2: Correzione deterministica delle coppie confondibili

**Files:**
- Modify: `cmr_renamer/watcher.py` (nuove costanti + funzione, subito dopo `_pulisci_nome`)
- Test: `$SCRATCH/test_confusioni.py`

**Interfaces:**
- Consumes: niente.
- Produces:
  - `CHAR_MODE_MISTO = "misto"`, `CHAR_MODE_TESTO = "testo"`, `CHAR_MODE_NUMERI = "numeri"`
  - `CHAR_MODES = (CHAR_MODE_MISTO, CHAR_MODE_TESTO, CHAR_MODE_NUMERI)` — ordine usato anche per i radio button del calibratore (Task 5)
  - `CHAR_MODE_LABELS: dict[str, str]` — etichette UI italiane
  - `_correggi_confusioni(testo: str, modo: str) -> str`

**Contesto:** Tesseract confonde forme simili quando non ha contesto linguistico. Se l'utente dichiara che un box contiene solo lettere (ragione sociale) o solo cifre (numero documento), la mappatura diventa non ambigua e applicabile a valle dell'OCR. `misto` è il default e non tocca nulla, quindi il comportamento resta identico a oggi per chi non configura niente.

La mappa verso cifre è volutamente **conservativa**: include solo confusioni frequenti e visivamente inequivocabili. `T→7`, `A→4`, `q→9` sono escluse perché generano falsi positivi più spesso di quanti ne correggano.

- [ ] **Step 1: Scrivere il test che fallisce**

```python
# $SCRATCH/test_confusioni.py
import sys
sys.path.insert(0, '/var/home/icenoir/coding/test/cmr-renamer')
from cmr_renamer.watcher import (
    _correggi_confusioni, CHAR_MODES,
    CHAR_MODE_MISTO, CHAR_MODE_TESTO, CHAR_MODE_NUMERI,
)

# modo testo: le cifre sosia diventano lettere (il bug segnalato dall'utente)
assert _correggi_confusioni("C00PERATIVA", CHAR_MODE_TESTO) == "COOPERATIVA"
assert _correggi_confusioni("R0SSI & FIGLI", CHAR_MODE_TESTO) == "ROSSI & FIGLI"
assert _correggi_confusioni("8ARILLA", CHAR_MODE_TESTO) == "BARILLA"
assert _correggi_confusioni("5PA", CHAR_MODE_TESTO) == "SPA"
assert _correggi_confusioni("1TALIA", CHAR_MODE_TESTO) == "ITALIA"
assert _correggi_confusioni("2ETA", CHAR_MODE_TESTO) == "ZETA"
assert _correggi_confusioni("6AMMA", CHAR_MODE_TESTO) == "GAMMA"

# modo numeri: le lettere sosia diventano cifre
assert _correggi_confusioni("O12345", CHAR_MODE_NUMERI) == "012345"
assert _correggi_confusioni("l23", CHAR_MODE_NUMERI) == "123"
assert _correggi_confusioni("S6O", CHAR_MODE_NUMERI) == "560"
assert _correggi_confusioni("B00", CHAR_MODE_NUMERI) == "800"

# modo misto: nessuna modifica
assert _correggi_confusioni("C00PERATIVA", CHAR_MODE_MISTO) == "C00PERATIVA"
assert _correggi_confusioni("O12345", CHAR_MODE_MISTO) == "O12345"

# un modo sconosciuto (config.ini corrotto a mano) non deve rompere: nessuna modifica
assert _correggi_confusioni("C00PERATIVA", "boh") == "C00PERATIVA"

# caratteri non coinvolti restano intatti in ogni modo
for modo in CHAR_MODES:
    assert _correggi_confusioni(" &'-.", modo) == " &'-."
    assert _correggi_confusioni("MÜLLER", modo).startswith("MÜLLER"[0])

# le lettere non-sosia non vengono toccate in modo numeri
assert _correggi_confusioni("X", CHAR_MODE_NUMERI) == "X"

print("OK test_confusioni")
```

- [ ] **Step 2: Eseguire il test e verificare che fallisca**

Run: `python3 $SCRATCH/test_confusioni.py`
Expected: `ImportError: cannot import name '_correggi_confusioni' from 'cmr_renamer.watcher'`

- [ ] **Step 3: Implementare**

In `cmr_renamer/watcher.py`, subito dopo `_pulisci_nome`:

```python
# Tipo di carattere atteso in un box, scelto nel calibratore e salvato in config.ini.
# Serve a disambiguare le coppie che Tesseract confonde quando manca il contesto
# linguistico (O/0, I/1, S/5...). tessedit_char_whitelist non è utilizzabile allo
# scopo: col motore LSTM di Tesseract 4/5 viene ignorato o degrada il risultato,
# in particolare sui set con accentate — e i tessdata_fast che imbarchiamo non
# contengono i dati del motore legacy dove funzionerebbe.
CHAR_MODE_MISTO = "misto"
CHAR_MODE_TESTO = "testo"
CHAR_MODE_NUMERI = "numeri"
CHAR_MODES = (CHAR_MODE_MISTO, CHAR_MODE_TESTO, CHAR_MODE_NUMERI)

CHAR_MODE_LABELS = {
    CHAR_MODE_MISTO: "Misto",
    CHAR_MODE_TESTO: "Testo",
    CHAR_MODE_NUMERI: "Numeri",
}

# Cifre che Tesseract produce al posto della lettera corrispondente.
_CONFUSIONI_VERSO_LETTERA = {
    '0': 'O', '1': 'I', '2': 'Z', '5': 'S', '6': 'G', '8': 'B',
}

# Lettere che Tesseract produce al posto della cifra corrispondente. Volutamente
# conservativa: T/7, A/4, q/9 causerebbero più falsi positivi che correzioni.
_CONFUSIONI_VERSO_CIFRA = {
    'O': '0', 'o': '0', 'D': '0',
    'I': '1', 'l': '1', 'i': '1',
    'Z': '2', 'z': '2',
    'S': '5', 's': '5',
    'G': '6', 'g': '6',
    'B': '8',
}


def _correggi_confusioni(testo: str, modo: str) -> str:
    """Corregge le coppie di caratteri confondibili in base al tipo dichiarato del box.

    `misto` (default) non tocca nulla. Un modo sconosciuto — es. config.ini
    modificato a mano — viene trattato come `misto` anziché sollevare.
    """
    if modo == CHAR_MODE_TESTO:
        mappa = _CONFUSIONI_VERSO_LETTERA
    elif modo == CHAR_MODE_NUMERI:
        mappa = _CONFUSIONI_VERSO_CIFRA
    else:
        return testo
    return ''.join(mappa.get(c, c) for c in testo)
```

- [ ] **Step 4: Eseguire il test e verificare che passi**

Run: `python3 $SCRATCH/test_confusioni.py`
Expected: `OK test_confusioni`

- [ ] **Step 5: Commit**

```bash
git add cmr_renamer/watcher.py
git commit -m "Add per-box character mode and confusion-pair correction"
```

---

### Task 3: Persistenza dei tipi di carattere in `config.ini`

**Files:**
- Modify: `cmr_renamer/watcher.py:274-309` (`_save_calibration_to_config`, `_load_boxes_from_config`)
- Test: `$SCRATCH/test_config_chars.py`

**Interfaces:**
- Consumes: `CHAR_MODES`, `CHAR_MODE_MISTO`, `MAX_BOXES` (Task 2 / esistenti).
- Produces:
  - `_save_calibration_to_config(boxes: list, anchor: "tuple[int,int] | None", char_modes: list) -> None` — **firma cambiata**, terzo parametro obbligatorio
  - `_load_char_modes_from_config(ocr_section, n_boxes: int) -> list` — ritorna sempre esattamente `n_boxes` elementi

**Contesto:** le chiavi si chiamano `box1_chars`..`box5_chars`, parallele a `box1`..`box5`, e vengono ripulite oltre il numero di box attivi esattamente come già succede per `box{N}` (evita chiavi stantie passando da 4 box a 3). Un `config.ini` senza queste chiavi deve produrre `misto` per ogni box.

- [ ] **Step 1: Scrivere il test che fallisce**

```python
# $SCRATCH/test_config_chars.py
import configparser, os, sys, tempfile
sys.path.insert(0, '/var/home/icenoir/coding/test/cmr-renamer')
import cmr_renamer.watcher as w
from cmr_renamer.watcher import (
    _load_char_modes_from_config, CHAR_MODE_MISTO, CHAR_MODE_TESTO, CHAR_MODE_NUMERI,
)

# --- lettura ---
cfg = configparser.ConfigParser()
cfg.read_string("[OCR]\nbox1 = 1,2,3,4\nbox2 = 5,6,7,8\n")
# config.ini pre-esistente, senza le nuove chiavi -> tutto misto (retrocompatibilità)
assert _load_char_modes_from_config(cfg['OCR'], 2) == [CHAR_MODE_MISTO, CHAR_MODE_MISTO]

cfg2 = configparser.ConfigParser()
cfg2.read_string("[OCR]\nbox1_chars = numeri\nbox2_chars = testo\n")
assert _load_char_modes_from_config(cfg2['OCR'], 2) == [CHAR_MODE_NUMERI, CHAR_MODE_TESTO]

# valore non valido scritto a mano -> misto, non un crash
cfg3 = configparser.ConfigParser()
cfg3.read_string("[OCR]\nbox1_chars = sciocchezza\n")
assert _load_char_modes_from_config(cfg3['OCR'], 1) == [CHAR_MODE_MISTO]

# meno chiavi che box -> la lista viene comunque riempita fino a n_boxes
cfg4 = configparser.ConfigParser()
cfg4.read_string("[OCR]\nbox1_chars = testo\n")
assert _load_char_modes_from_config(cfg4['OCR'], 3) == [CHAR_MODE_TESTO, CHAR_MODE_MISTO, CHAR_MODE_MISTO]

# --- scrittura ---
tmpdir = tempfile.mkdtemp()
w._get_config_dir = lambda: tmpdir
path = os.path.join(tmpdir, 'config.ini')

w._save_calibration_to_config(
    [(1, 2, 3, 4), (5, 6, 7, 8), (9, 10, 11, 12)], (30, 40),
    [CHAR_MODE_NUMERI, CHAR_MODE_TESTO, CHAR_MODE_MISTO],
)
saved = configparser.ConfigParser(); saved.read(path)
assert saved['OCR']['box1_chars'] == 'numeri'
assert saved['OCR']['box2_chars'] == 'testo'
assert saved['OCR']['box3_chars'] == 'misto'
assert saved['OCR']['box1'] == '1,2,3,4'
assert saved['OCR']['anchor_x'] == '30'

# passando a 2 box, box3_chars non deve restare orfano
w._save_calibration_to_config(
    [(1, 2, 3, 4), (5, 6, 7, 8)], (30, 40), [CHAR_MODE_TESTO, CHAR_MODE_TESTO],
)
saved2 = configparser.ConfigParser(); saved2.read(path)
assert 'box3' not in saved2['OCR'], saved2['OCR'].get('box3')
assert 'box3_chars' not in saved2['OCR'], saved2['OCR'].get('box3_chars')

# round-trip
assert _load_char_modes_from_config(saved2['OCR'], 2) == [CHAR_MODE_TESTO, CHAR_MODE_TESTO]

print("OK test_config_chars")
```

- [ ] **Step 2: Eseguire il test e verificare che fallisca**

Run: `python3 $SCRATCH/test_config_chars.py`
Expected: `ImportError: cannot import name '_load_char_modes_from_config'`

- [ ] **Step 3: Implementare**

Sostituire `_save_calibration_to_config` e aggiungere `_load_char_modes_from_config` dopo `_load_boxes_from_config`:

```python
def _save_calibration_to_config(boxes: list, anchor: "tuple[int, int] | None", char_modes: list) -> None:
    """Salva coordinate dei box (2-5), tipo di carattere per box e ancora di contenuto nel config.ini esistente."""
    config_path = os.path.join(_get_config_dir(), 'config.ini')
    config = configparser.ConfigParser()
    config.read(config_path)
    if 'OCR' not in config:
        config['OCR'] = {}
    for i, box in enumerate(boxes, start=1):
        config['OCR'][f'box{i}'] = ','.join(str(int(v)) for v in box)
    for i, modo in enumerate(char_modes[:len(boxes)], start=1):
        config['OCR'][f'box{i}_chars'] = modo
    # Rimuove eventuali chiavi box(N+1).. rimaste da una configurazione precedente
    # con più box (es. da 4 box a 3: box4 va eliminato, non lasciato stantio).
    for i in range(len(boxes) + 1, MAX_BOXES + 1):
        config['OCR'].pop(f'box{i}', None)
        config['OCR'].pop(f'box{i}_chars', None)
    if anchor is not None:
        config['OCR']['anchor_x'] = str(int(anchor[0]))
        config['OCR']['anchor_y'] = str(int(anchor[1]))
    else:
        config['OCR'].pop('anchor_x', None)
        config['OCR'].pop('anchor_y', None)
    with open(config_path, 'w') as f:
        config.write(f)


def _load_char_modes_from_config(ocr_section, n_boxes: int) -> list:
    """Legge box1_chars..box5_chars, riempiendo con `misto` ciò che manca o non è valido.

    Ritorna sempre esattamente `n_boxes` elementi: i config.ini creati prima che
    questa opzione esistesse non hanno nessuna di queste chiavi e devono comportarsi
    come oggi, cioè senza correzione.
    """
    modes = []
    for i in range(1, n_boxes + 1):
        raw = (ocr_section.get(f'box{i}_chars') or '').strip().lower()
        modes.append(raw if raw in CHAR_MODES else CHAR_MODE_MISTO)
    return modes
```

- [ ] **Step 4: Eseguire il test e verificare che passi**

Run: `python3 $SCRATCH/test_config_chars.py`
Expected: `OK test_config_chars`

Nota: a questo punto i tre call site di `_save_calibration_to_config` (`_rinomina_pdf`, tray `_recalibra`) sono rotti perché passano 2 argomenti. Vengono sistemati nel Task 6; è atteso e il commit di questo task resta comunque coerente perché la funzione non viene invocata dai test.

- [ ] **Step 5: Commit**

```bash
git add cmr_renamer/watcher.py
git commit -m "Persist per-box character mode as box{N}_chars in config.ini"
```

---

### Task 4: `--psm 6` e bordo bianco attorno ai crop

**Files:**
- Modify: `cmr_renamer/watcher.py:194-202` (`_preprocess_for_ocr`)
- Test: `$SCRATCH/test_preprocess.py`

**Interfaces:**
- Consumes: niente.
- Produces:
  - `OCR_PSM_DEFAULT = 6`
  - `_ocr_config(psm: int) -> str` — stringa `config` da passare a `pytesseract.image_to_string`
  - `_preprocess_for_ocr(img: "Image.Image") -> "Image.Image"` — firma invariata, aggiunge un bordo bianco

**Contesto:** `pytesseract.image_to_string` passa `config=''`, quindi Tesseract usa il proprio default `--psm 3` (analisi di layout di una pagina intera) su un ritaglio di poche centinaia di pixel. La documentazione ufficiale indica `6` (blocco uniforme di testo) / `7` (riga singola) / `8` (parola singola) per le regioni ritagliate; si sceglie **6** perché tollera sia una riga sola sia una ragione sociale su più righe, mentre `7` scarterebbe le righe successive. La stessa pagina di documentazione segnala che *"adding a white border to text which is too tightly cropped may also help"*: i box calibrati col mouse sono per definizione tagliati stretti attorno al testo.

Il bordo va aggiunto **dopo** la binarizzazione, altrimenti `ImageOps.autocontrast` calcolerebbe l'istogramma includendo il bordo appena inserito e altererebbe la soglia.

- [ ] **Step 1: Scrivere il test che fallisce**

```python
# $SCRATCH/test_preprocess.py
import sys
sys.path.insert(0, '/var/home/icenoir/coding/test/cmr-renamer')
from PIL import Image
from cmr_renamer.watcher import _preprocess_for_ocr, _ocr_config, OCR_PSM_DEFAULT, OCR_BORDER_PX

# il config OCR deve chiedere esplicitamente la segmentazione a blocco uniforme
assert _ocr_config(6) == "--psm 6"
assert _ocr_config(OCR_PSM_DEFAULT) == "--psm 6"
assert _ocr_config(7) == "--psm 7"

# il crop esce con un bordo bianco su tutti i lati
src = Image.new('L', (50, 20), color=0)  # tutto nero: senza bordo non ci sarebbero pixel bianchi
out = _preprocess_for_ocr(src)
assert out.size == (50 + 2 * OCR_BORDER_PX, 20 + 2 * OCR_BORDER_PX), out.size
assert out.getpixel((0, 0)) == 255, "l'angolo deve essere bordo bianco"
assert out.getpixel((OCR_BORDER_PX + 25, OCR_BORDER_PX + 10)) == 0, "il contenuto deve restare nero"

# la binarizzazione preesistente resta: solo 0 e 255, nessun grigio
assert set(out.getdata()) <= {0, 255}

# un'immagine RGB deve continuare a essere accettata
rgb = Image.new('RGB', (30, 10), color=(255, 255, 255))
assert _preprocess_for_ocr(rgb).mode == 'L'

print("OK test_preprocess")
```

- [ ] **Step 2: Eseguire il test e verificare che fallisca**

Run: `python3 $SCRATCH/test_preprocess.py`
Expected: `ImportError: cannot import name '_ocr_config'`

- [ ] **Step 3: Implementare**

Sostituire `_preprocess_for_ocr` e aggiungere le costanti/funzione accanto:

```python
# Tesseract, senza --psm esplicito, usa il proprio default 3 (analisi di layout di
# una pagina intera): sbagliato per un ritaglio di poche centinaia di pixel, ed è
# la condizione in cui la classificazione dei caratteri perde contesto e confonde
# le forme simili. 6 = "blocco uniforme di testo": regge sia una riga sola sia una
# ragione sociale su più righe (7 scarterebbe le righe dopo la prima).
OCR_PSM_DEFAULT = 6

# Bordo bianco aggiunto attorno a ogni crop prima dell'OCR: la documentazione
# ufficiale lo raccomanda per il testo tagliato stretto, che è esattamente il caso
# dei box disegnati col mouse.
OCR_BORDER_PX = 10


def _ocr_config(psm: int) -> str:
    """Stringa di configurazione da passare a pytesseract."""
    return f"--psm {int(psm)}"


def _preprocess_for_ocr(img: "Image.Image") -> "Image.Image":
    """Migliora un crop prima dell'OCR: scala di grigi, contrasto, binarizzazione, bordo bianco.

    Soglia fissa (128), non derivata per immagine — punto di partenza pensato
    per tuning manuale (vedi verifica del piano), non un default definitivo.
    Il bordo si aggiunge dopo la binarizzazione, così da non falsare l'istogramma
    usato da autocontrast.
    """
    gray = img.convert('L')
    contrasted = ImageOps.autocontrast(gray)
    binaria = contrasted.point(lambda p: 255 if p > 128 else 0)
    return ImageOps.expand(binaria, border=OCR_BORDER_PX, fill=255)
```

- [ ] **Step 4: Eseguire il test e verificare che passi**

Run: `python3 $SCRATCH/test_preprocess.py`
Expected: `OK test_preprocess`

- [ ] **Step 5: Commit**

```bash
git add cmr_renamer/watcher.py
git commit -m "Use --psm 6 and pad OCR crops with a white border"
```

---

### Task 5: Selettore del tipo di carattere nel calibratore

**Files:**
- Modify: `cmr_renamer/watcher.py:27` (import tkinter), `346-656` (`_calibra_box`)
- Test: `$SCRATCH/test_calibra_signature.py`

**Interfaces:**
- Consumes: `CHAR_MODES`, `CHAR_MODE_LABELS`, `CHAR_MODE_MISTO` (Task 2).
- Produces: `_calibra_box(pdf_paths: list, initial_path: str, boxes: list, char_modes: list, dpi: int)` — **firma cambiata**, `char_modes` inserito prima di `dpi`. Ritorna `{'boxes': [...], 'char_modes': [...], 'anchor': (x, y) | None}` oppure `None`.

**Contesto:** il calibratore ha già una riga di pulsanti colorati per selezionare il box attivo (`rebuild_select_buttons` / `set_active`). Il tipo si applica al **box attivo**, quindi va reso come un gruppo di tre `Radiobutton` legati a una `StringVar`, aggiornati quando cambia il box attivo e che riscrivono `state['char_modes'][state['active']]` quando l'utente clicca.

Punti a cui prestare attenzione:
- `add_box` e `remove_box` devono tenere `char_modes` allineato a `boxes`, altrimenti gli indici scivolano.
- La `StringVar` va aggiornata dentro `set_active`, ma con `mode_var.set(...)` che **non** deve ri-scattare la scrittura sullo stato del box precedente: si usa quindi una guardia `state['syncing']`.
- `char_modes` in ingresso può essere più corto di `boxes` (config vecchio): va normalizzato all'inizio.

- [ ] **Step 1: Scrivere il test che fallisce**

Il calibratore è Tkinter e questo ambiente non ha `tkinter` (`TKINTER_AVAILABLE` è `False`), quindi `_calibra_box` prende subito il ramo di uscita. Il test verifica ciò che è verificabile senza display: la firma e il ramo di fallback.

```python
# $SCRATCH/test_calibra_signature.py
import inspect, sys
sys.path.insert(0, '/var/home/icenoir/coding/test/cmr-renamer')
import cmr_renamer.watcher as w

params = list(inspect.signature(w._calibra_box).parameters)
assert params == ['pdf_paths', 'initial_path', 'boxes', 'char_modes', 'dpi'], params

# senza tkinter deve ritornare None senza sollevare
assert w.TKINTER_AVAILABLE is False, "questo test presuppone un ambiente senza tkinter"
assert w._calibra_box(['/x.pdf'], '/x.pdf', [(0, 0, 1, 1)], ['misto'], 300) is None

print("OK test_calibra_signature")
```

- [ ] **Step 2: Eseguire il test e verificare che fallisca**

Run: `python3 $SCRATCH/test_calibra_signature.py`
Expected: `AssertionError: ['pdf_paths', 'initial_path', 'boxes', 'dpi']`

- [ ] **Step 3: Implementare**

**3a.** Estendere l'import Tkinter alla riga 27:

```python
    from tkinter import Tk, Canvas, Button, Label, Frame, Scrollbar, Listbox, Radiobutton, StringVar
```

**3b.** Firma e docstring di `_calibra_box`:

```python
def _calibra_box(pdf_paths: list, initial_path: str, boxes: list, char_modes: list, dpi: int):
    """Mostra la pagina 1 di un PDF a scelta tra `pdf_paths` e permette di ridisegnare 2-5 box col mouse.

    `pdf_paths` è l'elenco dei PDF della cartella monitorata, selezionabili da una lista laterale
    per confrontare visivamente se i box calibrati si applicano bene a più documenti; il cambio file
    ridisegna solo l'immagine di sfondo e sposta i box del solo scostamento di deriva rilevato.
    `initial_path` è il file mostrato all'apertura (preselezionato in lista). `boxes` è una lista di
    partenza di 2-5 tuple (x1,y1,x2,y2); `char_modes` il tipo di carattere atteso per ciascun box
    (valori in CHAR_MODES), normalizzato a `misto` dove manca. Se l'utente salva, ritorna
    {'boxes': [...], 'char_modes': [...], 'anchor': (x,y) | None} — l'ancora è rilevata
    sull'immagine visualizzata al momento del salvataggio (non necessariamente quella di
    `initial_path`, se nel frattempo si è passati a un altro file dalla lista). Ritorna None se
    l'utente annulla.
    """
```

**3c.** Inizializzare lo stato — sostituire il dizionario `state` (righe ~373-378) con:

```python
    modes_iniziali = [
        char_modes[i] if i < len(char_modes) and char_modes[i] in CHAR_MODES else CHAR_MODE_MISTO
        for i in range(len(boxes))
    ]

    state = {
        'boxes': list(boxes),
        'char_modes': modes_iniziali,
        'active': 0, 'start': None, 'drag_id': None, 'result': None,
        'zoom': 1.0, 'photo': None, 'img': None, 'current_path': initial_path,
        'reference_anchor': None, 'preview_shift': (0, 0), 'syncing': False,
    }
```

**3d.** Aggiungere il frame dei radio button. Dopo il blocco `zoom_frame` (riga ~415-416), inserire:

```python
        mode_frame = Frame(top_frame)
        mode_frame.pack(side="left", padx=20)

        Label(mode_frame, text="Contenuto:").pack(side="left")
        mode_var = StringVar(value=CHAR_MODE_MISTO)
```

**3e.** Aggiungere il callback e i radio button. Subito **prima** di `def set_active(index):` (riga ~519), inserire:

```python
        def on_mode_change():
            # set_active() aggiorna mode_var per riflettere il box appena selezionato:
            # senza questa guardia quella scrittura verrebbe riapplicata al box attivo.
            if state['syncing']:
                return
            state['char_modes'][state['active']] = mode_var.get()

        for modo in CHAR_MODES:
            Radiobutton(
                mode_frame, text=CHAR_MODE_LABELS[modo], value=modo,
                variable=mode_var, command=on_mode_change,
            ).pack(side="left")
```

**3f.** Sincronizzare la `StringVar` in `set_active` — sostituire il corpo di `set_active`:

```python
        def set_active(index):
            state['active'] = index
            for i, btn in select_buttons.items():
                btn.config(relief=("sunken" if i == index else "raised"))
            state['syncing'] = True
            mode_var.set(state['char_modes'][index])
            state['syncing'] = False
            label.config(text=f"Box attivo: {_box_label(index)}. Trascina col mouse per ridisegnarlo (rotellina per zoomare).")
```

**3g.** Tenere allineati `boxes` e `char_modes` — sostituire `add_box` e `remove_box`:

```python
        def add_box():
            if len(state['boxes']) >= MAX_BOXES:
                return
            state['boxes'].append(_default_box(len(state['boxes'])))
            state['char_modes'].append(CHAR_MODE_MISTO)
            rebuild_select_buttons()
            set_active(len(state['boxes']) - 1)
            render()

        def remove_box():
            if len(state['boxes']) <= MIN_BOXES:
                return
            removed = state['active']
            del state['boxes'][removed]
            del state['char_modes'][removed]
            rebuild_select_buttons()
            set_active(max(removed - 1, 0))
            render()
```

**3h.** Restituire anche i modi — sostituire `on_save`:

```python
        def on_save():
            state['result'] = {
                'boxes': list(state['boxes']),
                'char_modes': list(state['char_modes']),
                'anchor': _detect_content_anchor(state['img']),
            }
            root.destroy()
```

- [ ] **Step 4: Eseguire il test e verificare che passi**

Run: `python3 $SCRATCH/test_calibra_signature.py`
Expected: `OK test_calibra_signature`

- [ ] **Step 5: Commit**

```bash
git add cmr_renamer/watcher.py
git commit -m "Add per-box content type selector to the calibrator"
```

---

### Task 6: Collegare i tipi al pipeline di rinomina

**Files:**
- Modify: `cmr_renamer/watcher.py` — `_rinomina_pdf` (~757-815), `_build_tray_icon._recalibra` (~675-696), `run()` (~935-941)
- Test: `$SCRATCH/test_pipeline.py`

**Interfaces:**
- Consumes: `_correggi_confusioni`, `CHAR_MODE_MISTO` (Task 2); `_load_char_modes_from_config`, `_save_calibration_to_config` (Task 3); `_ocr_config`, `OCR_PSM_DEFAULT` (Task 4); `_calibra_box` (Task 5).
- Produces: `ocr_cfg['char_modes']: list[str]` e `ocr_cfg['psm']: int`, disponibili a tutto il resto dell'applicazione.

**Contesto:** è il task che chiude il cerchio. Tre punti:
1. `run()` legge `char_modes` e `psm` in `ocr_cfg` (chiavi opzionali, con fallback).
2. `_rinomina_pdf` passa `char_modes` al calibratore, li riceve indietro, li salva, e applica `_correggi_confusioni` a ogni box **prima** di `_pulisci_nome` — l'ordine conta: la correzione deve vedere il testo grezzo, non uno già troncato a `max_length`.
3. Il tray `_recalibra` fa lo stesso giro.

Serve anche una guardia: se `ocr_cfg['char_modes']` è più corto di `ocr_cfg['boxes']` (config vecchio, o box aggiunti a mano), la lettura per indice deve degradare a `misto` invece di sollevare `IndexError`.

- [ ] **Step 1: Scrivere il test che fallisce**

```python
# $SCRATCH/test_pipeline.py
import os, sys, tempfile
sys.path.insert(0, '/var/home/icenoir/coding/test/cmr-renamer')
from PIL import Image
import cmr_renamer.watcher as w

tmpdir = tempfile.mkdtemp()
pdf_path = os.path.join(tmpdir, 'DOC_test.pdf')
open(pdf_path, 'wb').close()

# Tesseract non è eseguibile qui: si simula il testo grezzo che restituirebbe.
finte_letture = ["O0123", "C00PERATIVA R0SSI & FIGLI"]
chiamate = {'n': 0, 'config': []}

def finto_ocr(image, lang=None, config=''):
    chiamate['config'].append(config)
    testo = finte_letture[chiamate['n']]
    chiamate['n'] += 1
    return testo

w._render_pdf_page = lambda path, dpi: Image.new('RGB', (600, 400), color=(255, 255, 255))
w.pytesseract.image_to_string = finto_ocr

ocr_cfg = {
    'boxes': [(0, 0, 100, 50), (0, 60, 300, 110)],
    'char_modes': [w.CHAR_MODE_NUMERI, w.CHAR_MODE_TESTO],
    'anchor': None, 'show_rects': False, 'lang': 'ita', 'dpi': 300,
    'psm': w.OCR_PSM_DEFAULT,
}
name_cfg = {'max_length': 100, 'remove_leading_zeros': False}

w._rinomina_pdf(pdf_path, ocr_cfg, name_cfg)

# --psm deve essere stato passato a Tesseract
assert chiamate['config'] == ['--psm 6', '--psm 6'], chiamate['config']

prodotti = os.listdir(tmpdir)
# box1 (numeri): "O0123" -> "00123";  box2 (testo): "C00PERATIVA R0SSI & FIGLI" -> "COOPERATIVA ROSSI & FIGLI"
assert prodotti == ['00123 COOPERATIVA ROSSI & FIGLI.pdf'], prodotti

# --- char_modes più corto dei boxes (config.ini pre-esistente): nessun IndexError ---
pdf2 = os.path.join(tmpdir, 'DOC_due.pdf')
open(pdf2, 'wb').close()
chiamate['n'] = 0
finte_letture[:] = ["O0123", "C00PERATIVA"]
ocr_cfg_vecchio = dict(ocr_cfg, char_modes=[])
w._rinomina_pdf(pdf2, ocr_cfg_vecchio, name_cfg)
# senza modi dichiarati non si corregge nulla: comportamento identico a prima del fix
assert 'O0123 C00PERATIVA.pdf' in os.listdir(tmpdir), os.listdir(tmpdir)

print("OK test_pipeline")
```

- [ ] **Step 2: Eseguire il test e verificare che fallisca**

Run: `python3 $SCRATCH/test_pipeline.py`
Expected: `AssertionError: ['']` — `config` non viene ancora passato a `image_to_string`.

- [ ] **Step 3: Implementare**

**3a.** In `run()`, sostituire il blocco `ocr_cfg` (righe ~931-941):

```python
    # box1..box5 are absent from freshly-created config.ini files (the box
    # count and coordinates are selected with the mouse on the first PDF
    # processed, not prompted for at setup time); show_rects likewise has no
    # setup prompt and only takes effect if a user hand-edits config.ini.
    # box{N}_chars is written by the calibrator; psm has no prompt either and
    # exists to allow tuning the segmentation mode without a rebuild.
    boxes = _load_boxes_from_config(cfg['OCR'])
    ocr_cfg = {
        'boxes': boxes,
        'char_modes': _load_char_modes_from_config(cfg['OCR'], len(boxes)),
        'anchor': _load_anchor_from_config(cfg['OCR']),
        'show_rects': cfg['OCR'].getboolean('show_rects', fallback=False),
        'lang': cfg['OCR']['lang'],
        'dpi': int(cfg['OCR']['dpi']),
        'psm': cfg['OCR'].getint('psm', fallback=OCR_PSM_DEFAULT),
    }
```

**3b.** In `_rinomina_pdf`, sostituire il blocco di calibrazione (righe ~772-784, da `boxes_seed = ...` fino al `return` del ramo annullato):

```python
                    boxes_seed = list(ocr_cfg['boxes'])
                    modes_seed = list(ocr_cfg.get('char_modes', []))
                    while len(boxes_seed) < MIN_BOXES:
                        boxes_seed.append(_default_box(len(boxes_seed)))
                    while len(modes_seed) < len(boxes_seed):
                        modes_seed.append(CHAR_MODE_MISTO)
                    pdf_paths = _list_watched_pdfs(os.path.dirname(pdf_path))
                    risultato = _calibra_box(pdf_paths, pdf_path, boxes_seed, modes_seed, ocr_cfg['dpi'])
                    if risultato:
                        ocr_cfg['boxes'] = risultato['boxes']
                        ocr_cfg['char_modes'] = risultato['char_modes']
                        ocr_cfg['anchor'] = risultato['anchor']
                        _save_calibration_to_config(ocr_cfg['boxes'], ocr_cfg['anchor'], ocr_cfg['char_modes'])
                        print(f"✅ Nuove coordinate salvate → {ocr_cfg['boxes']}")
                    elif serve_calibrazione:
                        print(f"⚠️ Calibrazione annullata: '{os.path.basename(pdf_path)}' non elaborato (nessun box configurato).")
                        return
```

**3c.** In `_rinomina_pdf`, sostituire il ciclo OCR (righe ~788-794):

```python
        parti = []
        modes = ocr_cfg.get('char_modes', [])
        psm = ocr_cfg.get('psm', OCR_PSM_DEFAULT)
        for i, box in enumerate(_resolve_crop_boxes(img, ocr_cfg)):
            crop = _preprocess_for_ocr(img.crop(box))
            testo = pytesseract.image_to_string(crop, lang=ocr_cfg['lang'], config=_ocr_config(psm))
            # La correzione va applicata al testo grezzo, prima che _pulisci_nome
            # tronchi a max_length o rimuova gli zeri iniziali.
            modo = modes[i] if i < len(modes) else CHAR_MODE_MISTO
            testo = _correggi_confusioni(testo, modo)
            pulito = _pulisci_nome(testo, name_cfg['max_length'], name_cfg['remove_leading_zeros'])
            if pulito:
                parti.append(pulito)
```

**3d.** Nel tray, sostituire il corpo di `_recalibra` (righe ~684-692):

```python
            boxes_seed = list(ocr_cfg['boxes'])
            modes_seed = list(ocr_cfg.get('char_modes', []))
            while len(boxes_seed) < MIN_BOXES:
                boxes_seed.append(_default_box(len(boxes_seed)))
            while len(modes_seed) < len(boxes_seed):
                modes_seed.append(CHAR_MODE_MISTO)
            risultato = _calibra_box(pdf_paths, pdf_paths[0], boxes_seed, modes_seed, ocr_cfg['dpi'])
            if risultato:
                ocr_cfg['boxes'] = risultato['boxes']
                ocr_cfg['char_modes'] = risultato['char_modes']
                ocr_cfg['anchor'] = risultato['anchor']
                _save_calibration_to_config(ocr_cfg['boxes'], ocr_cfg['anchor'], ocr_cfg['char_modes'])
                print(f"✅ Nuove coordinate salvate → {ocr_cfg['boxes']}")
```

- [ ] **Step 4: Eseguire il test e verificare che passi**

Run: `python3 $SCRATCH/test_pipeline.py`
Expected: `OK test_pipeline`

- [ ] **Step 5: Rieseguire tutti i test precedenti (regressione)**

Run: `for f in $SCRATCH/test_*.py; do python3 "$f" || echo "FALLITO: $f"; done`
Expected: cinque righe `OK ...`, nessun `FALLITO`.

- [ ] **Step 6: Verificare che non siano rimasti call site vecchi**

Run: `grep -n "_save_calibration_to_config\|_calibra_box\|image_to_string" cmr_renamer/watcher.py`
Expected: ogni chiamata a `_save_calibration_to_config` ha 3 argomenti, ogni chiamata a `_calibra_box` ne ha 5, `image_to_string` passa `config=`.

- [ ] **Step 7: Commit**

```bash
git add cmr_renamer/watcher.py
git commit -m "Apply per-box character mode and psm through the rename pipeline"
```

---

### Task 7: Aggiornare CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` (sezioni "Config lives next to the executable" e "Processing pipeline")

**Interfaces:**
- Consumes: tutto quanto sopra. Produces: niente codice.

**Contesto:** CLAUDE.md descrive `config.ini` chiave per chiave e il pipeline di elaborazione passo per passo; entrambe le descrizioni sono ora incomplete. Va documentato anche *perché* non si usa `tessedit_char_whitelist`, altrimenti è il primo cambiamento che un agente futuro proverebbe a fare.

- [ ] **Step 1: Aggiornare l'elenco delle sezioni di config**

Sostituire la riga che elenca le chiavi:

> Config sections: `[Watcher]` (folder, prefix, delay_riavvio), `[OCR]` (box1..box5 crop coordinates
> for 2-5 boxes, anchor_x/anchor_y content-anchor reference, show_rects debug flag, lang, dpi),

con:

> Config sections: `[Watcher]` (folder, prefix, delay_riavvio), `[OCR]` (box1..box5 crop coordinates
> for 2-5 boxes, box1_chars..box5_chars per-box expected content type, anchor_x/anchor_y
> content-anchor reference, show_rects debug flag, lang, dpi, psm),

- [ ] **Step 2: Documentare `box{N}_chars` e `psm` nel paragrafo delle chiavi opzionali**

Dopo la frase che descrive `box1..box5`/`anchor_x`/`anchor_y`/`show_rects` come chiavi opzionali, aggiungere:

> `box1_chars`..`box5_chars` follow the same optional-key pattern and are written by the calibrator
> alongside the boxes: each holds `misto` (default, no correction), `testo`, or `numeri`, declaring
> what a box is expected to contain. `psm` is optional too and has no prompt — it only exists so the
> Tesseract page segmentation mode can be tuned by hand without a rebuild (default `OCR_PSM_DEFAULT`,
> 6 = single uniform block of text).

- [ ] **Step 3: Aggiornare la descrizione del pipeline**

Sostituire la descrizione di `_rinomina_pdf` dove elenca i passi, così da includere bordo bianco, `--psm` e correzione:

> **Processing pipeline** (`_rinomina_pdf`): `pdf2image.convert_from_path` renders page 1 → `PIL` crops
> each of the 2-5 configured boxes → `_preprocess_for_ocr` (grayscale, autocontrast, fixed threshold,
> then a white `OCR_BORDER_PX` border — the official docs recommend padding tightly-cropped text)
> improves each crop → `pytesseract.image_to_string` OCRs each preprocessed crop with
> `_ocr_config(psm)` (`--psm 6`; without it Tesseract defaults to `--psm 3`, full-page layout
> analysis, which is wrong for a small crop and is where character shapes get misclassified) →
> `_correggi_confusioni` maps confusable pairs (`0`↔`O`, `1`↔`I`, `2`↔`Z`, `5`↔`S`, `6`↔`G`, `8`↔`B`)
> according to that box's `box{N}_chars` mode → `_pulisci_nome` strips characters illegal in a
> filename (keeping `&` and `'`, both legal on Windows and common in company names), truncates to
> `max_length`, optionally strips leading zeros → the non-empty cleaned strings are joined with a
> single space into the new filename, with `(1)`, `(2)`, ... appended on collision.

- [ ] **Step 4: Documentare perché non si usa la whitelist di Tesseract**

Aggiungere subito dopo, come paragrafo a sé:

> **Why not `tessedit_char_whitelist`:** constraining the character set is the obvious way to fix
> `O`/`0` confusion, but the parameter is unreliable with Tesseract 4/5's LSTM engine — it was
> dropped in 4.0 and only partially restored, and the reported failures are worst on character sets
> with diacritics, which is exactly ours (`ita+deu`). It works properly only under `--oem 0`, whose
> model data is not in the `tessdata_fast` files vendored under `vendor/tesseract/tessdata/`. The
> post-OCR correction in `_correggi_confusioni` is engine-independent, deterministic, and testable
> without the Tesseract binary. Don't replace it with a whitelist without verifying on real scans first.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "Document per-box character mode, psm, and the whitelist rationale"
```

---

### Task 8: Verifica manuale (richiede Windows + Tesseract)

**Files:** nessuno.

**Contesto:** questo ambiente non ha `tkinter` né un binario Tesseract eseguibile, quindi tutto ciò che segue **non è stato verificato** dall'implementazione automatica e va provato sulla macchina reale prima di considerare chiusi i due bug. Nessuna di queste voci va spuntata sulla base dei test automatici dei task 1-6.

- [ ] Aprire il calibratore (tray → "Ricalibra box") e verificare che compaia il gruppo "Contenuto:" con i tre radio `Misto` / `Testo` / `Numeri`.
- [ ] Selezionare il box 1, impostare `Numeri`; selezionare il box 2, impostare `Testo`; tornare sul box 1 e verificare che i radio mostrino ancora `Numeri` (cioè che `set_active` sincronizzi senza sovrascrivere).
- [ ] Aggiungere un box con `+ Box`, verificare che parta da `Misto`; rimuoverlo con `− Box` e verificare che i tipi dei box rimanenti non scivolino di posizione.
- [ ] Salvare e controllare che `config.ini` contenga `box1_chars = numeri` e `box2_chars = testo`.
- [ ] Far elaborare un CMR reale che contiene una `&` nella ragione sociale: il file rinominato deve contenere la `&`.
- [ ] Far elaborare un CMR reale con una `O` che prima veniva letta `0`: il nome deve ora contenere `O`.
- [ ] Verificare che il numero documento (box `Numeri`) non abbia acquisito lettere spurie.
- [ ] Confrontare la qualità complessiva dell'OCR con la beta precedente: `--psm 6` e il bordo bianco dovrebbero aver ridotto anche altri errori. Se invece qualche campo peggiora, provare `psm = 7` in `config.ini` (riga singola) prima di toccare il codice.
- [ ] Verificare che un `config.ini` di v3.1.0b3 (senza `box{N}_chars` né `psm`) venga caricato senza errori e rinomini come prima, a parte le migliorie di psm/bordo.

---

## Self-Review

**1. Copertura dei requisiti**
- `&` conservata → Task 1. `'` conservata → Task 1. Virgola esclusa → asserzione esplicita in Task 1.
- `O`/`0` → Task 2 (correzione), Task 4 (`--psm`, bordo), Task 5 (UI), Task 6 (collegamento). 
- Scelta dell'utente "tipo per box nel calibratore" → Task 5, con persistenza in Task 3.
- Retrocompatibilità config → Task 3 (`_load_char_modes_from_config`), Task 6 (guardia `i < len(modes)`), verificata in Task 6 Step 1 e in Task 8.

**2. Placeholder** — nessuno: ogni step ha il codice completo, i comandi esatti e l'output atteso.

**3. Coerenza dei tipi** — `char_modes` è ovunque una `list[str]` con valori in `CHAR_MODES`; `_calibra_box` la riceve in quarta posizione e la restituisce sotto la chiave `'char_modes'`, usata identica in `_rinomina_pdf` e nel tray; `_save_calibration_to_config` la prende come terzo parametro in entrambi i call site; `_ocr_config(psm)` è usata solo in `_rinomina_pdf`, con `psm` letto da `ocr_cfg`.

**Rischio noto:** in modo `testo` una cifra legittima all'interno della ragione sociale (es. "3M", "Gruppo 24 ORE") verrebbe convertita in lettera. È il contratto voluto — dichiarare un box come `testo` significa affermare che non contiene cifre — e `misto` resta il default per chi non vuole questa semantica. Da segnalare all'utente al momento della consegna.
