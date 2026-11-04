"""Nubes vistas por satélite (Meteosat SEVIRI, EUMETSAT Data Tailor): módulo OPCIONAL y ORIENTATIVO.

- Solo se activa si existen las variables de entorno EUMETSAT_CONSUMER_KEY y EUMETSAT_CONSUMER_SECRET (clave personal de cada usuario,
  que se crea en https://api.eumetsat.int/api-key/ ; NUNCA se guardan en el código ni en el repositorio).
- Necesita los paquetes `eumdac`, `netCDF4` y `numpy` (requirements-satelite.txt). Si faltan, el módulo se desactiva sin romper la app.
- NO entra en el semáforo de seguridad. Las reglas de nube (umbrales) son criterio propio, sin calibrar con noches reales.
- No verificado contra el servidor real de EUMETSAT: nombres de canales (channel_4 = IR 3.9 µm, channel_9 = IR 10.8 µm), formato y
  proyección del recorte, y qué satélite entrega hoy la colección HRSEVIRI. Los coeficientes de calibración deben contrastarse con el
  documento oficial de EUMETSAT; solo se incluyen MSG3 y MSG4 y, si el satélite del producto no se reconoce, se devuelve un error.
"""
import math
import os
from collections import deque
import shutil
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone

from . import diagnostico

COLECCION = "EO:EUM:DAT:MSG:HRSEVIRI"
REFRESCO_S = int(os.getenv("AVAMET_METEOSAT_REFRESCO_S", "1800"))     # una consulta cada 30 min como máximo
REINTENTO_S = int(os.getenv("AVAMET_METEOSAT_REINTENTO_S", "900"))    # si falla o no hay imagen nueva, a los 15 min
IMAGEN_VIEJA_MIN = int(os.getenv("AVAMET_METEOSAT_IMAGEN_VIEJA_MIN", "45"))   # aviso si la imagen mostrada es más antigua
ESPERA_MAX_S = 600
SONDEO_S = 8
MEDIO_LAT, MEDIO_LON = 1.5, 1.7        # medio recorte alrededor de la estación (grados)
UMBRAL_TECHO_FRIO_C = -20.0            # sin calibrar
UMBRAL_DIFF_NOCTURNA_K = 2.2           # sin calibrar: (10.8 - 3.9 µm) solo de noche
UMBRAL_DELTA_AIRE_K = 12.0             # sin calibrar: aire local - T_brillo 10.8
RANGO_TB_C = (-120.0, 60.0)            # fuera de esto la conversión es absurda y se rechaza
# Mejora 3 (3-nov-2026): la mínima de TODO el recorte (~334 x 290 km) casi siempre tiene alguna nube fría: dio 39 falsas alarmas de 39.
# El aviso por nube fría del informe usa ahora la mínima dentro de este radio alrededor de la estación (criterio propio, sin calibrar).
RADIO_AVISO_KM = float(os.getenv("AVAMET_METEOSAT_RADIO_KM", "40"))
# Mejora 4 (3-nov-2026): franja de crepúsculo. Con el sol entre estos dos valores no se aplica la prueba nocturna de niebla
# (el canal de 3,9 µm todavía mezcla luz solar reflejada) y tampoco se da el cielo por despejado: «SIN DETERMINAR (crepúsculo)».
# Antes había un único corte en -6°: la clasificación cambiaba de DESPEJADO a NIEBLA sin que cambiara el cielo. Sin calibrar.
SOL_DIA_DESDE = float(os.getenv("AVAMET_METEOSAT_SOL_DIA", "-3"))       # sol a esta altura o más: día
SOL_NOCHE_HASTA = float(os.getenv("AVAMET_METEOSAT_SOL_NOCHE", "-9"))   # sol a esta altura o menos: noche

C1 = 1.19104e-5     # mW m-2 sr-1 (cm-1)-4
C2 = 1.43877        # K cm
# (nu_c, alpha, beta) por satélite; SIN VERIFICAR aquí: contrastar con el documento oficial de EUMETSAT
CALIB = {
    "MSG4": {"channel_4": (2555.280, 0.9916, 2.9438), "channel_9": (929.842, 0.9982, 0.6056)},
    "MSG3": {"channel_4": (2547.771, 0.9915, 2.9002), "channel_9": (931.700, 0.9983, 0.6084)},
}

