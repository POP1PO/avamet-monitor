"""Capa «Nubes IR» del mapa: imagen Meteosat IR 10,8 µm del WMS público de EUMETView (EUMETSAT). SOLO PARA MOSTRAR.

No necesita claves ni entra en el semáforo ni en el informe del satélite. El navegador pide las teselas directamente a EUMETView
(L.tileLayer.wms); este módulo solo averigua la HORA DEL ÚLTIMO BARRIDO, que hay que mandar como `time` en cada tesela:

  Comprobado el 3-nov-2026: un GetMap sin `time` (o con time=current) devuelve teselas de la caché del servidor con fechas
  mezcladas (cabecera Last-Modified del 23-jul, 1-ago, 19-ago y del día, una al lado de otra), y el mapa sale cortado en bandas
  con nubes que no existen. Con time=<default de la Dimension time> todas las teselas son del mismo barrido.

La hora se lee del GetCapabilities de la capa sola (servicio virtual de GeoServer /geoserver/<espacio>/<capa>/wms, ~7 kB) y se
guarda CACHE_S segundos. Las TESELAS también se piden a ese servicio virtual (layers=<capa> sin espacio):

  Comprobado el 3-nov-2026 a las 20:40 UTC: el WMS global (/geoserver/wms, layers=msg_fes:ir108) devolvía HTTP 500 a TODA petición
  con time (también con barridos que una hora antes funcionaban), mientras el servicio virtual de la capa devolvía 200 con la
  imagen correcta y sin cortes, con time y sin él.

Ninguna función lanza excepciones hacia fuera.
"""
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

WMS = "https://view.eumetsat.int/geoserver/wms"
RAIZ = "https://view.eumetsat.int/geoserver"
CAPA = os.getenv("AVAMET_NUBES_IR_CAPA", "msg_fes:ir108")
CACHE_S = int(os.getenv("AVAMET_NUBES_IR_CACHE_S", "300"))     # la cadencia de SEVIRI es de 15 min
PASO_MIN = 15                                                    # separación entre barridos (PT15M en la Dimension time)
N_FOTOGRAMAS = max(1, min(24, int(os.getenv("AVAMET_NUBES_IR_FOTOGRAMAS", "8"))))   # animación: por defecto 8 = últimas 2 h
TIMEOUT = (4, 10)
_UA = {"User-Agent": "AVAMET-Monitor/1.0 (capa de nubes del mapa)"}

_CERROJO = threading.Lock()
_CACHE: dict = {}          # {"ts": instante de la consulta, "res": respuesta}


def wms_de_capa(capa: str) -> tuple | None:
    """("https://.../geoserver/<espacio>/<capa>/wms", "<capa>") del servicio virtual de UNA capa ("espacio:nombre").
    None si el nombre no lleva espacio."""
    partes = (capa or "").strip().split(":")
    if len(partes) != 2 or not all(p.strip() for p in partes):
        return None
    esp, nombre = partes[0].strip(), partes[1].strip()
    return f"{RAIZ}/{esp}/{nombre}/wms", nombre


def url_capabilities_capa(capa: str) -> str | None:
    """GetCapabilities de UNA capa ("espacio:nombre"). None si el nombre no lleva espacio."""
    w = wms_de_capa(capa)
    return f"{w[0]}?service=WMS&version=1.3.0&request=GetCapabilities" if w else None


def tiempo_por_defecto(xml: str) -> str | None:
    """Atributo default de la primera <Dimension name="time"> (p. ej. "2026-11-03T19:00:00Z"); None si no hay."""
    for m in re.finditer(r"<Dimension\b[^>]*>", xml or ""):
        if not re.search(r'\bname\s*=\s*"time"', m.group(0), re.IGNORECASE):
            continue
        d = re.search(r'\bdefault\s*=\s*"([^"]+)"', m.group(0))
        return d.group(1).strip() or None if d else None
    return None


def fotogramas(tiempo: str | None, n: int = N_FOTOGRAMAS) -> list:
    """Horas de los últimos `n` barridos, del más antiguo al más nuevo, acabando en `tiempo` ("2026-11-03T21:45:00Z").
    Comprobado el 4-nov-2026: el servicio de la capa da imagen distinta para cada barrido de las últimas 6 h como mínimo.
    [] si `tiempo` no se entiende."""
    try:
        fin = datetime.strptime((tiempo or "").strip(), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return []
    return [(fin - timedelta(minutes=PASO_MIN * k)).strftime("%Y-%m-%dT%H:%M:%SZ") for k in range(max(1, n) - 1, -1, -1)]


def _descargar(url: str) -> str:
    r = requests.get(url, timeout=TIMEOUT, headers=_UA)
    r.raise_for_status()
    return r.text


def obtener(ahora: float | None = None, descargar=None) -> dict:
    """{"capa", "wms", "tiempo", "fotogramas", "error"} para L.tileLayer.wms: servicio y nombre de capa a los que pedir las teselas y hora del
    último barrido publicado (cacheada CACHE_S s). Si la consulta falla se devuelve la última hora conocida (con el error) o None."""
    ahora = time.time() if ahora is None else ahora
    descargar = descargar or _descargar
    with _CERROJO:
        c = _CACHE.get("res")
        if c is not None and ahora - _CACHE.get("ts", 0) < CACHE_S:
            return dict(c)
        previo = c.get("tiempo") if c else None
        w = wms_de_capa(CAPA)
        res = {"capa": w[1] if w else CAPA, "wms": w[0] if w else WMS, "tiempo": None, "fotogramas": [], "error": None}
        url = url_capabilities_capa(CAPA)
        try:
            if not url:
                raise ValueError(f"nombre de capa no válido: {CAPA!r}")
            t = tiempo_por_defecto(descargar(url))
            if not t:
                raise ValueError("el servidor no indica la hora del último barrido")
            res["tiempo"] = t
            res["fotogramas"] = fotogramas(t)
        except Exception as e:                       # noqa: BLE001 - nunca debe romper la API
            res["tiempo"] = previo
            res["fotogramas"] = fotogramas(previo)
            res["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        _CACHE.update(ts=ahora, res=res)
        return dict(res)


def limpiar_cache() -> None:
    with _CERROJO:
        _CACHE.clear()
