import json
import re
import os
import sys
import shutil
import requests
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config
from . import diagnostico

BASE_URL = "https://www.avamet.org"
MAPA_URL = f"{BASE_URL}/mxo-meteoxarxaonline.html"
MXO_URL  = f"{BASE_URL}/mxo_i.php"
HEADERS  = {"User-Agent": "Mozilla/5.0 (compatible; MiAppMeteo/1.0)"}

PROGRESO_DESCARGAS = {"activo": False, "actual": 0, "total": 0}

# BUNDLED_FILE: copia de serie (dentro del .exe, o en la raíz del proyecto si la has copiado).
# CACHE_FILE: la que la app lee/escribe, en la carpeta de datos (config.DATA_DIR).
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys._MEIPASS)
else:
    BASE_DIR = Path(__file__).parent.parent
BUNDLED_FILE = BASE_DIR / "coordenadas.json"
CACHE_FILE = config.DATA_DIR / "coordenadas.json"

def obtener_listado_estaciones() -> dict:
    resp = requests.get(MAPA_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    estaciones = {}
    for enlace in soup.find_all("a", href=True):
        m = re.search(r"mxo_i\.php\?id=([a-zA-Z0-9]+)", enlace["href"])
        if m:
            est_id = m.group(1)
            nombre = enlace.get_text(strip=True)
            municipio = municipio_de(nombre)
            estaciones.setdefault(municipio, []).append((est_id, nombre))
    return estaciones

def _extraer_coordenadas(estacion_id: str):
    try:
        resp = requests.get(f"{MXO_URL}?id={estacion_id}", headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        texto = " ".join(soup.stripped_strings)
        pat_lat = r"(\d+)°\s*(\d+)'\s*([\d.]+)\"\s*([NS])"
        pat_lon = r"(\d+)°\s*(\d+)'\s*([\d.]+)\"\s*([EW])"
        m_lat = re.search(pat_lat, texto)
        m_lon = re.search(pat_lon, texto)
        if not m_lat or not m_lon: return None, None, None
        def to_dec(g, mi, s, h):
            v = float(g) + float(mi) / 60 + float(s) / 3600
            return -v if h in ("S", "W") else v
        lat = to_dec(*m_lat.groups())
        lon = to_dec(*m_lon.groups())
        titulo = soup.find("title")
        nombre = titulo.get_text(strip=True) if titulo else estacion_id
        return lat, lon, nombre
    except Exception:
        return None, None, None

def municipio_de(nombre: str) -> str:
    """'Almassora (Nord)' -> 'Almassora'. Igual que se agrupa en obtener_listado_estaciones."""
    return str(nombre).split("(")[0].strip()


def completar_municipios(coords: dict) -> dict:
    """B4: coordenadas.json no traía la clave 'municipio' en ninguna estación (ni la descarga la guardaba), así que
    el buscador de la web y /pueblo de Telegram solo podían usar 'nombre'. Se rellena aquí, al cargar, sin tocar
    las que ya la tengan. Devuelve el mismo diccionario."""
    for info in coords.values():
        if isinstance(info, dict) and not info.get("municipio"):
            info["municipio"] = municipio_de(info.get("nombre", ""))
    return coords


def obtener_coordenadas_todas(forzar: bool = False) -> dict:
    global PROGRESO_DESCARGAS
    
    if not forzar and CACHE_FILE.exists() and CACHE_FILE.stat().st_size > 100:
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return completar_municipios(json.load(f))
        except Exception:
            pass
            
    if not forzar and BUNDLED_FILE.exists() and BUNDLED_FILE.stat().st_size > 100:
        print("📦 Cargando base de datos empaquetada/local...")
        try:
            with open(BUNDLED_FILE, "r", encoding="utf-8") as f:
                coords = completar_municipios(json.load(f))
            if BUNDLED_FILE.resolve() != CACHE_FILE.resolve():
                shutil.copy(BUNDLED_FILE, CACHE_FILE)
            return coords
        except Exception as e:
            print(f"Error cargando archivo empaquetado: {e}")

    print("🌍 Descargando coordenadas desde AVAMET...")
    estaciones = obtener_listado_estaciones()
    ids_nombres = [(eid, nom) for lista in estaciones.values() for eid, nom in lista]
    
    PROGRESO_DESCARGAS["total"] = len(ids_nombres)
    PROGRESO_DESCARGAS["actual"] = 0
    PROGRESO_DESCARGAS["activo"] = True
    
    coords = {}
    with ThreadPoolExecutor(max_workers=10) as pool:
        futuros = {pool.submit(_extraer_coordenadas, eid): (eid, nom) for eid, nom in ids_nombres}
        for i, fut in enumerate(as_completed(futuros), 1):
            eid, nombre = futuros[fut]
            lat, lon, _ = fut.result()
            if lat is not None and lon is not None:
                coords[eid] = {"lat": lat, "lon": lon, "nombre": nombre, "municipio": municipio_de(nombre)}
            PROGRESO_DESCARGAS["actual"] = i

    tmp_file = CACHE_FILE.with_suffix(".tmp")
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(coords, f, ensure_ascii=False, indent=1)
    os.replace(tmp_file, CACHE_FILE)
    
    PROGRESO_DESCARGAS["activo"] = False
    return coords

NUM = r"(\d+(?:[.,]\d+)?)"

# La presión es opcional: un sensor de presión averiado no debe invalidar la estación.
# "Fecha lectura" es obligatoria para considerar válidos los datos de la central:
# sin ella no se puede saber la edad del dato (necesaria para la seguridad).
CLAVES_MINIMAS = ("Temperatura", "Humedad", "Fecha lectura")   # la presión NO es imprescindible

# Ej. texto real: "01-11-2026 05:25 15,1° mín 15,1° màx 17,7° Humitat 90 %"
# Confirmado por el usuario (1-nov-2026, comparando con la web): el primer valor es la temperatura ACTUAL
# y las etiquetas mín/màx preceden a sus valores.
RE_FECHA_TEMPS = re.compile(
    r"(\d{2}-\d{2}-\d{4})\s+(\d{2}:\d{2})\s+"
    r"(-?\d+(?:[.,]\d+)?)\s*°\s*mín\s+(-?\d+(?:[.,]\d+)?)\s*°\s*màx\s+(-?\d+(?:[.,]\d+)?)\s*°"
)


def _a_float(texto: str):
    """'1.021' -> 1021.0 ; '1021,3' -> 1021.3 ; '12,5' -> 12.5 ; None si no es válido."""
    t = str(texto).strip()
    if "," in t:                                   # 1.021,3 -> 1021.3
        t = t.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,2}\.\d{3}", t):       # 1.021 -> 1021 (miles con punto)
        t = t.replace(".", "")
    try:
        return float(t)
    except ValueError:
        return None


def _punto(texto: str) -> str:
    return texto.replace(",", ".")


@lru_cache(maxsize=1)
def zona_horaria():
    """ZoneInfo de la 'Fecha lectura' de AVAMET, o None si no está disponible.
    En Windows requiere el paquete 'tzdata'. Con None se usa la hora local del equipo."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(config.ZONA_HORARIA)
    except Exception as e:
        print(f"⚠️  Zona horaria '{config.ZONA_HORARIA}' no disponible ({e}). Se usa la hora local del equipo.")
        return None


def datos_validos(datos) -> bool:
    """True si la lectura tiene todos los campos mínimos y no es un error."""
    return isinstance(datos, dict) and "Error" not in datos and all(k in datos for k in CLAVES_MINIMAS)


def edad_lectura_s(datos: dict, ahora: float | None = None):
    """Segundos transcurridos desde 'Fecha lectura' (hora local de la estación).
    `ahora` es un epoch en segundos (por defecto time.time()).
    Devuelve None si falta/no se puede interpretar la fecha, o si está en el futuro
    más de 5 min (señal de zona horaria mal configurada)."""
    import time as _time
    texto = (datos or {}).get("Fecha lectura")
    if not texto:
        return None
    try:
        dt = datetime.strptime(texto, "%d-%m-%Y %H:%M")
    except ValueError:
        return None
    tz = zona_horaria()
    if tz is not None:
        dt = dt.replace(tzinfo=tz)
    lectura_ts = dt.timestamp()          # naive -> hora local del sistema
    edad = (ahora if ahora is not None else _time.time()) - lectura_ts
    if edad < -300:
        return None
    return max(0.0, edad)


def obtener_datos_tiempo_real(estacion_id: str) -> dict:
    try:
        resp = requests.get(f"{MXO_URL}?id={estacion_id}", headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        texto = " ".join(soup.stripped_strings)
        datos = {}

        m = RE_FECHA_TEMPS.search(texto)
        if m:
            datos["Fecha lectura"] = f"{m.group(1)} {m.group(2)}"
            datos["Temperatura"] = _punto(m.group(3)) + " °C"
            datos["Temp mín"] = _punto(m.group(4)) + " °C"
            datos["Temp máx"] = _punto(m.group(5)) + " °C"
        else:
            # Respaldo (formato antiguo): solo temperatura, SIN fecha -> la central quedará SIN_DATOS.
            m = re.search(rf"(-?\d+(?:[.,]\d+)?)\s*°\s*mín", texto)
            if m: datos["Temperatura"] = _punto(m.group(1)) + " °C"

        m = re.search(r"Humitat\s+(\d+)\s*%", texto)
        if m: datos["Humedad"] = m.group(1) + " %"
        m = re.search(rf"Vent\s+{NUM}\s*km/h\s+([A-Z]+)", texto)
        if m: datos["Viento"] = f"{_punto(m.group(1))} km/h {m.group(2)}"
        m = re.search(rf"Vent\s+màx\s+{NUM}\s*km/h", texto)
        if m: datos["Viento máximo"] = _punto(m.group(1)) + " km/h"
        m = re.search(rf"Pluja\s+hui\s+{NUM}\s*mm", texto)
        if m: datos["Lluvia hoy"] = _punto(m.group(1)) + " mm"
        m = re.search(rf"Pluja\s+mensual\s+{NUM}\s*mm", texto)
        if m: datos["Lluvia mensual"] = _punto(m.group(1)) + " mm"
        m = re.search(rf"Pluja\s+anual\s+{NUM}\s*mm", texto)
        if m: datos["Lluvia anual"] = _punto(m.group(1)) + " mm"
        m = re.search(r"Pressió\s+al\s+nivell\s+de\s+la\s+mar\s+([\d.,]+)\s*hPa", texto)
        if m:
            p = _a_float(m.group(1))
            if p is not None and config.PRESION_MIN_HPA <= p <= config.PRESION_MAX_HPA:
                datos["Presión"] = f"{p:g} hPa"
            else:                                         # fuera de rango: se ignora y se deja constancia
                datos["Presión descartada"] = m.group(1) + " hPa"
        return datos
    except Exception as e:
        tipo, _detalle = diagnostico.registrar_excepcion("AVAMET", e, f"estación {estacion_id}")
        return {"Error": str(e), "TipoError": tipo}

def encontrar_mas_cercana(lat: float, lon: float, coords: dict):
    from math import radians, sin, cos, atan2, sqrt
    def hav(a, b, c, d):
        R = 6371.0
        p1, p2 = radians(a), radians(c)
        dp = radians(c - a)
        dl = radians(d - b)
        x = sin(dp/2)**2 + cos(p1)*cos(p2)*sin(dl/2)**2
        return 2 * R * atan2(sqrt(x), sqrt(1 - x))
    mejor, mejor_d = None, float("inf")
    for eid, info in coords.items():
        d = hav(lat, lon, info["lat"], info["lon"])
        if d < mejor_d:
            mejor_d, mejor = d, (eid, info, d)
    return mejor