_CERROJO = threading.RLock()      # reentrante: obtener() llama a traza() con el cerrojo tomado
_CACHE: dict = {}
_TRAZA = deque(maxlen=300)        # pasos de las últimas consultas (texto); NUNCA incluye la clave ni el secreto


def _paso(texto: str) -> None:
    """Anota un paso con la hora local: en memoria (panel y /api/meteosat/traza), en el log de diagnóstico y en la consola."""
    linea = f"{time.strftime('%H:%M:%S')} {texto}"
    with _CERROJO:
        _TRAZA.append(linea)
    print(f"[meteosat] {texto}")
    try: diagnostico.registrar("METEOSAT", "PASO", texto)
    except Exception: pass


def traza(ultimas: int = 300) -> list:
    with _CERROJO:
        return list(_TRAZA)[-ultimas:]


def configurado() -> bool:
    return bool(os.getenv("EUMETSAT_CONSUMER_KEY") and os.getenv("EUMETSAT_CONSUMER_SECRET"))


def paquetes_instalados() -> bool:
    try:
        import eumdac, netCDF4, numpy  # noqa: F401
        return True
    except Exception:
        return False


def limpiar_cache() -> None:
    with _CERROJO:
        _CACHE.clear()


def disponible() -> bool:
    """True si hay credenciales Y están instalados los paquetes necesarios."""
    return configurado() and paquetes_instalados()


# ---- Funciones puras (se prueban sin red) --------------------------------------------------------------------------------
def elevacion_solar(dt_utc: datetime, lat: float, lon: float) -> float:
    """Elevación solar en grados (algoritmo NOAA, fórmulas completas). Precisión ~décimas de grado."""
    if dt_utc.tzinfo is None: dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    doy = dt_utc.timetuple().tm_yday
    h = dt_utc.hour + dt_utc.minute / 60.0 + dt_utc.second / 3600.0
    g = 2.0 * math.pi / 365.0 * (doy - 1 + (h - 12.0) / 24.0)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    tst = h * 60.0 + eqtime + 4.0 * lon
    ha = math.radians(tst / 4.0 - 180.0)
    la = math.radians(lat)
    s = math.sin(la) * math.sin(decl) + math.cos(la) * math.cos(decl) * math.cos(ha)
    return math.degrees(math.asin(max(-1.0, min(1.0, s))))


def a_celsius(datos, unidades: str, canal: str, satelite: str):
    """Matriz (numpy) de radiancia/Kelvin/Celsius -> °C. Lanza ValueError si el resultado no es físicamente razonable."""
    import numpy as np
    d = np.squeeze(np.ma.filled(np.ma.asarray(datos).astype(np.float64), np.nan))
    u = (unidades or "").strip().lower()
    if u in ("k", "kelvin", "kelvins"): tc = d - 273.15
    elif u in ("c", "celsius", "degc", "degrees_celsius"): tc = d
    else:
        if satelite not in CALIB: raise ValueError(f"Satélite {satelite!r} sin tabla de calibración")
        nu, alpha, beta = CALIB[satelite][canal]
        rad = np.where(d > 0.0, d, np.nan)
        with np.errstate(invalid="ignore", divide="ignore"):
            tb = ((C2 * nu) / np.log(1.0 + (C1 * nu ** 3) / rad) - beta) / alpha
        tc = tb - 273.15
    v = tc[np.isfinite(tc)]
    if v.size == 0: raise ValueError("El canal no contiene valores válidos")
    if v.min() < RANGO_TB_C[0] or v.max() > RANGO_TB_C[1]:
        raise ValueError(f"Temperatura de brillo fuera de rango ({v.min():.0f}..{v.max():.0f} °C): ¿unidades mal interpretadas?")
    return tc


def pixel_mas_cercano(lats, lons, lat, lon):
    """(iy, ix, lat_real, lon_real) con lat/lon de 1 o 2 dimensiones. ValueError si el punto cae fuera del recorte."""
    import numpy as np
    lats, lons = np.asarray(lats, dtype=float), np.asarray(lons, dtype=float)
    if not (np.nanmin(lats) <= lat <= np.nanmax(lats) and np.nanmin(lons) <= lon <= np.nanmax(lons)):
        raise ValueError("La ubicación cae fuera del recorte descargado")
    if lats.ndim == 1 and lons.ndim == 1:
        iy, ix = int(np.abs(lats - lat).argmin()), int(np.abs(lons - lon).argmin())
        return iy, ix, float(lats[iy]), float(lons[ix])
    if lats.ndim == 2 and lons.ndim == 2:
        iy, ix = np.unravel_index(np.nanargmin((lats - lat) ** 2 + (lons - lon) ** 2), lats.shape)
        return int(iy), int(ix), float(lats[iy, ix]), float(lons[iy, ix])
    raise ValueError(f"Dimensiones de lat/lon no reconocidas: {lats.shape}, {lons.shape}")


