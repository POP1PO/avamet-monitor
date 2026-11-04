"""Autenticación por token Bearer para los endpoints que cambian el estado de la app.

Protege /api/shutdown y /api/ubicacion. Sin esto, con HOST=0.0.0.0 (Raspberry Pi) cualquiera en la
red podría apagar la app o mover la estación central.

Token (mínimo 16 caracteres), por orden de prioridad:
  1. Variable de entorno AVAMET_API_TOKEN (o línea en el fichero .env)
  2. Fichero config.API_TOKEN_PATH (por defecto api_token.txt en la carpeta de datos)
  3. Si no existe ninguno, se genera uno aleatorio y se guarda en ese fichero.

Uso:  curl -X POST -H "Authorization: Bearer <token>" http://HOST:8000/api/shutdown
"""
from __future__ import annotations

import hmac
import os
import secrets
import time
from pathlib import Path

from . import config

LONGITUD_MIN = 16
_TOKEN: str | None = None
_FALLOS: dict[str, list[float]] = {}


def obtener_token() -> str:
    global _TOKEN
    if _TOKEN:
        return _TOKEN
    t = (os.getenv("AVAMET_API_TOKEN") or "").strip()
    if t:
        if len(t) >= LONGITUD_MIN:
            _TOKEN = t
            return t
        print(f"⚠️  AVAMET_API_TOKEN ignorado: debe tener al menos {LONGITUD_MIN} caracteres.")
    ruta = Path(config.API_TOKEN_PATH)
    try:
        t = ruta.read_text(encoding="utf-8").strip()
        if len(t) >= LONGITUD_MIN:
            _TOKEN = t
            return t
    except FileNotFoundError:
        pass
    except OSError as e:
        print(f"⚠️  No se pudo leer {ruta} ({e})")
    t = secrets.token_urlsafe(32)
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # solo el propietario (en Linux/RPi)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(t + "\n")
        print(f"🔐 Token de API generado y guardado en: {ruta}")
    except OSError as e:
        # Sin fichero el token solo existe en memoria: hay que mostrarlo o nadie podría usarlo.
        print(f"⚠️  No se pudo guardar el token ({e}). Token de esta sesión: {t}")
    _TOKEN = t
    return t


def comprobar(authorization: str | None, cliente: str = "?", ahora: float | None = None) -> str:
    """Devuelve "OK", "NO_AUTORIZADO" o "BLOQUEADO" (demasiados fallos recientes de ese cliente)."""
    ahora = time.time() if ahora is None else ahora
    fallos = [t for t in _FALLOS.get(cliente, []) if ahora - t < config.API_VENTANA_FALLOS_S]
    if len(fallos) >= config.API_MAX_FALLOS:
        _FALLOS[cliente] = fallos
        return "BLOQUEADO"
    ok = False
    if authorization:
        partes = authorization.split(None, 1)
        if len(partes) == 2 and partes[0].lower() == "bearer":
            ok = hmac.compare_digest(partes[1].strip().encode("utf-8"), obtener_token().encode("utf-8"))
    if ok:
        _FALLOS.pop(cliente, None)
        return "OK"
    fallos.append(ahora)
    _FALLOS[cliente] = fallos
    return "NO_AUTORIZADO"
