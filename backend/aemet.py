"""Observaciones de las estaciones oficiales de AEMET (AEMET OpenData) más cercanas a la estación de referencia.

- La clave (AEMET_API_KEY) se lee SOLO del entorno (.env de la carpeta de datos) y viaja únicamente en la cabecera
  HTTP «api_key». Nunca se escribe en mensajes de error, registros ni respuestas de la API local.
- AEMET responde en dos pasos: primero un JSON con el campo «datos» (una URL) y luego hay que descargar esa URL.
- Información meramente orientativa; no entra en el semáforo ni en las alarmas.
- Atribución exigida por la nota legal de AEMET (https://www.aemet.es/es/nota_legal): «© AEMET» / «Fuente: AEMET».
"""
import os
import threading
import time
from math import radians, sin, cos, atan2, sqrt

import requests

BASE = "https://opendata.aemet.es/opendata/api"
RUTA_OBSERVACION = "/observacion/convencional/todas"
CACHE_OK_S = int(os.getenv("AVAMET_AEMET_CACHE_S", "600"))        # AEMET publica datos horarios: 10 min bastan (límite: 40 consultas/min)
CACHE_ERROR_S = 120                                                 # tras un fallo no se insiste antes de 2 min
TIMEOUT_S = 25

_cerrojo = threading.Lock()
_cache = {"t": 0.0, "datos": None, "error": None}


class ErrorAemet(Exception):
    """Error legible para el usuario (nunca contiene la clave)."""


def clave() -> str:
    return os.getenv("AEMET_API_KEY", "").strip()


def configurado() -> bool:
    return bool(clave())


def limpiar_cache() -> None:
    with _cerrojo:
        _cache.update(t=0.0, datos=None, error=None)


def _json(r: requests.Response):
    try:
        return r.json()
    except ValueError:
        raise ErrorAemet("AEMET devolvió una respuesta que no es JSON.")


def _pedir(url: str, con_clave: bool):
    cab = {"Accept": "application/json"}
    if con_clave:
        cab["api_key"] = clave()
    try:
        r = requests.get(url, headers=cab, timeout=TIMEOUT_S)
    except requests.RequestException as e:
        raise ErrorAemet(f"No se pudo contactar con AEMET ({type(e).__name__}).")
    if r.status_code in (401, 403):
        raise ErrorAemet("AEMET rechazó la clave: puede estar mal copiada o haber caducado (valen 3 meses). Pide una nueva en opendata.aemet.es.")
    if r.status_code == 429:
        raise ErrorAemet("AEMET indica demasiadas consultas; se reintentará más tarde.")
    if r.status_code != 200:
        raise ErrorAemet(f"AEMET respondió con el código {r.status_code}.")
    return _json(r)


def descargar_observaciones() -> list:
    """Dos pasos: 1) pide la URL temporal; 2) descarga los datos. Devuelve la lista de observaciones tal cual."""
    paso1 = _pedir(BASE + RUTA_OBSERVACION, True)
    if not isinstance(paso1, dict) or int(paso1.get("estado", 0) or 0) != 200 or not paso1.get("datos"):
        desc = paso1.get("descripcion") if isinstance(paso1, dict) else None
        raise ErrorAemet("AEMET no dio los datos: " + (str(desc)[:120] if desc else "respuesta inesperada") + ".")
    url = str(paso1["datos"])
    if not url.startswith("https://opendata.aemet.es/"):          # solo se descarga de AEMET
        raise ErrorAemet("AEMET devolvió una dirección de datos inesperada.")
    datos = _pedir(url, False)
    if not isinstance(datos, list):
        raise ErrorAemet("AEMET devolvió un formato de datos inesperado.")
    return datos


def _haversine(lat1, lon1, lat2, lon2):
    p1, p2 = radians(lat1), radians(lat2)
    a = sin((p2 - p1) / 2) ** 2 + cos(p1) * cos(p2) * sin(radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * atan2(sqrt(a), sqrt(1 - a))


def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None                                    # descarta NaN


def ultima_por_estacion(registros: list) -> dict:
    """Se queda con la observación más reciente (campo fint) de cada estación (idema)."""
    res = {}
    for r in registros:
        if not isinstance(r, dict):
            continue
        idema, fint = r.get("idema"), str(r.get("fint") or "")
        if not idema or not fint:
            continue
        if idema not in res or fint > str(res[idema].get("fint") or ""):
            res[idema] = r
    return res


def cercanas_de(registros: list, lat: float, lon: float, radio_km: float = 60.0, n: int = 5) -> list:
    """Función pura: las n estaciones de AEMET más próximas (dentro de radio_km) con su última observación."""
    salida = []
    for idema, r in ultima_por_estacion(registros).items():
        la, lo = _num(r.get("lat")), _num(r.get("lon"))
        if la is None or lo is None:
            continue
        d = _haversine(lat, lon, la, lo)
        if d > radio_km:
            continue
        vv = _num(r.get("vv"))
        salida.append({
            "idema": idema, "nombre": str(r.get("ubi") or idema), "distancia_km": round(d, 1),
            "fint_utc": str(r.get("fint")),
            "lluvia_mm": _num(r.get("prec")),                       # mm acumulados en la última hora (según AEMET)
            "temp_c": _num(r.get("ta")), "humedad": _num(r.get("hr")),
            "viento_kmh": None if vv is None else round(vv * 3.6, 1),   # AEMET da m/s
            "dir_viento": _num(r.get("dv")),
        })
    salida.sort(key=lambda e: e["distancia_km"])
    return salida[:n]


def cercanas(lat: float, lon: float, radio_km: float = 60.0, n: int = 5) -> list:
    """Con caché compartida (los datos de AEMET son los mismos para todas las posiciones). Lanza ErrorAemet."""
    if not configurado():
        raise ErrorAemet("Falta la clave de AEMET.")
    ahora = time.time()
    with _cerrojo:
        if _cache["datos"] is not None and ahora - _cache["t"] < CACHE_OK_S:
            datos = _cache["datos"]
        elif _cache["error"] and ahora - _cache["t"] < CACHE_ERROR_S:
            raise ErrorAemet(_cache["error"])
        else:
            datos = None
    if datos is None:
        try:
            datos = descargar_observaciones()
        except ErrorAemet as e:
            with _cerrojo:
                _cache.update(t=time.time(), datos=None, error=str(e))
            raise
        with _cerrojo:
            _cache.update(t=time.time(), datos=datos, error=None)
    return cercanas_de(datos, lat, lon, radio_km, n)