def _tramo(eje, x: float):
    """(i0, i1, peso_de_i1) del tramo del eje 1-D (creciente o decreciente) que contiene x."""
    import numpy as np
    eje = np.asarray(eje, dtype=float)
    n = eje.size
    if n < 2:
        return 0, 0, 0.0
    creciente = eje[-1] >= eje[0]
    e = eje if creciente else eje[::-1]
    j = min(max(int(np.searchsorted(e, x)), 1), n - 1)          # e[j-1] <= x <= e[j]
    i0, i1 = j - 1, j
    w = float((x - e[i0]) / (e[i1] - e[i0])) if e[i1] != e[i0] else 0.0
    w = min(max(w, 0.0), 1.0)
    if w < 1e-6: w = 0.0                         # justo sobre un píxel: sin restos de coma flotante
    elif w > 1 - 1e-6: w = 1.0
    if not creciente:
        i0, i1 = n - 1 - i0, n - 1 - i1
    return i0, i1, w


def valores_en_punto(campos: list, lats, lons, lat: float, lon: float) -> tuple[list, str]:
    """Valor de cada campo (matrices [fila, columna]) en la posición de la estación, por interpolación bilineal entre los
    4 píxeles que la rodean. Antes se tomaba el píxel más cercano, y con 78 filas la estación caía justo entre dos y se
    elegía siempre la del sur (medio píxel, ~2 km). Si la rejilla no es 1-D o alguno de los 4 píxeles no tiene dato,
    se usa el píxel más cercano. Devuelve (valores, descripción para la traza)."""
    import numpy as np
    la, lo = np.asarray(lats, dtype=float), np.asarray(lons, dtype=float)
    iy, ix, _, _ = pixel_mas_cercano(la, lo, lat, lon)          # también comprueba que la estación cae dentro
    if la.ndim == 1 and lo.ndim == 1:
        y0, y1, wy = _tramo(la, lat)
        x0, x1, wx = _tramo(lo, lon)
        pesos = ((y0, x0, (1 - wy) * (1 - wx)), (y0, x1, (1 - wy) * wx), (y1, x0, wy * (1 - wx)), (y1, x1, wy * wx))
        valores = []
        for c in campos:
            c = np.asarray(c, dtype=float)
            if any(p > 0 and not np.isfinite(c[y, x]) for y, x, p in pesos):
                break
            valores.append(float(sum(p * c[y, x] for y, x, p in pesos if p > 0)))
        else:
            def tramo(sing, plur, i0, i1, w):
                if i0 > i1: i0, i1, w = i1, i0, 1.0 - w             # siempre de menor a mayor índice
                if i0 == i1 or w < 0.005: return f"{sing} {i0}"
                if w > 0.995: return f"{sing} {i1}"
                return f"{plur} {i0}-{i1} (pesos {1 - w:.2f}/{w:.2f})"
            return valores, (f"valor interpolado en la estación: {tramo('fila', 'filas', y0, y1, wy)}, "
                             f"{tramo('columna', 'columnas', x0, x1, wx)}")
    return [float(np.asarray(c, dtype=float)[iy, ix]) for c in campos], f"valor del píxel más cercano ({iy},{ix})"


def periodo_solar(elev: float, dia_desde: float | None = None, noche_hasta: float | None = None) -> str:
    """'día', 'crepúsculo' o 'noche' según la altura del sol (grados). Si los límites están mal puestos (noche >= día), no hay
    franja de crepúsculo y el corte es el límite de día."""
    dia = SOL_DIA_DESDE if dia_desde is None else dia_desde
    noche = SOL_NOCHE_HASTA if noche_hasta is None else noche_hasta
    if elev >= dia:
        return "día"
    if noche >= dia or elev <= noche:
        return "noche"
    return "crepúsculo"


