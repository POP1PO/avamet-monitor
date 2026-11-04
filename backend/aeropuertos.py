import os
import threading
import time
import requests
from math import radians, sin, cos, atan2, sqrt

from . import diagnostico

AEROPUERTOS = {
    "LECO": {"nombre": "A Coruña", "lat": 43.3021, "lon": -8.3773},
    "LEAB": {"nombre": "Albacete", "lat": 38.9485, "lon": -1.8635},
    "LEAL": {"nombre": "Alicante-Elche", "lat": 38.2822, "lon": -0.5581},
    "LEAM": {"nombre": "Almería", "lat": 36.8439, "lon": -2.3701},
    "LEAS": {"nombre": "Asturias", "lat": 43.5636, "lon": -6.0346},
    "LEBZ": {"nombre": "Badajoz", "lat": 38.8913, "lon": -6.8213},
    "LEBL": {"nombre": "Barcelona-El Prat", "lat": 41.2971, "lon": 2.0785},
    "LEBB": {"nombre": "Bilbao", "lat": 43.3011, "lon": -2.9106},
    "LEBG": {"nombre": "Burgos", "lat": 42.3576, "lon": -3.6208},
    "LECH": {"nombre": "Castellón", "lat": 40.2139, "lon": 0.0736},
    "LEBA": {"nombre": "Córdoba", "lat": 37.8420, "lon": -4.8489},
    "LEGE": {"nombre": "Girona", "lat": 41.9010, "lon": 2.7605},
    "LEGR": {"nombre": "Granada-Jaén", "lat": 37.1887, "lon": -3.7774},
    "LEIB": {"nombre": "Ibiza", "lat": 38.8729, "lon": 1.3731},
    "LEJR": {"nombre": "Jerez", "lat": 36.7446, "lon": -6.0601},
    "LELN": {"nombre": "León", "lat": 42.5890, "lon": -5.6556},
    "LEDA": {"nombre": "Lleida-Alguaire", "lat": 41.7281, "lon": 0.5353},
    "LERJ": {"nombre": "Logroño", "lat": 42.4610, "lon": -2.3206},
    "LEMD": {"nombre": "Madrid-Barajas", "lat": 40.4719, "lon": -3.5626},
    "LEMG": {"nombre": "Málaga-Costa del Sol", "lat": 36.6749, "lon": -4.4991},
    "LEMH": {"nombre": "Menorca", "lat": 39.8626, "lon": 4.2186},
    "LEML": {"nombre": "Melilla", "lat": 35.2798, "lon": -2.9563},
    "LEPA": {"nombre": "Palma de Mallorca", "lat": 39.5517, "lon": 2.7388},
    "LEPP": {"nombre": "Pamplona", "lat": 42.7700, "lon": -1.6463},
    "LERS": {"nombre": "Reus", "lat": 41.1474, "lon": 1.1672},
    "LESO": {"nombre": "San Sebastián", "lat": 43.3564, "lon": -1.7906},
    "LEZL": {"nombre": "Sevilla", "lat": 37.4180, "lon": -5.8987},
    "LEST": {"nombre": "Santiago", "lat": 42.8963, "lon": -8.4151},
    "LETL": {"nombre": "Teruel", "lat": 40.4111, "lon": -1.2183},
    "LEVC": {"nombre": "Valencia", "lat": 39.4893, "lon": -0.4816},
    "LEVT": {"nombre": "Vitoria", "lat": 42.8828, "lon": -2.7244},
    "LEZG": {"nombre": "Zaragoza", "lat": 41.6662, "lon": -1.0415},
    "GCLP": {"nombre": "Gran Canaria", "lat": 27.9319, "lon": -15.3866},
    "GCTS": {"nombre": "Tenerife Sur", "lat": 28.0445, "lon": -16.5725},
    "GCXO": {"nombre": "Tenerife Norte", "lat": 28.4827, "lon": -16.3415},
}

