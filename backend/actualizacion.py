"""Aviso de versión nueva (NO se actualiza sola: solo avisa; la persona descarga y sustituye).

Cómo funciona: cada compilación del CI escribe `backend/build_info.py` con el nº de ejecución del flujo de GitHub (BUILD).
La release «ultima» de GitHub lleva ese mismo número en su título («Última versión (nº N)»). La app lo lee con la API
pública de GitHub (sin claves), lo compara con su BUILD y, si es mayor, avisa con el enlace de descarga.

- Desde el código fuente (sin build_info.py) no hay número de versión y la comprobación queda desactivada.
- Se puede desactivar con AVAMET_COMPROBAR_ACTUALIZACIONES=0. Se consulta una vez cada vez que arranca la app (si falla, se reintenta como mucho cada 30 min mientras se abra la página).
- Ninguna función lanza excepciones hacia fuera: un fallo de red nunca debe afectar a la vigilancia.
"""
import os
import re
import sys
import threading
import time

import requests

try:
    from . import build_info
    BUILD = int(build_info.BUILD)
    COMMIT = str(getattr(build_info, "COMMIT", ""))
except Exception:
    BUILD = None
    COMMIT = ""

REPO = os.getenv("AVAMET_REPO", "POP1PO/avamet-monitor")
URL_API = f"https://api.github.com/repos/{REPO}/releases/tags/ultima"
URL_RELEASES = f"https://github.com/{REPO}/releases/tag/ultima"
CACHE_OK_S = float('inf')   # una consulta por arranque: el resultado vale hasta que se cierre la app
CACHE_FALLO_S = 30 * 60
_UA = {"User-Agent": "AVAMET-Monitor-updatecheck", "Accept": "application/vnd.github+json"}

_lock = threading.Lock()
_cache = {"t": 0.0, "dato": None, "ttl": 0.0}


def activa() -> bool:
    return BUILD is not None and os.getenv("AVAMET_COMPROBAR_ACTUALIZACIONES", "1").strip() != "0"


def numero_de_titulo(titulo: str):
    """«Última versión (nº 57)» -> 57. None si no hay número."""
    m = re.search(r"n[ºo°]\s*(\d+)", titulo or "", re.IGNORECASE)
    return int(m.group(1)) if m else None


def elegir_descarga(assets, plataforma: str = sys.platform):
    """URL del fichero de la release que corresponde a este sistema (zip en Windows, tar.gz en Linux)."""
    sufijo = ".zip" if plataforma.startswith("win") else ".tar.gz"
    for a in assets or []:
        nombre = str(a.get("name", ""))
        if nombre.endswith(sufijo):
            return a.get("browser_download_url")
    return None


def evaluar(release: dict, build_actual, plataforma: str = sys.platform) -> dict:
    """Compara la release de GitHub con la versión instalada. Función pura (se prueba sin red)."""
    ultima = numero_de_titulo(release.get("name", ""))
    if build_actual is None or ultima is None:
        return {"activo": build_actual is not None, "disponible": False, "actual": build_actual, "ultima": ultima,
                "error": None if ultima is not None else "No se pudo leer el número de la última versión"}
    return {"activo": True, "disponible": ultima > build_actual, "actual": build_actual, "ultima": ultima,
            "url": release.get("html_url") or URL_RELEASES,
            "descarga": elegir_descarga(release.get("assets"), plataforma), "error": None}


def consultar(forzar: bool = False) -> dict:
    """Estado de actualización (con caché). Nunca lanza excepciones."""
    if not activa():
        return {"activo": False, "disponible": False, "actual": BUILD, "ultima": None, "error": None}
    with _lock:
        if not forzar and _cache["dato"] is not None and time.time() - _cache["t"] < _cache["ttl"]:
            return _cache["dato"]
    try:
        r = requests.get(URL_API, headers=_UA, timeout=8)
        r.raise_for_status()
        dato = evaluar(r.json(), BUILD)
        ttl = CACHE_OK_S
    except Exception as e:
        dato = {"activo": True, "disponible": False, "actual": BUILD, "ultima": None,
                "error": f"No se pudo comprobar ({type(e).__name__})"}
        ttl = CACHE_FALLO_S
    with _lock:
        _cache.update(t=time.time(), dato=dato, ttl=ttl)
    return dato
