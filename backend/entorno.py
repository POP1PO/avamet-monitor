"""Lectura y escritura SEGURA del fichero .env desde la pestaña «Configuración».

Funciones puras (sin FastAPI), para poder probarlas sin red:
  - validar_campos(): comprueba token / contraseña / Admin ID / claves de EUMETSAT (opcionales) y devuelve solo los que traen valor.
  - actualizar_env(): cambia SOLO esas claves del .env; conserva comentarios, orden, otras variables y el tipo de salto
    de línea; escritura atómica y copia de seguridad .env.bak.
  - enmascarar_token(), claves_definidas_en(): ayudas para mostrar el estado sin revelar secretos.

Restricciones de los valores: se escriben SIN comillas, así que se rechazan los caracteres que python-dotenv interpretaría
(comillas, #, $, \\, espacios y saltos de línea). Es mejor decírselo al usuario que guardar algo que luego se lea distinto.
"""
import os
import re
import shutil
import threading
from pathlib import Path

CLAVES = ("AVAMET_TELEGRAM_TOKEN", "AVAMET_TELEGRAM_CLAVE", "AVAMET_ADMIN_CHAT_ID", "EUMETSAT_CONSUMER_KEY", "EUMETSAT_CONSUMER_SECRET")

_RE_TOKEN = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{20,}$")          # igual que backend/telegram.py
_RE_CLAVE = re.compile(r"^[A-Za-z0-9_.@!%^&*+=/~?,;:\-]{8,128}$")
_RE_ADMIN = re.compile(r"^-?\d{1,20}$")
_RE_EUMETSAT = re.compile(r"^[A-Za-z0-9_-]{10,100}$")            # clave y secreto de api.eumetsat.int: alfanuméricos
_RE_LINEA = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")

_CERROJO = threading.Lock()


def validar_campos(datos: dict) -> dict:
    """Devuelve {CLAVE_ENV: valor} solo con los campos NO vacíos. Lanza ValueError con un mensaje claro si alguno es inválido."""
    if not isinstance(datos, dict):
        raise ValueError("Formato de datos no válido.")
    token = str(datos.get("token") or "").strip()
    clave = str(datos.get("clave") or "").strip()
    admin = str(datos.get("admin_id") or "").strip()
    res = {}
    if token:
        if not _RE_TOKEN.match(token):
            raise ValueError("El token de Telegram no tiene el formato correcto (123456789:AAxxxxxxxxxxxxxxxxxxxxxxxx). "
                             "Cópialo completo desde @BotFather.")
        res["AVAMET_TELEGRAM_TOKEN"] = token
    if clave:
        if len(clave) < 8:
            raise ValueError("La contraseña de Telegram debe tener al menos 8 caracteres.")
        if not _RE_CLAVE.match(clave):
            raise ValueError("La contraseña tiene caracteres no permitidos (máximo 128). Usa letras, números y . _ - @ ! % ^ & * + = / ~ ? , ; : "
                             "(sin espacios, comillas, # ni $).")
        res["AVAMET_TELEGRAM_CLAVE"] = clave
    if admin:
        if not _RE_ADMIN.match(admin):
            raise ValueError("El Admin ID debe ser un número (solo dígitos). Envía /id a tu bot o escribe a @userinfobot para saberlo.")
        res["AVAMET_ADMIN_CHAT_ID"] = admin
    for campo, env, nombre in (("eumetsat_key", "EUMETSAT_CONSUMER_KEY", "La clave (Consumer Key)"),
                               ("eumetsat_secret", "EUMETSAT_CONSUMER_SECRET", "El secreto (Consumer Secret)")):
        v = str(datos.get(campo) or "").strip()
        if v:
            if not _RE_EUMETSAT.match(v):
                raise ValueError(f"{nombre} de EUMETSAT no tiene un formato válido (10-100 letras, números, _ o -). Cópialo completo de https://api.eumetsat.int/api-key/")
            res[env] = v
    if not res:
        raise ValueError("No has indicado ningún valor nuevo.")
    return res


def enmascarar_token(token: str) -> str:
    """'123456789:AAxxxx…WxyZ' -> '123456789:••••••••WxyZ' (el número del bot no es secreto; del resto solo se ven 4 caracteres)."""
    if not token:
        return ""
    num, sep, resto = token.partition(":")
    if not sep:
        return "••••••••"
    return f"{num}:••••••••{resto[-4:]}"


def enmascarar_clave(valor: str) -> str:
    """'AbCdEfGh1234…' -> 'AbCd••••' (solo los 4 primeros caracteres; el secreto NUNCA se muestra)."""
    return (valor[:4] + "••••••••") if valor else ""


def _lineas(texto: str) -> list[str]:
    ls = re.split(r"\r\n|\n|\r", texto)
    if ls and ls[-1] == "":
        ls.pop()
    return ls


def _leer(ruta: Path) -> str:
    return ruta.read_bytes().decode("utf-8-sig") if ruta.is_file() else ""     # utf-8-sig: tolera el BOM del Bloc de notas


def claves_definidas_en(ruta: Path, claves=CLAVES) -> set:
    """Claves de `claves` que están DEFINIDAS (línea activa, no comentada) en ese fichero."""
    try:
        texto = _leer(Path(ruta))
    except OSError:
        return set()
    return {m.group(1) for ln in _lineas(texto) if (m := _RE_LINEA.match(ln)) and m.group(1) in claves}


def actualizar_env(ruta, cambios: dict) -> Path:
    """Actualiza/añade SOLO las claves de `cambios` en el .env `ruta`. Devuelve la ruta escrita.

    - La línea activa existente de cada clave se sustituye en su sitio; si hay duplicados, se deja solo la primera.
    - Las líneas comentadas (# CLAVE=...) y cualquier otra variable no se tocan.
    - Si la clave no existe, se añade al final.
    - Se conserva el salto de línea del fichero (CRLF en Windows); escritura atómica; copia previa en .env.bak.
    """
    ruta = Path(ruta)
    for k, v in cambios.items():
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k) or re.search(r"[\r\n]", str(v)):
            raise ValueError("Clave o valor no válidos para el .env.")
    with _CERROJO:
        texto = _leer(ruta)
        nl = "\r\n" if "\r\n" in texto else ("\n" if "\n" in texto else ("\r\n" if os.name == "nt" else "\n"))
        salida, vistos = [], set()
        for ln in _lineas(texto):
            m = _RE_LINEA.match(ln)
            if m and m.group(1) in cambios:
                k = m.group(1)
                if k in vistos:
                    continue                                   # duplicado: se elimina
                vistos.add(k)
                salida.append(f"{k}={cambios[k]}")
            else:
                salida.append(ln)
        for k, v in cambios.items():
            if k not in vistos:
                salida.append(f"{k}={v}")
        ruta.parent.mkdir(parents=True, exist_ok=True)
        if ruta.is_file():
            try:
                shutil.copy2(ruta, ruta.with_name(ruta.name + ".bak"))
            except OSError:
                pass                                           # sin copia de seguridad no se bloquea el guardado
        tmp = ruta.with_name(ruta.name + ".tmp")
        tmp.write_bytes((nl.join(salida) + nl).encode("utf-8"))
        try:
            os.chmod(tmp, 0o600)                               # en Linux: solo el propietario; en Windows no hace nada útil
        except OSError:
            pass
        os.replace(tmp, ruta)
    return ruta
