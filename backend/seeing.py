"""Seeing, transparencia y nubosidad nocturnos del pronóstico de 7Timer! (producto "astro").

Es un dato EXTERNO (modelo de pronóstico), no sale de las estaciones AVAMET ni de los METAR, y NO entra en el semáforo de
seguridad. Escalas (wiki de 7Timer): seeing 1-8 (1 = menos de 0,5", 8 = más de 2,5"); transparencia 1-8 (1 = menos de 0,3,
8 = más de 1 magnitud por masa de aire); nubosidad 1-9 (1 = 0-6 %, 9 = 94-100 %). -9999 = sin dato.
No se ha comprobado cuánto acierta este pronóstico en cada emplazamiento.
"""
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

from . import diagnostico

URL = os.getenv("AVAMET_SEEING_URL", "https://www.7timer.info/bin/api.pl")
REFRESCO_S = int(os.getenv("AVAMET_SEEING_REFRESCO_S", "3600"))      # una consulta por hora como máximo
REINTENTO_S = int(os.getenv("AVAMET_SEEING_REINTENTO_S", "600"))     # si falla, se reintenta a los 10 min
MAX_HORAS_NOCHE = 9
_CERROJO = threading.Lock()
_CACHE: dict = {}          # (lat, lon) redondeados -> {"ts": instante de la última consulta, "datos": resultado|None, "error": str|None}


def _user_agent() -> str:
    contacto = os.getenv("AVAMET_CONTACTO", "").strip()
    base = "AVAMET-Monitor/1.0 (observatorio astronomico"
    return f"{base}; {contacto})" if contacto else f"{base})"


def interpretar(datos: dict, ahora: float) -> dict:
    """JSON de 7Timer -> resumen de la noche (horas locales 21:00-05:59 que aún no han pasado). Lanza ValueError si no hay nada útil."""
    init = datetime.strptime(str(datos["init"]), "%Y%m%d%H").replace(tzinfo=timezone.utc)
    ahora_dt = datetime.fromtimestamp(ahora)               # hora local del equipo
    see, tra, nub = [], [], []
    for p in datos.get("dataseries", []):
        t_utc = init + timedelta(hours=int(p["timepoint"]))
        t_loc = t_utc.astimezone()                          # hora local del equipo
        if t_loc.replace(tzinfo=None) < ahora_dt - timedelta(hours=1): continue
        if not (t_loc.hour >= 21 or t_loc.hour <= 5): continue
        if len(see) >= MAX_HORAS_NOCHE: break
        s, t, c = p.get("seeing", -9999), p.get("transparency", -9999), p.get("cloudcover", -9999)
        if isinstance(s, int) and 1 <= s <= 8: see.append(s)
        if isinstance(t, int) and 1 <= t <= 8: tra.append(t)
        if isinstance(c, int) and 1 <= c <= 9: nub.append(c)
    if not see:
        raise ValueError("7Timer no devuelve horas de noche con seeing")
    def resumen(v): return {"media": round(sum(v) / len(v), 1), "peor": max(v)} if v else None
    return {"seeing": resumen(see), "transparencia": resumen(tra), "nubosidad": resumen(nub), "horas": len(see),
            "emitido_hace_h": int((ahora - init.timestamp()) // 3600), "fuente": "7Timer! (producto astro)"}


def _descargar(lat: float, lon: float) -> dict:
    r = requests.get(URL, params={"lon": f"{lon:.3f}", "lat": f"{lat:.3f}", "product": "astro", "output": "json",
                                  "ac": 0, "unit": "metric", "tzshift": 0},
                     headers={"User-Agent": _user_agent()}, timeout=12)
    r.raise_for_status()
    return r.json()


def obtener(lat: float, lon: float, ahora: float | None = None) -> dict:
    """{"datos": resumen|None, "error": texto|None}. Hace como máximo una petición por hora y emplazamiento; si falla, devuelve el último
    dato bueno (si lo hay) junto con el error y reintenta a los 10 min. Nunca lanza excepciones."""
    ahora = time.time() if ahora is None else ahora
    clave = (round(lat, 3), round(lon, 3))
    with _CERROJO:
        e = _CACHE.get(clave)
        espera = REFRESCO_S if (e and e["error"] is None) else REINTENTO_S
        if e and ahora - e["ts"] < espera:
            return {"datos": e["datos"], "error": e["error"]}
    try:
        datos = interpretar(_descargar(lat, lon), ahora)
        nuevo = {"ts": ahora, "datos": datos, "error": None}
    except Exception as ex:
        tipo, detalle = diagnostico.registrar_excepcion("SEEING", ex)
        previo = e["datos"] if e else None
        nuevo = {"ts": ahora, "datos": previo, "error": f"{type(ex).__name__}: {ex}"}
    with _CERROJO:
        _CACHE[clave] = nuevo
    return {"datos": nuevo["datos"], "error": nuevo["error"]}