def evaluar(t108_c: float, t039_c: float, es_noche: bool, aire_c: float | None, crepusculo: bool = False) -> dict:
    """Estimación ORIENTATIVA (umbrales sin calibrar). El orden de las reglas importa: la primera que se cumple gana.
    Las reglas de cima fría y de diferencia con el aire solo usan el canal térmico de 10,8 µm y valen a cualquier hora;
    la de niebla (10,8 - 3,9 µm) solo de noche. En el crepúsculo, si no salta ninguna, el resultado es SIN DETERMINAR."""
    diff = t108_c - t039_c
    if t108_c < UMBRAL_TECHO_FRIO_C:
        return {"estado": "CUBIERTO", "motivo": f"Cima fría sobre la vertical ({t108_c:.1f} °C < {UMBRAL_TECHO_FRIO_C:.0f} °C)", "diff_k": round(diff, 1)}
    if es_noche and diff > UMBRAL_DIFF_NOCTURNA_K:
        return {"estado": "POSIBLE NUBE BAJA / NIEBLA", "motivo": f"Diferencia nocturna 10.8 - 3.9 µm = {diff:+.1f} K (> {UMBRAL_DIFF_NOCTURNA_K} K)", "diff_k": round(diff, 1)}
    if aire_c is not None and aire_c - t108_c > UMBRAL_DELTA_AIRE_K:
        return {"estado": "POSIBLE NUBOSIDAD", "motivo": f"El satélite ve {aire_c - t108_c:.1f} K menos que tu estación ({aire_c:.1f} °C)", "diff_k": round(diff, 1)}
    if crepusculo:
        return {"estado": "SIN DETERMINAR (crepúsculo)",
                "motivo": (f"Con el sol entre {SOL_NOCHE_HASTA:g}° y {SOL_DIA_DESDE:g}° el canal de 3,9 µm mezcla luz solar y emisión térmica: "
                           f"la prueba de niebla no es fiable y no se puede dar el cielo por despejado (diferencia 10.8 - 3.9 µm = {diff:+.1f} K)"),
                "diff_k": round(diff, 1)}
    return {"estado": "DESPEJADO (estimado)", "motivo": "Sin firma térmica de nubes frías ni de niebla nocturna; no descarta nubes bajas templadas ni cirros finos", "diff_k": round(diff, 1)}


def minimo_en_radio(campo, lats, lons, lat: float, lon: float, radio_km: float) -> tuple:
    """(mínimo, nº de píxeles) del campo [fila, columna] dentro de `radio_km` de la estación (distancia equirrectangular: de sobra para
    unas decenas de km). lat/lon de 1 o 2 dimensiones. (None, 0) si no cae ningún píxel válido dentro del radio."""
    import numpy as np
    c = np.ma.filled(np.ma.asarray(campo, dtype=float), np.nan)
    la, lo = np.asarray(lats, dtype=float), np.asarray(lons, dtype=float)
    if la.ndim == 1 and lo.ndim == 1:
        la, lo = np.meshgrid(la, lo, indexing="ij")
    if la.shape != c.shape or lo.shape != c.shape:
        return None, 0
    dy = (la - lat) * 111.2
    dx = (lo - lon) * 111.2 * math.cos(math.radians(lat))
    dentro = (dx * dx + dy * dy <= radio_km * radio_km) & np.isfinite(c)
    n = int(dentro.sum())
    return (float(c[dentro].min()), n) if n else (None, 0)


