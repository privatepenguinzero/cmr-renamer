"""Aggiornamento automatico dell'exe dalle release GitHub (repository privato).

Stesso protocollo del launcher di offerte-gen, ma dentro l'app e per un exe
`--onefile`: si sceglie la release più recente (beta comprese) che porti l'exe,
il suo `.sha256` e il suo `.sig`; si scarica accanto all'exe in uso come `.new`;
si verificano impronta e firma; poi si rinomina l'exe in esecuzione in `.old`
(Windows lo permette) e il `.new` al suo posto, e si riavvia. Il `.old` si
cancella al giro successivo, quando nessuno lo tiene più aperto.

Il modulo non sa nulla di Tk, del tray o del watcher: `watcher.run()` gli passa
token, percorso dell'exe e una funzione da chiamare quando serve riavviare.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import subprocess
import threading
import urllib.error
import urllib.request
from datetime import date, datetime

from . import __version__

REPO = "privatepenguinzero/cmr-renamer"
API = "https://api.github.com"
ASSET_EXE = "cmr-renamer.exe"
ASSET_SHA = ASSET_EXE + ".sha256"
ASSET_SIG = ASSET_EXE + ".sig"

TIMEOUT_API_S = 10
TIMEOUT_DOWNLOAD_S = 60
PRIMO_CONTROLLO_S = 60          # dopo l'avvio, per non rallentare la scansione iniziale
INTERVALLO_CONTROLLO_S = 6 * 3600
AVVISO_SCADENZA_TOKEN_GIORNI = 30

# Chiave pubblica di tools/firma.py (RSA-3072). La privata non è nel repository.
_CHIAVE_PUBBLICA_N = int(
    "b760d8ebcb1a2dae6ca02a90dcff62855ee087c2b765b827d0a2fed8f0b078bd"
    "dee5b04b6b424be8d8dd4998c80d39157a5c3aab56789bc5b5c84a98772141eb"
    "a156e48fe0a17ea85b8115dcdb69f5c41f351b29e36aef6c2e3e57dbc2fb1638"
    "bffb682a4921577bf14258c9219b53f3a7fdee67c56a342737e05420919b9c58"
    "0469aa1529ec4760dc8a771fd09d188f9a0784fec70430822580d90e89bc1396"
    "f079789fc118fa210599d5f6318d8aefa864a56f999b872bcc7fcf3637cf694f"
    "04a637c6889fff0ccf420521b276fe0c14b38de8f2d7c994cfb7c462c81bd6b5"
    "60d6de29ac06194ffad2d12e330cfdf05f8ca4f3fa0f7fe3ebcda719eaee8f42"
    "3c9a2e6054d4b1fb4dea79b1c8561226802832b344bc25e9f7637ff514478b6f"
    "fe9d6a9d022f200d600e04c4c0a9b5fcd4cf20fb9fb0b3708b802aab8f19d877"
    "6d24b5622846fe27a5bf1c4b1f7ee8facc8ee6563a89704b6b745281fa378019"
    "0e928af6b1907e34525403fdf261c76ab43cd264187eb6d6afe14d18cf5afdaf",
    16,
)
_CHIAVE_PUBBLICA_E = 65537

# Prefisso DER del DigestInfo SHA-256 in una firma PKCS#1 v1.5 (RFC 8017, 9.2).
_DIGEST_INFO_SHA256 = bytes.fromhex("3031300d060960864801650304020105000420")

# Accetta sia i tag delle release ("v3.1.0-beta.5") sia la forma PEP 440 di
# __version__ ("3.1.0b5"). Una versione finale sta sopra ogni sua pre-release.
_RE_VERSIONE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-.]?(a|alpha|b|beta|rc)[.-]?(\d+))?$", re.IGNORECASE)
_FASI = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "rc": 2}
_FINALE = 3


def parse_versione(testo: str) -> "tuple | None":
    """(major, minor, patch, fase, numero) confrontabile; None se il formato non è riconosciuto."""
    m = _RE_VERSIONE.match((testo or "").strip())
    if not m:
        return None
    fase = _FASI[m.group(4).lower()] if m.group(4) else _FINALE
    numero = int(m.group(5)) if m.group(5) else 0
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), fase, numero)


def firma_valida(digest: bytes, firma_b64: str, n: "int | None" = None, e: "int | None" = None) -> bool:
    """Verifica RSA PKCS#1 v1.5 con SHA-256 in Python puro.

    Evita di includere `cryptography` nell'exe solo per una verifica: con una
    chiave pubblica è un'esponenziazione modulare e un confronto con il blocco
    atteso, ricostruito per intero (niente parsing della firma decifrata).
    """
    n = _CHIAVE_PUBBLICA_N if n is None else n
    e = _CHIAVE_PUBBLICA_E if e is None else e
    try:
        firma = base64.b64decode((firma_b64 or "").strip(), validate=True)
    except ValueError:
        return False
    k = (n.bit_length() + 7) // 8
    if len(firma) != k or len(digest) != 32:
        return False
    s = int.from_bytes(firma, "big")
    if s >= n:
        return False
    em = pow(s, e, n).to_bytes(k, "big")
    riempimento = k - 3 - len(_DIGEST_INFO_SHA256) - len(digest)
    atteso = b"\x00\x01" + b"\xff" * riempimento + b"\x00" + _DIGEST_INFO_SHA256 + digest
    return hmac.compare_digest(em, atteso)


def scegli_release(releases: list, corrente: tuple) -> "dict | None":
    """La release più nuova di `corrente` che abbia exe, impronta e firma.

    Le release pubblicate ma non ancora firmate si saltano: la firma si aggiunge
    a mano dopo la CI, e verranno prese a un controllo successivo.
    """
    migliore = None
    for r in releases:
        if r.get("draft"):
            continue
        versione = parse_versione(r.get("tag_name", ""))
        if versione is None or versione <= corrente:
            continue
        asset = {a.get("name"): a.get("url") for a in r.get("assets", [])}
        if not all(asset.get(nome) for nome in (ASSET_EXE, ASSET_SHA, ASSET_SIG)):
            print(f"ℹ️ Release {r.get('tag_name')} non ancora completa o firmata: la salto per ora.")
            continue
        if migliore is None or versione > migliore["versione"]:
            migliore = {
                "versione": versione,
                "tag": r["tag_name"],
                "exe": asset[ASSET_EXE],
                "sha": asset[ASSET_SHA],
                "sig": asset[ASSET_SIG],
            }
    return migliore


class _SenzaRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_SenzaRedirect)


def _apri(url: str, token: str, accept: str, timeout: int):
    """GET verso l'API GitHub; i redirect si seguono a mano, senza token.

    Il download di un asset rimanda allo storage di GitHub, che non deve ricevere
    l'header Authorization: urllib lo ricopierebbe sul redirect, quindi il
    secondo salto si fa con una richiesta pulita.
    """
    headers = {"Accept": accept, "User-Agent": "cmr-renamer", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        return _opener.open(urllib.request.Request(url, headers=headers), timeout=timeout)
    except urllib.error.HTTPError as e:
        destinazione = e.headers.get("Location") if e.code in (301, 302, 303, 307, 308) else None
        if destinazione:
            return urllib.request.urlopen(
                urllib.request.Request(destinazione, headers={"User-Agent": "cmr-renamer"}), timeout=timeout,
            )
        if e.code == 401:
            raise RuntimeError("token GitHub rifiutato (scaduto o revocato)") from e
        if e.code == 404:
            raise RuntimeError("repository non trovato: il token non ha accesso a " + REPO) from e
        if e.code in (403, 429):
            raise RuntimeError(f"GitHub ha rifiutato la richiesta ({e.code}): riprovo più tardi") from e
        raise


def leggi_releases(token: str) -> tuple:
    """(releases, scadenza_token) — la scadenza è una `date` o None se il token non ne ha."""
    url = f"{API}/repos/{REPO}/releases?per_page=20"
    with _apri(url, token, "application/vnd.github+json", TIMEOUT_API_S) as risposta:
        scadenza = _data_scadenza(risposta.headers.get("github-authentication-token-expiration"))
        return json.loads(risposta.read().decode("utf-8")), scadenza


def _data_scadenza(valore: "str | None") -> "date | None":
    try:
        return datetime.strptime(valore.strip()[:10], "%Y-%m-%d").date() if valore else None
    except ValueError:
        return None


def _scarica(url: str, token: str, destinazione: str) -> str:
    """Scarica un asset su file; ritorna lo SHA-256 esadecimale di quanto scritto."""
    impronta = hashlib.sha256()
    with _apri(url, token, "application/octet-stream", TIMEOUT_DOWNLOAD_S) as risposta, \
            open(destinazione, "wb") as f:
        while True:
            blocco = risposta.read(1 << 16)
            if not blocco:
                break
            impronta.update(blocco)
            f.write(blocco)
    return impronta.hexdigest()


def _scarica_testo(url: str, token: str) -> str:
    with _apri(url, token, "application/octet-stream", TIMEOUT_API_S) as risposta:
        return risposta.read(64 * 1024).decode("ascii", errors="replace")


def _rimuovi(percorso: str) -> None:
    try:
        os.remove(percorso)
    except FileNotFoundError:
        pass


def installa(release: dict, token: str, exe: str) -> None:
    """Scarica, verifica e mette al suo posto il nuovo exe. Solleva se qualcosa non torna.

    Fino alla verifica della firma l'exe in uso non viene toccato. Lo scambio è
    due rinomine nella stessa cartella; se la seconda fallisce la prima si annulla.
    """
    nuovo, vecchio = exe + ".new", exe + ".old"
    try:
        impronta = _scarica(release["exe"], token, nuovo)
        righe = _scarica_testo(release["sha"], token).split()
        attesa = righe[0].lower() if righe else ""
        if not hmac.compare_digest(impronta, attesa):
            raise RuntimeError("impronta SHA-256 dell'exe scaricato non corrispondente")
        if not firma_valida(bytes.fromhex(impronta), _scarica_testo(release["sig"], token)):
            raise RuntimeError("firma della release non valida: aggiornamento rifiutato")
    except BaseException:
        _rimuovi(nuovo)
        raise

    _rimuovi(vecchio)
    os.rename(exe, vecchio)  # Windows consente di rinominare l'exe in esecuzione
    try:
        os.rename(nuovo, exe)
    except BaseException:
        os.rename(vecchio, exe)
        raise


def pulisci_residui(exe: str) -> None:
    """Cancella `.old`/`.new` di un aggiornamento precedente; se sono ancora in uso, al prossimo avvio."""
    for suffisso in (".old", ".new"):
        try:
            _rimuovi(exe + suffisso)
        except OSError:
            pass


def riavvia(exe: str) -> None:
    """Avvia il nuovo exe come processo indipendente.

    `PYINSTALLER_RESET_ENVIRONMENT=1` fa sì che il figlio `--onefile` estragga la
    propria cartella temporanea invece di riusare quella di questo processo, che
    sta per uscire e cancellarla (PyInstaller ≥ 6.9).
    """
    env = dict(os.environ)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    flag = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([exe], env=env, cwd=os.path.dirname(exe), creationflags=flag, close_fds=True)


class Aggiornatore:
    """Controllo periodico e su richiesta; chiama `al_riavvio(tag)` dopo un'installazione riuscita."""

    def __init__(self, token: str, exe: str, al_riavvio):
        self.token = token
        self.exe = exe
        self.al_riavvio = al_riavvio
        self._in_corso = threading.Lock()

    def controlla(self) -> str:
        """Un controllo completo; ritorna l'esito in una frase, già stampata nel log."""
        if not self._in_corso.acquire(blocking=False):
            return "Controllo aggiornamenti già in corso."
        try:
            esito = self._controlla()
        except Exception as e:
            esito = f"⚠️ Aggiornamento non riuscito: {e}"
        finally:
            self._in_corso.release()
        print(esito)
        return esito

    def _controlla(self) -> str:
        corrente = parse_versione(__version__)
        if corrente is None:
            return f"⚠️ Versione corrente '{__version__}' non riconosciuta: aggiornamento disattivato."
        releases, scadenza = leggi_releases(self.token)
        avviso = ""
        if scadenza is not None:
            giorni = (scadenza - date.today()).days
            if giorni <= AVVISO_SCADENZA_TOKEN_GIORNI:
                avviso = f" ⚠️ Il token GitHub scade il {scadenza:%d/%m/%Y} ({giorni} giorni): rinnovalo in config.ini."
        release = scegli_release(releases, corrente)
        if release is None:
            return f"✅ Nessun aggiornamento: la {__version__} è la più recente.{avviso}"
        print(f"⬇️ Scarico la {release['tag']}...")
        installa(release, self.token, self.exe)
        self.al_riavvio(release["tag"])
        return f"✅ Installata la {release['tag']}: riavvio in corso.{avviso}"

    def avvia_periodico(self, stop_event: threading.Event) -> None:
        """Thread daemon: primo controllo poco dopo l'avvio, poi a intervalli regolari."""
        def ciclo():
            attesa = PRIMO_CONTROLLO_S
            while not stop_event.wait(attesa):
                self.controlla()
                attesa = INTERVALLO_CONTROLLO_S

        threading.Thread(target=ciclo, name="aggiornamento", daemon=True).start()
