"""Firma delle release: l'aggiornamento automatico installa un exe solo se è firmato con la nostra chiave privata.

La chiave privata non va mai su GitHub: resta sul PC di chi pubblica (e su una chiavetta di backup).
È una chiave diversa da quella di offerte-gen, così compromettere un progetto non compromette l'altro.

    uv run --with cryptography python tools/firma.py crea-chiavi       una volta: crea la coppia di chiavi
    uv run --with cryptography python tools/firma.py firma v3.1.0      dopo ogni release: firma l'exe e carica il .sig
    uv run --with cryptography python tools/firma.py pubblica          ristampa la chiave pubblica

`crea-chiavi` e `pubblica` stampano le due costanti da incollare in `cmr_renamer/aggiornamento.py`.
"""

import base64
import os
import subprocess
import sys
import tempfile

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

DEFAULT_KEY = os.path.join(os.path.expanduser("~"), ".config", "cmr-renamer", "firma", "chiave-privata.pem")
KEY_BITS = 3072
ASSET = "cmr-renamer.exe"


def _costanti(public):
    """La chiave pubblica come costanti Python: la verifica nell'exe è in Python puro, senza `cryptography`."""
    n = public.public_numbers()
    esadecimale = format(n.n, "x")
    righe = "\n".join(f'    "{esadecimale[i:i + 64]}"' for i in range(0, len(esadecimale), 64))
    return f"_CHIAVE_PUBBLICA_N = int(\n{righe},\n    16,\n)\n_CHIAVE_PUBBLICA_E = {n.e}"


def _load(path):
    with open(path, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def make_keys(path=DEFAULT_KEY):
    """Crea la coppia di chiavi (mai sopra una esistente); ritorna la chiave pubblica come costanti."""
    if os.path.exists(path):
        raise FileExistsError(f"{path} esiste già: la chiave non si rifà (gli exe installati conoscono solo quella)")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=KEY_BITS)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # leggibile solo dal proprietario
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    return _costanti(key.public_key())


def public_constants(path=DEFAULT_KEY):
    return _costanti(_load(path).public_key())


def sign_file(path, key_path=DEFAULT_KEY):
    """FILE.sig accanto al file: base64 della firma RSA PKCS#1 v1.5 del suo SHA-256."""
    with open(path, "rb") as f:
        data = f.read()
    signature = _load(key_path).sign(data, padding.PKCS1v15(), hashes.SHA256())
    sig_path = path + ".sig"
    with open(sig_path, "w", encoding="ascii") as f:
        f.write(base64.b64encode(signature).decode() + "\n")
    return sig_path


def sign_release(tag, key_path=DEFAULT_KEY):
    """Scarica l'exe di una release pubblicata, lo firma e carica il .sig accanto."""
    with tempfile.TemporaryDirectory() as folder:
        subprocess.run(["gh", "release", "download", tag, "--pattern", ASSET, "--dir", folder], check=True)
        sig = sign_file(os.path.join(folder, ASSET), key_path)
        subprocess.run(["gh", "release", "upload", tag, sig, "--clobber"], check=True)
    return ASSET + ".sig"


def main(argv):
    if argv[:1] == ["crea-chiavi"]:
        print(make_keys())
    elif len(argv) == 2 and argv[0] == "firma":
        print("caricata", sign_release(argv[1]))
    elif argv[:1] == ["pubblica"]:
        print(public_constants())
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