def analizar_netcdf(ruta: str, instante_utc: datetime, satelite: str, lat: float, lon: float, aire_c: float | None) -> dict:
    """Lee el NetCDF recortado y devuelve el resumen. Sin red: se puede probar con un fichero cualquiera."""
    import netCDF4 as nc
    import numpy as np
    with nc.Dataset(ruta, "r") as ds:
        vars_ = list(ds.variables.keys())
        _paso("NetCDF abierto. Variables: " + ", ".join(f"{n}{tuple(getattr(ds.variables[n], 'shape', ()))}" for n in vars_))
        def canal(num):
            for nombre in ds.variables:
                n = nombre.lower()
                if n in (f"channel_{num}", f"ch{num}"): return ds.variables[nombre]
            raise KeyError(f"No hay variable del canal {num}. Variables: {vars_}")
        v4, v9 = canal(4), canal(9)
        _paso(f"Canal 4 -> variable con unidades {getattr(v4, 'units', 'N/A')!r}; canal 9 -> unidades {getattr(v9, 'units', 'N/A')!r}")
        t039 = a_celsius(v4[:], getattr(v4, "units", ""), "channel_4", satelite)
        t108 = a_celsius(v9[:], getattr(v9, "units", ""), "channel_9", satelite)
        _paso(f"Conversión a °C correcta (satélite {satelite}); rango 10.8 µm: {float(np.nanmin(t108)):.1f} a {float(np.nanmax(t108)):.1f} °C")
        lats = np.ma.filled(np.ma.asarray(ds.variables["lat"][:], dtype=float), np.nan)
        lons = np.ma.filled(np.ma.asarray(ds.variables["lon"][:], dtype=float), np.nan)
        iy, ix, rlat, rlon = pixel_mas_cercano(lats, lons, lat, lon)
        (a108, a039), muestreo = valores_en_punto([t108, t039], lats, lons, lat, lon)
        _paso(f"Píxel más cercano ({iy},{ix}) = {rlat:.3f}N, {rlon:.3f}E para la estación en {lat:.3f}N, {lon:.3f}E; {muestreo}")
        regional = float(np.nanmin(t108))
        cerca, n_cerca = minimo_en_radio(t108, lats, lons, lat, lon, RADIO_AVISO_KM)
        if cerca is None:                        # radio menor que un píxel: vale el valor en la estación
            cerca = a108
        unidades = (getattr(v4, "units", "N/A"), getattr(v9, "units", "N/A"))
    elev = elevacion_solar(instante_utc, lat, lon)
    periodo = periodo_solar(elev)
    es_noche = periodo == "noche"
    r = evaluar(a108, a039, es_noche, aire_c, crepusculo=(periodo == "crepúsculo"))
    _paso(f"Valores: IR10.8={a108:.1f} °C, IR3.9={a039:.1f} °C, mínima zona={regional:.1f} °C, mínima a {RADIO_AVISO_KM:g} km={cerca:.1f} °C "
          f"({n_cerca} píxeles), sol {elev:.1f}° ({periodo}), aire estación={aire_c if aire_c is not None else 'n/d'} -> {r['estado']}")
    r.update({"t108_c": round(a108, 1), "t039_c": round(a039, 1), "min_regional_c": round(regional, 1),
              "min_radio_c": round(cerca, 1), "radio_km": RADIO_AVISO_KM, "elevacion_solar": round(elev, 1),
              "noche": es_noche, "periodo": periodo, "pixel": [round(rlat, 3), round(rlon, 3)], "muestreo": muestreo, "satelite": satelite,
              "barrido_utc": instante_utc.strftime("%Y-%m-%d %H:%M"), "unidades": list(unidades), "variables": vars_})
    return r