def _haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = radians(lat1), radians(lat2)
    dp = radians(lat2 - lat1)
    dl = radians(lon2 - lon1)
    a = sin(dp/2)**2 + cos(p1)*cos(p2)*sin(dl/2)**2
    return 2 * R * atan2(sqrt(a), sqrt(1 - a))

def _safe_float(val, default=None):
    """Convierte a float. 'VRB' (viento variable), vacío o ausente => None (NUNCA 0.0: 0° sería Norte y 0 °C
    parecería una temperatura real)."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return default

def buscar_aeropuertos_cercanos(lat: float, lon: float, radio_km: float = 120.0) -> list:
    cercanos = []
    for icao, info in AEROPUERTOS.items():
        d = _haversine(lat, lon, info["lat"], info["lon"])
        if d <= radio_km:
            cercanos.append({"icao": icao, "nombre": info["nombre"], "distancia_km": round(d, 1), "lat": info["lat"], "lon": info["lon"]})
    cercanos.sort(key=lambda x: x["distancia_km"])
    return cercanos

COVER_TRAD = {"FEW": "Pocas nubes", "SCT": "Nubes dispersas", "BKN": "Nublado (Fragmentado)", "OVC": "Cielo Cubierto", "SKC": "Despejado", "CLR": "Despejado", "NSC": "Sin nubes significativas"}

def traducir_nubosidad(clouds: list) -> dict:
    niveles = {"baja": [], "media": [], "alta": []}
    for c in clouds:
        cover = c.get("cover", "")
        base_ft = c.get("base", 0)
        if not cover or cover in ["SKC", "CLR", "NSC"]: continue
        texto = COVER_TRAD.get(cover, cover)
        base_m = base_ft * 0.3048
        if base_ft: texto += f" a {base_ft} ft ({base_m:.0f} m)"
        if base_m < 2000: niveles["baja"].append(texto)
        elif base_m < 6000: niveles["media"].append(texto)
        else: niveles["alta"].append(texto)
    return niveles

def traducir_fenomenos(wx: str) -> str:
    if not wx: return "Ninguno"
    trad = {"RA": "Lluvia", "DZ": "Llovizna", "SN": "Nieve", "SG": "Cinarra", "PL": "Hielo granulado", "GR": "Granizo", "GS": "Granizo pequeño", "BR": "Neblina", "FG": "Niebla", "FU": "Humo", "VA": "Ceniza volcánica", "DU": "Polvo", "SA": "Arena", "HZ": "Calima", "TS": "Tormenta", "SH": "Chubascos", "FZ": "Engelante", "SQ": "Turbonadas", "FC": "Tornado/Tromba"}
    partes = []
    for code in ["RA", "DZ", "SN", "SG", "PL", "GR", "GS", "BR", "FG", "FU", "VA", "DU", "SA", "HZ", "TS", "SH", "FZ", "SQ", "FC"]:
        if code in wx: partes.append(trad[code])
    return ", ".join(partes) if partes else wx

def _user_agent() -> str:
    """User-Agent para aviationweather.gov. El contacto es OPCIONAL (AVAMET_CONTACTO, p. ej. un correo): no se
    inventa ninguno. B3: antes iba fijo un correo de ejemplo."""
    contacto = os.getenv("AVAMET_CONTACTO", "").strip()
    base = "AVAMET-Monitor/1.0 (observatorio astronomico"
    return f"{base}; {contacto})" if contacto else f"{base})"

# ---- Caché del último METAR bueno de cada aeropuerto -------------------------------------------------------------------
# Si aviationweather.gov falla, tarda o no devuelve un aeropuerto, se envía el último dato bueno (marcado como
# "desactualizado" y con su edad) en vez de un objeto vacío: así la interfaz no se queda en blanco. Un METAR se emite cada
# 30-60 min, así que un dato de hace 1-2 h sigue siendo útil para MOSTRAR; pasadas CACHE_MAX_S se descarta (mejor nada que
# algo engañoso). Para CALCULAR (radar predictivo) solo se usa mientras sea reciente: ver utiles_para_calculo().
CACHE_MAX_S = int(os.getenv("AVAMET_METAR_CACHE_MAX_S", "10800"))        # 3 h: límite para mostrarlo
CACHE_CALCULO_S = int(os.getenv("AVAMET_METAR_CACHE_CALCULO_S", "1800"))  # 30 min: límite para usarlo en predicciones
_CACHE_METAR: dict = {}            # icao -> (instante en que se guardó, datos tal cual salieron de la API)
_CERROJO_CACHE = threading.Lock()
_ULTIMO_ERROR: dict | None = None   # fallo de la última descarga de METAR ({"tipo","detalle","ts"}); None = la última fue bien


def ultimo_error() -> dict | None:
    """Fallo de la última llamada a obtener_metars (None si fue bien). Lo usa el bucle principal para pedir el diagnóstico de red."""
    return _ULTIMO_ERROR


def _desde_cache(icaos, ahora: float) -> dict:
    """Último dato bueno de cada ICAO (si no es demasiado viejo), marcado como desactualizado y con su edad en segundos."""
    res = {}
    with _CERROJO_CACHE:
        for icao in icaos:
            e = _CACHE_METAR.get(icao)
            if not e:
                continue
            edad = ahora - e[0]
            if edad > CACHE_MAX_S:
                _CACHE_METAR.pop(icao, None)
                continue
            res[icao] = {**e[1], "desactualizado": True, "cache_edad_s": int(max(edad, 0))}
    return res


def utiles_para_calculo(metars: dict) -> dict:
    """METAR que pueden alimentar el radar predictivo: los recién descargados y los de caché de hasta CACHE_CALCULO_S."""
    return {k: v for k, v in metars.items()
            if v and (not v.get("desactualizado") or v.get("cache_edad_s", 0) <= CACHE_CALCULO_S)}


def obtener_metars(icaos: list, ahora: float | None = None) -> dict:
    global _ULTIMO_ERROR
    ahora = time.time() if ahora is None else ahora
    if not icaos: return {}
    url = f"https://aviationweather.gov/api/data/metar?ids={','.join(icaos)}&format=json"
    headers = {"User-Agent": _user_agent()}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        datos = r.json()
        resultado = {}
        for m in datos:
            icao = m.get("icaoId")
            if not icao: continue
            resultado[icao] = {
                "temperatura": _safe_float(m.get("temp")),
                "punto_rocio": _safe_float(m.get("dewp")),
                "viento_nudos": _safe_float(m.get("wspd")),
                "viento_dir": _safe_float(m.get("wdir")),
                "visibilidad": m.get("visib"),
                "nubosidad": traducir_nubosidad(m.get("clouds", [])),
                "fenomenos": traducir_fenomenos(m.get("wxString", "")),
                "actualizado": m.get("reportTime")
            }
        _ULTIMO_ERROR = None
        with _CERROJO_CACHE:
            for icao, d in resultado.items():
                _CACHE_METAR[icao] = (ahora, d)
        # los aeropuertos que la API no ha devuelto esta vez (sin METAR reciente) se rellenan con su último dato bueno
        faltan = [i for i in icaos if i not in resultado]
        if faltan:
            resultado.update(_desde_cache(faltan, ahora))
        return resultado
    except Exception as e:
        tipo, detalle = diagnostico.registrar_excepcion("METAR", e)
        _ULTIMO_ERROR = {"tipo": tipo, "detalle": detalle, "ts": ahora}
        cacheado = _desde_cache(icaos, ahora)
        print(f"Error obteniendo METAR: {e}" + (f" (se usa la caché de {len(cacheado)} aeropuerto(s))" if cacheado else ""))
        return cacheado