def _como_utc(dt: datetime) -> datetime:
    """eumdac da `sensing_end` sin zona horaria (es UTC): se normaliza para poder comparar."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def antiguedad(datos: dict | None, ahora: float | None = None) -> dict:
    """Edad (min) de la imagen que se está mostrando y, si pasa de IMAGEN_VIEJA_MIN, un aviso para la interfaz."""
    if not datos or not datos.get("barrido_utc"):
        return {"edad_imagen_min": None, "aviso_imagen": None}
    try:
        t = datetime.strptime(datos["barrido_utc"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return {"edad_imagen_min": None, "aviso_imagen": None}
    edad = int(((time.time() if ahora is None else ahora) - t) // 60)
    aviso = (f"La imagen es de hace {edad} min: EUMETSAT no ha publicado ninguna más nueva (lo normal es una cada 15 min)."
             if edad > IMAGEN_VIEJA_MIN else None)
    return {"edad_imagen_min": edad, "aviso_imagen": aviso}


# ---- Red (eumdac + Data Tailor): NO verificado contra el servidor real -------------------------------------------------------
def _descargar_recorte(lat: float, lon: float, ya_procesado: datetime | None = None):
    """Devuelve (ruta_netcdf, instante_utc, satelite). Borra siempre el trabajo del servidor (hay cuota de trabajos).
    Si la imagen más reciente NO es posterior a `ya_procesado` (fin de barrido de la última que se analizó), devuelve None
    sin crear ningún trabajo en Data Tailor: así no se gasta cuota ni se registra dos veces la misma imagen
    (pasó el 3-nov-2026 cuando EUMETSAT estuvo una hora sin publicar)."""
    _paso("1/7 Importando eumdac y autenticando con EUMETSAT")
    import eumdac
    from eumdac.tailor_models import Chain
    token = eumdac.AccessToken((os.environ["EUMETSAT_CONSUMER_KEY"], os.environ["EUMETSAT_CONSUMER_SECRET"]))
    ds = eumdac.DataStore(token)
    tailor = eumdac.DataTailor(token)
    ahora = datetime.now(timezone.utc)
    productos = list(ds.get_collection(COLECCION).search(dtstart=ahora - timedelta(hours=3), dtend=ahora))
    _paso(f"2/7 Búsqueda en {COLECCION}: {len(productos)} barrido(s) en las últimas 3 h")
    if not productos: raise RuntimeError("Sin barridos HRSEVIRI en las últimas 3 h")
    prod = max(productos, key=lambda p: p.sensing_end)                  # no se confía en el orden de la búsqueda
    pid = str(prod)
    _paso(f"3/7 Barrido más reciente: {pid} (fin de barrido {prod.sensing_end})")
    if ya_procesado is not None and _como_utc(prod.sensing_end) <= _como_utc(ya_procesado):
        _paso(f"3/7 Esa imagen ya se analizó: no se pide a Data Tailor (EUMETSAT aún no ha publicado otra más nueva)")
        return None
    satelite = next((m for m in ("MSG4", "MSG3", "MSG2", "MSG1") if m in pid), None)
    if satelite is None: raise RuntimeError(f"No se reconoce el satélite en el producto {pid!r}")
    _paso(f"4/7 Satélite detectado: {satelite}. Enviando recorte a Data Tailor")
    chain = Chain(product="HRSEVIRI", format="netcdf4", projection="geographic",
                  filter={"bands": ["channel_4", "channel_9"]},
                  roi={"NSWE": [lat + MEDIO_LAT, lat - MEDIO_LAT, lon - MEDIO_LON, lon + MEDIO_LON]})
    job = None
    try:
        job = tailor.new_customisation(prod, chain=chain)
        t0 = time.time(); previo = None
        _paso(f"5/7 Trabajo creado (id {getattr(job, '_id', '?')})")
        # En eumdac `status` es una propiedad que consulta el servidor en cada lectura (no existe .refresh()).
        estado = job.status
        while estado in ("QUEUED", "RUNNING", "INACTIVE"):
            if estado != previo: _paso(f"    estado del trabajo: {estado} ({int(time.time() - t0)} s)"); previo = estado
            if time.time() - t0 > ESPERA_MAX_S:
                try: job.kill()
                except Exception: pass
                raise TimeoutError("Data Tailor no terminó a tiempo")
            time.sleep(SONDEO_S)
            estado = job.status
        _paso(f"    estado final del trabajo: {estado} ({int(time.time() - t0)} s)")
        if estado != "DONE":
            raise RuntimeError(f"Data Tailor falló ({estado}): {getattr(job, 'logfile', 'sin log')}")
        salida = job.outputs[0]
        carpeta = tempfile.mkdtemp(prefix="meteosat_")
        ruta = os.path.join(carpeta, os.path.basename(salida))
        with (job.stream_output(salida) if hasattr(job, "stream_output") else job.open(salida)) as f1, open(ruta, "wb") as f2:
            shutil.copyfileobj(f1, f2)
        _paso(f"6/7 Descargado {os.path.basename(salida)} ({os.path.getsize(ruta) / 1024:.0f} KB)")
        return ruta, prod.sensing_end, satelite
    finally:
        if job is not None:
            try: job.delete(); _paso("7/7 Trabajo borrado del servidor (cuota liberada)")
            except Exception as e: _paso(f"7/7 AVISO: no se pudo borrar el trabajo del servidor (cuota): {type(e).__name__}: {e}")


def _vigente(e: dict | None, ahora: float) -> bool:
    """¿Toca esperar antes de volver a consultar? 30 min tras una imagen nueva; 15 min tras un fallo o si no había novedad."""
    if not e:
        return False
    espera = REFRESCO_S if (e["error"] is None and not e.get("sin_novedad")) else REINTENTO_S
    return ahora - e.get("ts_consulta", e["ts"]) < espera


def _respuesta(e: dict, ahora: float, **extra) -> dict:
    # "ts" identifica los DATOS (cambia solo con una imagen nueva): main.py guarda un registro por cada "ts" distinto
    return {"activo": True, "datos": e["datos"], "error": e["error"], "traza": traza(40), "ts": e["ts"],
            "sin_novedad": bool(e.get("sin_novedad")), **antiguedad(e["datos"]), **extra}


def obtener(lat: float, lon: float, aire_c: float | None = None, ahora: float | None = None) -> dict:
    """{"activo": bool, "datos": dict|None, "error": str|None, ...}. Una consulta cada 30 min como máximo (15 min si falló
    o si EUMETSAT no tenía imagen nueva); nunca lanza excepciones."""
    if not configurado():
        return {"activo": False, "datos": None, "error": None, "traza": []}
    ahora = time.time() if ahora is None else ahora
    clave = (round(lat, 2), round(lon, 2))
    with _CERROJO:
        e = _CACHE.get(clave)
        if _vigente(e, ahora):
            return _respuesta(e, ahora)
    ruta = None
    _paso("== Nueva consulta de satélite ==")
    ya = e.get("fin_barrido") if (e and e.get("datos")) else None
    try:
        if not disponible(): raise RuntimeError("Faltan los paquetes eumdac, netCDF4 o numpy (pip install -r requirements-satelite.txt)")
        res = _descargar_recorte(lat, lon, ya)
        if res is None:
            # Sin imagen nueva: se conserva la anterior tal cual (mismo "ts" => no se registra otra vez) y se reintenta en 15 min
            nuevo = {**e, "ts_consulta": ahora, "error": None, "sin_novedad": True}
            _paso(f"== Sin imagen nueva: se mantiene la anterior; nueva búsqueda en {REINTENTO_S // 60} min ==")
        else:
            ruta, instante, satelite = res
            datos = analizar_netcdf(ruta, instante, satelite, lat, lon, aire_c)
            nuevo = {"ts": ahora, "ts_consulta": ahora, "datos": datos, "error": None, "sin_novedad": False, "fin_barrido": instante}
            _paso("== Consulta terminada correctamente ==")
    except Exception as ex:
        try: diagnostico.registrar_excepcion("METEOSAT", ex)
        except Exception: pass
        _paso(f"== FALLO en esta consulta: {type(ex).__name__}: {ex} ==")
        # Se conservan los datos anteriores Y su "ts": así, si la siguiente búsqueda encuentra la misma imagen, no se registra dos veces
        nuevo = {"ts": e["ts"] if e else ahora, "ts_consulta": ahora, "datos": e["datos"] if e else None,
                 "error": f"{type(ex).__name__}: {ex}", "sin_novedad": False, "fin_barrido": e.get("fin_barrido") if e else None}
    finally:
        if ruta:
            shutil.rmtree(os.path.dirname(ruta), ignore_errors=True)
    with _CERROJO:
        _CACHE[clave] = nuevo
    return _respuesta(nuevo, ahora)


_HILO = {}          # clave -> hilo de consulta en marcha


def obtener_en_segundo_plano(lat: float, lon: float, aire_c: float | None = None, ahora: float | None = None) -> dict:
    """Como obtener(), pero NUNCA espera: devuelve al instante lo último que hay en caché y, si toca refrescar y no hay una
    consulta en marcha, lanza una en un hilo aparte. Así Data Tailor (minutos) no retrasa el ciclo principal ni el semáforo.
    Añade "consultando": True mientras haya una consulta en curso."""
    if not configurado():
        return {"activo": False, "datos": None, "error": None, "traza": [], "consultando": False}
    ahora = time.time() if ahora is None else ahora
    clave = (round(lat, 2), round(lon, 2))
    with _CERROJO:
        e = _CACHE.get(clave)
        hilo = _HILO.get(clave)
        en_marcha = hilo is not None and hilo.is_alive()
        if not _vigente(e, ahora) and not en_marcha:
            hilo = threading.Thread(target=obtener, args=(lat, lon, aire_c, ahora), name="meteosat", daemon=True)
            _HILO[clave] = hilo
            hilo.start()
            en_marcha = True
        if not e:
            return {"activo": True, "datos": None, "error": None, "traza": traza(40), "ts": None, "sin_novedad": False,
                    **antiguedad(None), "consultando": en_marcha}
        return _respuesta(e, ahora, consultando=en_marcha)
