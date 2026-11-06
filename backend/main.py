import asyncio
import re
import threading
import traceback
import json
import time
import os
import signal
import sys
import random
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime
from math import radians, sin, cos, atan2, sqrt, degrees, asin
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from . import config
from . import avamet
from . import alertas
from . import telegram
from . import aeropuertos
from . import seeing
from . import meteosat
from . import satelite_informe
from . import nubes_ir
from . import database
from . import boltwood
from . import seguridad
from . import notificaciones
from . import vigilante
from . import apagado
from . import movimiento
from . import estaticos
from . import entorno
from . import diagnostico
from . import respaldo
from . import actualizacion

VERSION = "1.0.0"

ESTADO: dict = {"estado": "INICIANDO", "mensaje": "Arrancando…", "timestamp": None}
SUSCRIPTORES: list[asyncio.Queue] = []
DESPERTAR = asyncio.Event()

COORDS: dict = {}
ESTACION_CENTRAL_ID: str = config.ESTACION_CENTRAL_ID
ESTACION_CENTRAL_NOMBRE: str = config.ESTACION_CENTRAL_NOMBRE
ESTACIONES_CERCA: list[dict] = []
_AEROPUERTOS_CERCA: list[dict] = []
_RADIO_CACHE: list[dict] = []
_TS_RADIO: float = 0.0
_VERSION_UBICACION: int = 0
# Cuándo avisar por Telegram (PELIGRO, SIN_DATOS, datos recuperados) y cola de reintentos si no hay red.
GESTOR = notificaciones.GestorNotificaciones(
    enviar=lambda texto: telegram.enviar_a_todos_detalle(texto),
    demora_sin_datos_s=config.TELEGRAM_SIN_DATOS_DEMORA_S)
_ULTIMO_GUARDADO_BD: float = 0.0
_NINA_ULTIMO_PING: float = 0.0

# None = "sin línea base todavía": evita falsos positivos de lluvia al arrancar,
# tras un fallo de red o al añadir una estación nueva al radio.
_LLUVIA_ANTERIOR = {"central": None, "estaciones": {}}

# Estación de respaldo (backend/respaldo.py): estado de la conmutación principal <-> respaldo
_RESPALDO: dict = respaldo.estado_inicial()

def _buscar_respaldo(cercanas: list, preferida: str | None, ahora: float, maximo: int = 3) -> dict | None:
    """Primera estación cercana (dentro de config.RESPALDO_MAX_KM) con lectura ACTIVA ahora: {**cercana, "datos": ...}.
    Se lee en el momento (no se fía de la caché del radio). None si no hay ninguna."""
    for cand in respaldo.candidatos(cercanas, config.RESPALDO_MAX_KM, preferida, maximo):
        try:
            datos = avamet.obtener_datos_tiempo_real(cand["id"])
        except Exception as e:
            print(f"Respaldo: no se pudo leer {cand['id']} ({type(e).__name__}: {e})")
            continue
        if alertas.estado_estacion(datos, cand.get("nombre", ""), ahora)["estado"] == "ACTIVA":
            return {**cand, "datos": datos}
    return None

def _hay_lluvia_nueva(anterior, actual, umbral: float = 0.1) -> bool:
    return alertas.hay_lluvia_nueva(anterior, actual, umbral)

# Radar predictivo: por estación (da igual cuál sea la central), cuándo subió por última vez su "Lluvia hoy".
_SEGUIMIENTO_LLUVIA: dict = {}

# Histéresis de lluvia: instante (epoch) del ÚLTIMO incremento detectado y dónde fue.
# Mientras no pasen config.RETENCION_LLUVIA_S el estado se mantiene en PELIGRO.
_ULTIMA_LLUVIA_TS: float = 0.0
_ULTIMA_LLUVIA_INFO = {"central": False, "estaciones": []}

def _guardar_retencion_lluvia(estacion_id: str, ts: float | None = None, info: dict | None = None) -> None:
    """Persiste la retención para que un reinicio en plena tormenta NO la olvide.
    ts/info se pasan explícitos: así no se mezclan con los de otra estación si se cambia de ubicación a mitad."""
    ts = _ULTIMA_LLUVIA_TS if ts is None else ts
    info = _ULTIMA_LLUVIA_INFO if info is None else info
    try:
        ruta = Path(config.ESTADO_LLUVIA_PATH)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        tmp = ruta.with_name(ruta.name + ".tmp")
        tmp.write_text(json.dumps({"estacion_id": estacion_id, "ts": ts, **info}), encoding="utf-8")
        os.replace(tmp, ruta)
    except Exception as e:
        print(f"Lluvia: no se pudo guardar la retención ({e})")

def _cargar_retencion_lluvia() -> None:
    global _ULTIMA_LLUVIA_TS, _ULTIMA_LLUVIA_INFO
    try:
        d = json.loads(Path(config.ESTADO_LLUVIA_PATH).read_text(encoding="utf-8"))
        edad = time.time() - float(d["ts"])
        if d.get("estacion_id") == ESTACION_CENTRAL_ID and 0 <= edad < config.RETENCION_LLUVIA_S:
            _ULTIMA_LLUVIA_TS = float(d["ts"])
            _ULTIMA_LLUVIA_INFO = {"central": bool(d.get("central")), "estaciones": list(d.get("estaciones", []))}
            print(f"Lluvia: retención recuperada (último incremento hace {edad/60:.0f} min)")
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"Lluvia: no se pudo leer la retención ({e})")

# Histéresis de viento: instante (epoch) del ÚLTIMO viento/racha en PELIGRO (config.RETENCION_VIENTO_S).
_ULTIMO_VIENTO_FUERTE_TS: float = 0.0

def _guardar_retencion_viento(estacion_id: str, ts: float | None = None) -> None:
    """Persiste la retención de viento para que un reinicio no la olvide (ts explícito, ver lluvia)."""
    ts = _ULTIMO_VIENTO_FUERTE_TS if ts is None else ts
    try:
        ruta = Path(config.ESTADO_VIENTO_PATH)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        tmp = ruta.with_name(ruta.name + ".tmp")
        tmp.write_text(json.dumps({"estacion_id": estacion_id, "ts": ts}), encoding="utf-8")
        os.replace(tmp, ruta)
    except Exception as e:
        print(f"Viento: no se pudo guardar la retención ({e})")

def _cargar_retencion_viento() -> None:
    global _ULTIMO_VIENTO_FUERTE_TS
    try:
        d = json.loads(Path(config.ESTADO_VIENTO_PATH).read_text(encoding="utf-8"))
        edad = time.time() - float(d["ts"])
        if d.get("estacion_id") == ESTACION_CENTRAL_ID and 0 <= edad < config.RETENCION_VIENTO_S:
            _ULTIMO_VIENTO_FUERTE_TS = float(d["ts"])
            print(f"Viento: retención recuperada (último viento fuerte hace {edad/60:.0f} min)")
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"Viento: no se pudo leer la retención ({e})")

def _exigir_token(req: Request) -> None:
    """401 sin token válido; 429 tras demasiados intentos fallidos desde la misma IP."""
    cliente = req.client.host if getattr(req, "client", None) else "?"
    r = seguridad.comprobar(req.headers.get("authorization"), cliente)
    if r == "BLOQUEADO":
        raise HTTPException(429, "Demasiados intentos fallidos. Espera unos minutos.")
    if r != "OK":
        raise HTTPException(401, "Token inválido o ausente", headers={"WWW-Authenticate": "Bearer"})

def es_seguro() -> bool:
    """¿Es seguro abrir? Misma lógica que usa el fichero Boltwood (alertas.evaluar_seguridad)."""
    return alertas.evaluar_seguridad(ESTADO)[0]

async def _esperar_ciclo() -> None:
    try:
        await asyncio.wait_for(DESPERTAR.wait(), timeout=config.INTERVALO_CENTRAL)
        DESPERTAR.clear()
    except asyncio.TimeoutError:
        pass

# Candado + bandera de "apagando": una vez escrito el APAGADO final, ninguna escritura rezagada del bucle
# principal puede sobrescribirlo con un estado "seguro".
_BOLTWOOD_LOCK = threading.Lock()
_APAGANDO = False

def _boltwood_sync(estado: dict, central: dict | None = None, final: bool = False) -> None:
    global _APAGANDO
    with _BOLTWOOD_LOCK:
        if final:
            _APAGANDO = True
        elif _APAGANDO:
            return
        boltwood.escribir(estado, central)

def _boltwood_apagado() -> bool:
    """Escribe el estado APAGADO (C=1, A=1: techo a cerrar) y bloquea escrituras posteriores."""
    try:
        _boltwood_sync({"estado": "APAGADO"}, {}, final=True)
        return True
    except Exception as e:
        print(f"Boltwood: no se pudo escribir el estado APAGADO ({e})")
        return False

async def _escribir_boltwood(estado: dict, central: dict | None = None) -> None:
    if not config.BOLTWOOD_ENABLED:
        return
    try:
        await asyncio.to_thread(_boltwood_sync, estado, central)
    except Exception as e:                # un fallo del fichero nunca debe romper el bucle
        print(f"Boltwood: no se pudo escribir el fichero ({e})")

async def _broadcast(estado: dict) -> None:
    for q in list(SUSCRIPTORES): await q.put(estado)

def _haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = radians(lat1), radians(lat2)
    dp = radians(lat2 - lat1)
    dl = radians(lon2 - lon1)
    a = sin(dp/2)**2 + cos(p1)*cos(p2)*sin(dl/2)**2
    return 2 * R * atan2(sqrt(a), sqrt(1 - a))

def _bearing(lat1, lon1, lat2, lon2):
    f1, f2 = radians(lat1), radians(lat2)
    dl = radians(lon2 - lon1)
    y = sin(dl) * cos(f2)
    x = cos(f1) * sin(f2) - sin(f1) * cos(f2) * cos(dl)
    return (degrees(atan2(y, x)) + 360) % 360

def _recalcular_radio() -> None:
    global ESTACIONES_CERCA, _AEROPUERTOS_CERCA
    info = COORDS[ESTACION_CENTRAL_ID]
    lat0, lon0 = info["lat"], info["lon"]
    cercanas = []
    for eid, inf in COORDS.items():
        if eid == ESTACION_CENTRAL_ID: continue
        d = _haversine(lat0, lon0, inf["lat"], inf["lon"])
        if d <= config.RADIO_KM:
            cercanas.append({"id": eid, "nombre": inf["nombre"], "distancia_km": round(d, 1), "lat": inf["lat"], "lon": inf["lon"]})
    cercanas.sort(key=lambda x: x["distancia_km"])
    ESTACIONES_CERCA = cercanas
    _AEROPUERTOS_CERCA = aeropuertos.buscar_aeropuertos_cercanos(lat0, lon0, 120.0)

_estimar_movimiento_nubes = movimiento.estimar_movimiento_nubes   # lógica pura en backend/movimiento.py

def _fetch_radio_paralelo(estaciones: list[dict]) -> list[dict]:
    def fetch(est): return {**est, "datos": avamet.obtener_datos_tiempo_real(est["id"])}
    if not estaciones: return []
    with ThreadPoolExecutor(max_workers=10) as pool:
        return list(pool.map(fetch, estaciones))

def _procesar_red_arrastre(aero, lat0, lon0, movimiento, coords_todas):
    vel = max(movimiento["velocidad_kmh"], 10)
    distancia_total = aero["distancia_km"]
    eta_base = int((distancia_total / vel) * 60)
    d_lat = lat0 - aero["lat"]
    d_lon = lon0 - aero["lon"]
    dist_cuadrada = d_lat**2 + d_lon**2
    candidatas = []
    if dist_cuadrada > 0:
        for eid, info in coords_todas.items():
            s_lat = info["lat"] - aero["lat"]
            s_lon = info["lon"] - aero["lon"]
            t = (s_lat * d_lat + s_lon * d_lon) / dist_cuadrada
            if 0.1 < t < 0.9:
                proj_lat = aero["lat"] + t * d_lat
                proj_lon = aero["lon"] + t * d_lon
                dist_deg = sqrt((info["lat"] - proj_lat)**2 + (info["lon"] - proj_lon)**2)
                dist_km = dist_deg * 111.0
                if dist_km < 15.0:
                    dist_a_mi = _haversine(lat0, lon0, info["lat"], info["lon"])
                    candidatas.append({"id": eid, "nombre": info["nombre"], "lat": info["lat"], "lon": info["lon"], "dist_a_mi": dist_a_mi, "progreso": t, "dist_a_linea": dist_km})
    candidatas.sort(key=lambda x: x["progreso"])
    estaciones_camino = []
    for c in candidatas:
        if not estaciones_camino:
            estaciones_camino.append(c)
        else:
            dist_entre = (c["progreso"] - estaciones_camino[-1]["progreso"]) * distancia_total
            if dist_entre > 8.0: estaciones_camino.append(c)
    estaciones_camino = estaciones_camino[:6]
    confirmado = False
    eta_actualizado = eta_base
    if estaciones_camino:
        def fetch_est(est):
            try:
                d = avamet.obtener_datos_tiempo_real(est["id"])
                if "Error" in d:
                    raise ValueError(d["Error"])
                lluvia = float(str(d.get("Lluvia hoy", "0")).split()[0].replace(",", "."))
                ahora_e = time.time()
                alertas.registrar_lluvia_estacion(_SEGUIMIENTO_LLUVIA, est["id"], lluvia, ahora_e)
                # "lluvia" = hay lluvia RECIENTE (acumulado subido en los últimos 30 min); el acumulado va aparte
                return {**est, "lluvia": lluvia if alertas.lluvia_reciente(_SEGUIMIENTO_LLUVIA, est["id"], ahora_e) else 0.0,
                        "lluvia_hoy": lluvia}
            except Exception as e:           # sin dato NO es "0 mm": se marca como desconocido (None)
                print(f"Radar largo alcance: no se pudo leer {est['id']} ({type(e).__name__}: {e})")
                return {**est, "lluvia": None, "lluvia_hoy": None}
        with ThreadPoolExecutor(max_workers=6) as pool:
            resultados = list(pool.map(fetch_est, estaciones_camino))
        for res in resultados:
            if res and (res["lluvia"] or 0) > 0: 
                confirmado = True
                if int((res["dist_a_mi"] / vel) * 60) < eta_actualizado:
                    eta_actualizado = int((res["dist_a_mi"] / vel) * 60)
        estaciones_camino = resultados
    return {"confirmado": confirmado, "eta": eta_actualizado, "estaciones_camino": estaciones_camino}

async def _preparar_entorno() -> None:
    global COORDS, ESTACION_CENTRAL_ID, ESTACION_CENTRAL_NOMBRE
    tiene_cache = avamet.CACHE_FILE.exists() and avamet.CACHE_FILE.stat().st_size > 100
    if tiene_cache:
        ESTADO.update({"estado": "INICIANDO", "mensaje": "Cargando coordenadas..."})
    else:
        ESTADO.update({"estado": "INICIANDO", "mensaje": "Iniciando descarga...", "progreso": 0})
    await _broadcast(ESTADO)

    async def _actualizar_progreso():
        while avamet.PROGRESO_DESCARGAS["activo"]:
            actual = avamet.PROGRESO_DESCARGAS["actual"]
            total = avamet.PROGRESO_DESCARGAS["total"]
            ESTADO.update({"estado": "INICIANDO", "mensaje": f"Descargando: {actual}/{total}...", "progreso": int((actual / total) * 100) if total > 0 else 0})
            await _broadcast(ESTADO)
            await asyncio.sleep(0.5)

    tarea_progreso = asyncio.create_task(_actualizar_progreso())
    try:
        COORDS = await asyncio.to_thread(avamet.obtener_coordenadas_todas)
    except BaseException:
        # Sin red: el hilo de descarga deja "activo" a True y la tarea de progreso no terminaría nunca.
        avamet.PROGRESO_DESCARGAS["activo"] = False
        tarea_progreso.cancel()
        raise
    await tarea_progreso

    if ESTACION_CENTRAL_ID not in COORDS:
        for eid, info in COORDS.items():
            if ESTACION_CENTRAL_NOMBRE.lower() in info.get("nombre", "").lower():
                ESTACION_CENTRAL_ID = eid
                break
        else:
            ESTADO.update({"estado": "ERROR", "mensaje": "Estación no encontrada."})
            await _broadcast(ESTADO)
            return
            
    ESTACION_CENTRAL_NOMBRE = COORDS[ESTACION_CENTRAL_ID].get("municipio") or COORDS[ESTACION_CENTRAL_ID].get("nombre", ESTACION_CENTRAL_ID)
    _recalcular_radio()
    ESTADO.update({"estado": "INICIANDO", "mensaje": f"{len(ESTACIONES_CERCA)} estaciones en {config.RADIO_KM:.0f} km. Leyendo datos…", "progreso": None})
    await _broadcast(ESTADO)

async def _notificar_si_cambia(nivel: str, lista_alertas: list[dict], motivo: str = "") -> None:
    try:
        await GESTOR.procesar(nivel, lista_alertas, ESTACION_CENTRAL_NOMBRE, motivo)
    except Exception as e:             # un fallo de Telegram NO debe pasar el sistema a SIN_DATOS
        diagnostico.registrar_excepcion("TELEGRAM", e, "notificaciones")
        print(f"Telegram: error en las notificaciones ({type(e).__name__}: {e})")

async def _al_fallar_bucle(exc: BaseException, intento: int, espera_s: float) -> None:
    """El bucle principal ha muerto o terminado: estado NO SEGURO en todas partes y aviso por Telegram."""
    global ESTADO
    if isinstance(exc, vigilante.TerminoInesperado):
        detalle = str(ESTADO.get("mensaje") or exc)           # p. ej. "Estación no encontrada."
    else:
        detalle = f"{type(exc).__name__}: {exc}"
        traza = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__, limit=6))
        print(f"Vigilante: el bucle principal falló (intento {intento}):\n{telegram._limpiar(traza)}")
    detalle = telegram._limpiar(detalle)[:200]
    diagnostico.registrar("BUCLE", type(exc).__name__, f"el bucle principal falló (intento {intento}): {detalle}")
    print(f"Vigilante: se considera NO SEGURO; reintento en {espera_s:g} s ({detalle})")
    ESTADO = {**ESTADO, "estado": "SIN_DATOS", "progreso": None, "estacion": {"estado": "DESCONOCIDA", "mensaje": ""},
              "mensaje": f"Fallo interno: {detalle}. Reintentando en {espera_s:g} s",
              "alertas": [{"nivel": "CRITICO", "mensaje": f"Fallo interno: {detalle}. Se considera NO SEGURO."}]}
    await _broadcast(ESTADO)
    await _escribir_boltwood(ESTADO, {})
    await _notificar_si_cambia("SIN_DATOS", [], f"Fallo interno del programa ({detalle})")

async def _guardian() -> None:
    """Segunda red de seguridad, independiente del bucle principal.

    Cubre lo que el vigilante no ve: un bucle COLGADO (no muerto) y las esperas entre reintentos. Cada
    GUARDIAN_PERIODO_S revisa el estado y, si es NO SEGURO (sin datos, o el bucle lleva más de EDAD_MAX_CICLO_S
    sin actualizar), reescribe el fichero Boltwood para que el consumidor no vea una hora vieja "segura" y avisa por
    Telegram. NUNCA escribe un estado seguro: solo refresca los inseguros.
    """
    while True:
        await asyncio.sleep(config.GUARDIAN_PERIODO_S)
        try:
            seguro, motivo = alertas.evaluar_seguridad(ESTADO)
            if motivo == "CICLO_CADUCADO":
                await _escribir_boltwood(ESTADO)
                await _notificar_si_cambia("SIN_DATOS", [], "El bucle principal no responde (CICLO_CADUCADO)")
            elif motivo == "SIN_DATOS" and ESTADO.get("estado") in ("INICIANDO", "ERROR", "SIN_DATOS"):
                await _escribir_boltwood(ESTADO, {})          # refresca la hora del fichero (sigue siendo no seguro)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"Guardián: error ({type(e).__name__}: {e})")

def _tarea_murio(t: asyncio.Task) -> None:
    if not t.cancelled() and t.exception() is not None:
        e = t.exception()
        print(f"Tarea {t.get_name()} terminó con error: {type(e).__name__}: {telegram._limpiar(str(e))}")

def _ubicacion_cambio(id_snap: str, version_snap: int) -> bool:
    """True si, desde que empezó el ciclo, el usuario ha cambiado de estación central."""
    return id_snap != ESTACION_CENTRAL_ID or version_snap != _VERSION_UBICACION

async def _bucle_principal() -> None:
    global ESTADO, _RADIO_CACHE, _TS_RADIO, _ULTIMO_GUARDADO_BD, _LLUVIA_ANTERIOR, _ULTIMA_LLUVIA_TS, _ULTIMA_LLUVIA_INFO, _ULTIMO_VIENTO_FUERTE_TS, _RESPALDO

    await _escribir_boltwood({"estado": "INICIANDO"}, {})     # sobrescribe cualquier fichero viejo "seguro"
    await _preparar_entorno()
    if ESTADO.get("estado") == "ERROR":
        await _escribir_boltwood(ESTADO, {})
        return
    _cargar_retencion_lluvia()
    _cargar_retencion_viento()
    
    while True:
        try:
            id_snap = ESTACION_CENTRAL_ID
            nombre_snap = ESTACION_CENTRAL_NOMBRE
            cerca_snap = ESTACIONES_CERCA
            version_snap = _VERSION_UBICACION
            aero_snap = _AEROPUERTOS_CERCA        # misma lista de aeropuertos durante TODO el ciclo

            datos_principal = await asyncio.to_thread(avamet.obtener_datos_tiempo_real, id_snap)
            ahora = time.time()

            # ---- Estación de respaldo: si la principal no da señal de vida, se usa la cercana más próxima que sí la dé ----
            # (fuente_* = de dónde salen los datos de este ciclo: la principal o el respaldo)
            datos_central, fuente_id, fuente_nombre, respaldo_info = datos_principal, id_snap, nombre_snap, None
            sin_respaldo = ""             # explicación si la principal no da datos y tampoco hay respaldo válido
            if config.RESPALDO_MAX_KM > 0:
                est_principal = alertas.estado_estacion(datos_principal, nombre_snap, ahora)
                fecha_p = datos_principal.get("Fecha lectura") if isinstance(datos_principal, dict) else None
                previo_id = _RESPALDO.get("usando_id") if _RESPALDO.get("principal_id") == id_snap else None
                _RESPALDO, accion = respaldo.decidir(_RESPALDO, id_snap, est_principal["estado"] == "ACTIVA", fecha_p,
                                                     ahora, config.RESPALDO_LECTURAS_VUELTA)
                if accion in (respaldo.CAMBIO, respaldo.RESPALDO):
                    # Si la principal ni siquiera responde (posible caída de AVAMET/Internet) se prueba solo 1 candidata
                    maximo = 1 if (isinstance(datos_principal, dict) and "Error" in datos_principal) else 3
                    elegido = await asyncio.to_thread(_buscar_respaldo, cerca_snap, previo_id, ahora, maximo)
                    if _ubicacion_cambio(id_snap, version_snap): continue
                    motivo_p = {"CONGELADA": f"última lectura hace {est_principal.get('edad_min')} min",
                                "SIN_FECHA": "su lectura no trae una fecha válida",
                                "SIN_LECTURA": "AVAMET no devuelve su lectura"}.get(est_principal["estado"], est_principal["estado"])
                    if elegido:
                        datos_central, fuente_id = elegido["datos"], elegido["id"]
                        fuente_nombre = (COORDS.get(fuente_id) or {}).get("municipio") or elegido.get("nombre") or fuente_id
                        dist = elegido.get("distancia_km")
                        respaldo_info = {
                            "activo": True,
                            "principal": {"id": id_snap, "nombre": nombre_snap, "estado": est_principal["estado"], "motivo": motivo_p},
                            "usando": {"id": fuente_id, "nombre": fuente_nombre, "distancia_km": dist},
                            "desde": datetime.fromtimestamp(_RESPALDO["desde_ts"]).isoformat(timespec="seconds"),
                            "lecturas_nuevas_principal": len(_RESPALDO["fechas_nuevas"]),
                            "lecturas_para_volver": config.RESPALDO_LECTURAS_VUELTA,
                            "mensaje": respaldo.mensaje(nombre_snap, {"nombre": fuente_nombre, "distancia_km": dist}, motivo_p)}
                        if fuente_id != previo_id:
                            _txt = f"{nombre_snap} no envía datos ({motivo_p}); se usa {fuente_nombre} ({fuente_id}, a {dist} km)"
                            print(f"Respaldo: {_txt}")
                            diagnostico.registrar("AVAMET", "RESPALDO", _txt)
                        _RESPALDO["usando_id"], _RESPALDO["sin_candidato"] = fuente_id, False
                    else:
                        sin_respaldo = f"Ninguna estación a menos de {config.RESPALDO_MAX_KM:g} km envía datos ahora (sin respaldo)."
                        if not _RESPALDO.get("sin_candidato"):
                            print(f"Respaldo: {nombre_snap} no envía datos ({motivo_p}) y {sin_respaldo[0].lower()}{sin_respaldo[1:]}")
                            diagnostico.registrar("AVAMET", "SIN_RESPALDO", f"{nombre_snap} no envía datos ({motivo_p}). {sin_respaldo}")
                        _RESPALDO["usando_id"], _RESPALDO["sin_candidato"] = None, True
                elif accion == respaldo.VUELTA:
                    _previo = ((COORDS.get(previo_id) or {}).get("municipio") or previo_id) if previo_id else ""
                    _txt = (f"{nombre_snap} vuelve a enviar datos ({config.RESPALDO_LECTURAS_VUELTA} lecturas nuevas): "
                            f"se deja el respaldo {_previo}").strip()
                    print(f"Respaldo: {_txt}")
                    diagnostico.registrar("AVAMET", "RESPALDO_FIN", _txt)

            if not avamet.datos_validos(datos_central):
                if "Error" in datos_central:
                    # Fallo de red o de la web: se averigua POR QUÉ (¿sin Internet?, ¿AVAMET caído?) y se deja en el log.
                    diag = await asyncio.to_thread(diagnostico.diagnosticar, "avamet", datos_central.get("TipoError", ""),
                                                   str(datos_central.get("Error", "")))
                    _motivo = diag["mensaje"]
                else:
                    _motivo = "Lectura incompleta (¿ha cambiado el HTML de AVAMET?)"
                    diagnostico.registrar("AVAMET", "LECTURA_INCOMPLETA", f"estación {id_snap}: la web responde pero faltan datos")
                    diagnostico.marcar("avamet", True)
                if sin_respaldo:
                    _motivo = f"{_motivo.rstrip('.')}. {sin_respaldo}"
                ESTADO = {**ESTADO, "estado": "SIN_DATOS", "progreso": None, "mensaje": _motivo, "red": diagnostico.estado_red(),
                          "estacion": {"estado": "DESCONOCIDA", "nombre": nombre_snap, "mensaje": ""},
                          "respaldo": {"activo": False, **({"sin_respaldo": sin_respaldo} if sin_respaldo else {})},
                          "alertas": [{"nivel": "CRITICO", "mensaje": f"Sin datos fiables. Motivo: {_motivo}. Se considera NO SEGURO."}]}
                await _broadcast(ESTADO)
                await _escribir_boltwood(ESTADO, datos_central)
                await _notificar_si_cambia("SIN_DATOS", [], _motivo)
                await _esperar_ciclo()
                continue

            diagnostico.marcar("avamet", True)       # una lectura correcta implica Internet y AVAMET operativos

            if ahora - _ULTIMO_GUARDADO_BD > 300:
                await asyncio.to_thread(database.guardar_lectura, fuente_id, datos_central)   # con el id de quien la midió
                _ULTIMO_GUARDADO_BD = ahora

            if (ahora - _TS_RADIO >= config.INTERVALO_RADIO) or not _RADIO_CACHE:
                nuevo_cache = await asyncio.to_thread(_fetch_radio_paralelo, cerca_snap)
                if id_snap != ESTACION_CENTRAL_ID or version_snap != _VERSION_UBICACION: continue
                _RADIO_CACHE = nuevo_cache
                _TS_RADIO = ahora
            if id_snap != ESTACION_CENTRAL_ID or version_snap != _VERSION_UBICACION: continue

            # M3: todos los `await` (METAR, tendencias, radar) van ANTES de tocar el estado global (línea base de
            # lluvia, retenciones). Si el usuario cambia de ubicación durante ellos, el ciclo se abandona entero.
            icaos = [a["icao"] for a in aero_snap]
            metars = await asyncio.to_thread(aeropuertos.obtener_metars, icaos)     # incluye caché del último dato bueno
            metars_calculo = aeropuertos.utiles_para_calculo(metars)               # para predecir solo se usan los recientes
            aviso_metar = ""
            if icaos:
                fallo_metar = aeropuertos.ultimo_error()
                if fallo_metar:
                    # La API de METAR falló: ¿es Internet, o solo aviationweather.gov?
                    diag_m = await asyncio.to_thread(diagnostico.diagnosticar, "metar", fallo_metar["tipo"], fallo_metar["detalle"])
                    en_cache = any(m.get("desactualizado") for m in metars.values())
                    aviso_metar = diag_m["mensaje"] + (". Se muestran los últimos METAR guardados." if en_cache else ". Sin datos de aeropuertos por ahora.")
                else:
                    diagnostico.marcar("aeropuertos", True)
            # Seeing (7Timer, pronóstico externo): 1 consulta/hora en un hilo; nunca bloquea ni cambia el semáforo
            try:
                seeing_info = await asyncio.to_thread(seeing.obtener, COORDS[id_snap]["lat"], COORDS[id_snap]["lon"])
            except Exception as e_see:
                seeing_info = {"datos": None, "error": f"{type(e_see).__name__}: {e_see}"}
            # (el registro histórico de satélite se guarda justo después de la consulta, ver más abajo)
            # Satélite (Meteosat, OPCIONAL: solo si hay credenciales EUMETSAT en el entorno); orientativo y fuera del semáforo
            meteosat_info = {"activo": False, "datos": None, "error": None}
            if meteosat.configurado():
                try:
                    _m = re.search(r"-?\d+(?:\.\d+)?", str(datos_central.get("Temperatura", "")))
                    meteosat_info = meteosat.obtener_en_segundo_plano(COORDS[id_snap]["lat"], COORDS[id_snap]["lon"],
                                                                      float(_m.group()) if _m else None)   # no espera: consulta en un hilo
                except Exception as e_met:
                    meteosat_info = {"activo": True, "datos": None, "error": f"{type(e_met).__name__}: {e_met}"}
                # Modo sombra: cada consulta NUEVA con datos se guarda con lo que medía la estación (no cambia ningún nivel)
                global _ULTIMO_TS_METEOSAT
                _ts_m = meteosat_info.get("ts")
                if _ts_m and meteosat_info.get("datos") and meteosat_info.get("error") is None and _ts_m != _ULTIMO_TS_METEOSAT:
                    _ULTIMO_TS_METEOSAT = _ts_m
                    # con el id de la estación que dio los datos: en respaldo no se mezcla con el informe de la principal
                    await asyncio.to_thread(database.guardar_satelite, fuente_id, meteosat_info["datos"], datos_central)
            movimiento = _estimar_movimiento_nubes(datos_central, metars_calculo, aero_snap)
            
            tendencias = await asyncio.to_thread(database.obtener_tendencia, fuente_id, 30)   # nunca mezcla dos estaciones
            if _ubicacion_cambio(id_snap, version_snap): continue
            alertas_tendencia = []
            if tendencias["delta_presion"] < -2:
                alertas_tendencia.append("Caída brusca de presión (-2 hPa en 30 min). Posible tormenta.")
            if tendencias["delta_temperatura"] < -3:
                alertas_tendencia.append("Caída brusca de temperatura (-3°C en 30 min). Llegada de frente.")
            if tendencias["delta_humedad"] > 15:
                alertas_tendencia.append("Aumento repentino de humedad. Aire saturándose.")

            alertas_predictivas = []
            radares = []
            estaciones_lloviendo_ahora = []
            lat0, lon0 = COORDS[id_snap]["lat"], COORDS[id_snap]["lon"]
            
            # El seguimiento se actualiza SIEMPRE (haya o no viento), para que "subió en 30 min" sea fiable
            for est in _RADIO_CACHE:
                if "Error" not in est["datos"]:
                    alertas.registrar_lluvia_estacion(_SEGUIMIENTO_LLUVIA, est["id"], alertas._num(est["datos"].get("Lluvia hoy", "0")), ahora)
            if movimiento["velocidad_kmh"] > 5:
                for est in _RADIO_CACHE:
                    if "Error" in est["datos"]:
                        continue
                    lluvia_est = alertas._num(est["datos"].get("Lluvia hoy", "0"))
                    # Solo cuenta si su acumulado SUBIÓ en los últimos 30 min (no basta con "ha llovido hoy")
                    if alertas.lluvia_reciente(_SEGUIMIENTO_LLUVIA, est["id"], ahora):
                        bearing_hacia_mi = _bearing(est["lat"], est["lon"], lat0, lon0)
                        diff = abs(bearing_hacia_mi - movimiento["grados_destino"])
                        if diff > 180: diff = 360 - diff
                        
                        if diff < 30:
                            horas = est["distancia_km"] / movimiento["velocidad_kmh"]
                            minutos = int(horas * 60)
                            if minutos < 120:
                                alertas_predictivas.append(f"Lluvia en {est['nombre']} a {est['distancia_km']} km. Llegada en ~{minutos} min.")
                                radares.append({
                                    "nombre": est["nombre"], "lat": est["lat"], "lon": est["lon"],
                                    "distancia": est["distancia_km"], "eta": minutos
                                })

            radares_aeropuertos = []
            if movimiento["velocidad_kmh"] > 5:
                for aero in aero_snap:
                    m = metars_calculo.get(aero["icao"], {})
                    if not m: continue            # METAR sin temperatura sigue valiendo para fenómenos y nubes
                    
                    tiempo_malo = m.get("fenomenos") != "Ninguno"
                    nubes_bajas = len(m.get("nubosidad", {}).get("baja", [])) > 0
                    
                    if tiempo_malo or nubes_bajas:
                        bearing_hacia_mi = _bearing(aero["lat"], aero["lon"], lat0, lon0)
                        diff = abs(bearing_hacia_mi - movimiento["grados_destino"])
                        if diff > 180: diff = 360 - diff
                        
                        if diff < 45:
                            info_arrastre = await asyncio.to_thread(_procesar_red_arrastre, aero, lat0, lon0, movimiento, COORDS)
                            
                            motivo = "Lluvia/Tormenta" if tiempo_malo else "Nubes bajas"
                            texto_alerta = f"[Largo Alcance] {motivo} en {aero['nombre']}."
                            if info_arrastre["confirmado"]:
                                texto_alerta = f"[CONFIRMADO EN TIERRA] {motivo} en {aero['nombre']}."
                            else:
                                texto_alerta += f" (Sin confirmar)."

                            alertas_predictivas.append(f"{texto_alerta} Llegada en ~{info_arrastre['eta']} min.")
                            radares_aeropuertos.append({
                                "nombre": aero["nombre"], "lat": aero["lat"], "lon": aero["lon"],
                                "distancia": aero["distancia_km"], "eta": info_arrastre["eta"], 
                                "motivo": motivo, "confirmado": info_arrastre["confirmado"],
                                "estaciones_camino": info_arrastre["estaciones_camino"]
                            })

            if _ubicacion_cambio(id_snap, version_snap): continue
            # ---- Desde aquí hasta construir ESTADO NO hay ningún `await`: nada puede cambiar la ubicación a mitad ----
            if _LLUVIA_ANTERIOR.get("central_id") != fuente_id:
                # Cambio principal <-> respaldo: cada estación lleva su propio acumulado de «Lluvia hoy»; compararlos daría
                # una «lluvia nueva» falsa (o la ocultaría). Esta lectura solo fija la nueva línea base.
                _LLUVIA_ANTERIOR["central"] = None
                _LLUVIA_ANTERIOR["central_id"] = fuente_id
            lluvia_actual = alertas._num(datos_central.get("Lluvia hoy", "0"))
            esta_lloviendo_central = _hay_lluvia_nueva(_LLUVIA_ANTERIOR["central"], lluvia_actual)
            _LLUVIA_ANTERIOR["central"] = lluvia_actual
            for est in _RADIO_CACHE:
                if "Error" in est["datos"]:
                    continue                      # no tocar la línea base con datos inválidos
                lluvia_est = alertas._num(est["datos"].get("Lluvia hoy", "0"))
                if _hay_lluvia_nueva(_LLUVIA_ANTERIOR["estaciones"].get(est["id"]), lluvia_est):
                    estaciones_lloviendo_ahora.append(est["nombre"])
                _LLUVIA_ANTERIOR["estaciones"][est["id"]] = lluvia_est

            nina_conectado = (time.time() - _NINA_ULTIMO_PING) < 15
            ret = alertas.actualizar_retencion_lluvia(_ULTIMA_LLUVIA_TS, _ULTIMA_LLUVIA_INFO, ahora,
                                                      esta_lloviendo_central, estaciones_lloviendo_ahora)
            _ULTIMA_LLUVIA_TS, _ULTIMA_LLUVIA_INFO = ret["ts"], ret["info"]
            solo_retenida = ret["vigente"] and not ret["ahora"]
            rv = alertas.actualizar_retencion_viento(_ULTIMO_VIENTO_FUERTE_TS, ahora,
                                                     alertas.viento_fuerte_ahora(datos_central))
            _ULTIMO_VIENTO_FUERTE_TS = rv["ts"]
            viento_retenido = rv["vigente"] and not rv["ahora"]
            nivel, lista_alertas = alertas.evaluar(
                datos_central, _RADIO_CACHE, ret["central"], ret["estaciones"],
                lluvia_retenida_min=(ret["hace_s"] / 60.0) if solo_retenida else None,
                viento_retenido_min=(rv["hace_s"] / 60.0) if viento_retenido else None)
            if "Presión descartada" in datos_central:      # aviso, NO cambia el nivel
                lista_alertas.append({"nivel": "AVISO", "mensaje": f"Presión fuera de rango ({datos_central['Presión descartada']}): ignorada; el resto de datos sigue evaluándose"})
            
            if alertas_predictivas and nivel == "SEGURO":
                nivel = "VIGILANCIA"
            msgs_aero = alertas.avisos_lluvia_aeropuertos(aero_snap, metars_calculo)
            for ma in msgs_aero:
                lista_alertas.append({"nivel": "AVISO", "mensaje": ma})
            if msgs_aero and nivel == "SEGURO":
                nivel = "VIGILANCIA"
            if aviso_metar:
                lista_alertas.append({"nivel": "AVISO", "mensaje": aviso_metar})
            for apt in alertas_tendencia: lista_alertas.append({"nivel": "AVISO", "mensaje": apt})
            for apr in alertas_predictivas: lista_alertas.append({"nivel": "PREDICCION", "mensaje": apr})

            # Insignia coherente con Alpaca/Boltwood: si la lectura de la estación está caducada, el estado NO puede
            # ser SEGURO/VIGILANCIA (es_seguro() sería False). PELIGRO se mantiene: también es "no seguro".
            # Estado de la ESTACIÓN (≠ estado de la red): si AVAMET responde pero el sensor está congelado, se dice con claridad.
            if respaldo_info:
                lista_alertas.append({"nivel": "AVISO", "mensaje": respaldo_info["mensaje"]})
            estacion = alertas.estado_estacion(datos_central, fuente_nombre, ahora)
            aviso_caducado = ""
            if nivel in ("SEGURO", "VIGILANCIA") and not alertas.evaluar_seguridad(
                    {"estado": nivel, "timestamp": datetime.now().isoformat(timespec="seconds")}, datos_central, ahora)[0]:
                aviso_caducado = alertas.aviso_sin_datos_estacion(estacion) or (
                    f"La última lectura de la estación es demasiado antigua (máximo {config.EDAD_MAX_DATOS_S // 60} min). Se considera NO SEGURO.")
                if sin_respaldo:
                    aviso_caducado = f"{aviso_caducado} {sin_respaldo}"
                nivel = "SIN_DATOS"
                lista_alertas = [{"nivel": "CRITICO", "mensaje": aviso_caducado}] + lista_alertas
            ESTADO = {
                "estado": nivel, **({"mensaje": aviso_caducado} if nivel == "SIN_DATOS" else {}), "timestamp": datetime.now().isoformat(timespec="seconds"),
                # «central» = la estación cuyos datos se usan en este ciclo (la principal o, si no da señal de vida, el respaldo)
                "central": {"id": fuente_id, "nombre": fuente_nombre, "municipio": COORDS[fuente_id].get("municipio", ""), "lat": COORDS[fuente_id]["lat"], "lon": COORDS[fuente_id]["lon"], "datos": datos_central},
                "respaldo": respaldo_info or {"activo": False, **({"sin_respaldo": sin_respaldo} if sin_respaldo else {})},
                "radio": _RADIO_CACHE, "alertas": lista_alertas,
                "aeropuertos": [ {**a, "metar": metars.get(a["icao"], {})} for a in aero_snap ],
                "movimiento_nubes": movimiento,
                "seeing": seeing_info,
                "meteosat": meteosat_info,
                "tendencias": tendencias,
                "radares": radares,
                "radares_aeropuertos": radares_aeropuertos,
                "nina_conectado": nina_conectado,
                "red": diagnostico.estado_red(),
                "estacion": {**estacion, **({"alternativas": alertas.estaciones_alternativas(_RADIO_CACHE, ahora)}
                                            if estacion["estado"] in ("CONGELADA", "SIN_FECHA") else {})},
                "config": {"radio_km": config.RADIO_KM, "intervalo_central": config.INTERVALO_CENTRAL, "intervalo_radio": config.INTERVALO_RADIO},
                "flags": {"lluvia_central": bool(ret["central"]), "lluvia_radio": bool(ret["estaciones"]),
                          "lluvia_ahora": bool(ret["ahora"]), "lluvia_retenida": bool(solo_retenida),
                          "lluvia_hace_s": ret["hace_s"],
                          "viento_peligro": bool(rv["vigente"]), "viento_retenido": bool(viento_retenido),
                          "viento_hace_s": rv["hace_s"], "prediccion": bool(alertas_predictivas)},
                "progreso": None
            }
            # Persistencia con los valores de ESTE ciclo (no los globals, que podrían ser ya de otra estación)
            if ret["ahora"]:
                await asyncio.to_thread(_guardar_retencion_lluvia, id_snap, ret["ts"], ret["info"])
            if rv["ahora"]:
                await asyncio.to_thread(_guardar_retencion_viento, id_snap, rv["ts"])
            if _ubicacion_cambio(id_snap, version_snap): continue   # api_ubicacion ya dejó ESTADO en INICIANDO
            await _broadcast(ESTADO)
            await _escribir_boltwood(ESTADO, datos_central)
            if not _ubicacion_cambio(id_snap, version_snap):
                await _notificar_si_cambia(nivel, lista_alertas, aviso_caducado if nivel == "SIN_DATOS" else "")
        except Exception as e:
            print(f"Error en bucle principal: {e}")
            diagnostico.registrar("BUCLE", type(e).__name__, str(e))
            # Una excepción NUNCA deja el nivel anterior como válido: se pasa a SIN_DATOS.
            ESTADO = {**ESTADO, "estado": "SIN_DATOS", "mensaje": f"Error interno: {e}", "estacion": {"estado": "DESCONOCIDA", "mensaje": ""},
                      "alertas": [{"nivel": "CRITICO", "mensaje": f"Error interno: {e}. Se considera NO SEGURO."}]}
            await _broadcast(ESTADO)
            await _escribir_boltwood(ESTADO)
            await _notificar_si_cambia("SIN_DATOS", [], f"Error interno: {e}")
        await _esperar_ciclo()

_TAREAS_FONDO: list = []   # vigilante, guardián y bot de Telegram (ver lifespan y _reiniciar_telegram)

@asynccontextmanager
async def lifespan(app: FastAPI):
    seguridad.obtener_token()      # lo crea la primera vez y avisa de dónde está
    try:
        await asyncio.to_thread(database.init_db)      # M5: ya no se hace al importar el módulo
    except Exception as e:
        print(f"⚠️  No se pudo preparar la base de datos del historial ({type(e).__name__}: {e}). La app sigue sin historial.")
    tareas = [
        asyncio.create_task(vigilante.supervisar(
            "bucle principal", _bucle_principal, _al_fallar_bucle,
            espera_inicial_s=config.VIGILANTE_ESPERA_INICIAL_S, espera_max_s=config.VIGILANTE_ESPERA_MAX_S), name="vigilante"),
        asyncio.create_task(_guardian(), name="guardian"),
        asyncio.create_task(telegram.bucle_suscripciones(), name="telegram"),
    ]
    asyncio.get_running_loop().run_in_executor(None, actualizacion.consultar)   # comprueba si hay versión nueva al arrancar (en segundo plano; nunca lanza)
    for t in tareas: t.add_done_callback(_tarea_murio)
    _TAREAS_FONDO[:] = tareas              # accesibles para reiniciar el bot de Telegram al guardar la configuración
    yield
    tareas = list(_TAREAS_FONDO)           # (puede incluir el bot reiniciado)
    for t in tareas: t.cancel()
    # M8: esperar (máx. 3 s) a que las tareas terminen de cancelarse antes de escribir el APAGADO, para que ninguna
    # pueda escribir después. Los hilos de red ya lanzados (hasta 25 s) no se pueden interrumpir: no se esperan
    # (run.py sale con apagado.terminar_ya al terminar uvicorn).
    await asyncio.wait(tareas, timeout=3)
    # Cierre ordenado: que el fichero Boltwood NO se quede diciendo "seguro" con la app parada.
    if config.BOLTWOOD_ENABLED:
        _boltwood_apagado()

app = FastAPI(title=f"AVAMET Monitor v{VERSION}", lifespan=lifespan)

if getattr(sys, 'frozen', False): BASE_DIR = Path(sys._MEIPASS)
else: BASE_DIR = Path(__file__).parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"

# Cache-busting: index.html y sw.js se sirven con __BUILD__ sustituido por un hash del contenido del frontend
# y SIEMPRE con "no-cache" (el navegador revalida en cada visita); vendor/ lleva ?v=BUILD y se cachea a largo plazo.
FRONTEND = estaticos.Frontend(FRONTEND_DIR, VERSION)
# Cabeceras anti-caché del HTML, del Service Worker y del manifest: el navegador nunca reutiliza una copia local.
_SIN_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}

_faltan_vendor = FRONTEND.vendor_faltante()
if _faltan_vendor:
    print("⚠️  Faltan librerías del frontend en frontend/vendor/: " + ", ".join(_faltan_vendor) +
          "\n    Sin ellas el mapa y las gráficas dependen de internet. Ejecuta:  python scripts/descargar_vendor.py")

@app.get("/")
async def index():
    return Response(FRONTEND.render("index.html"), media_type="text/html; charset=utf-8", headers=_SIN_CACHE)

@app.get("/manifest.json")
async def manifest(): return FileResponse(FRONTEND_DIR / "manifest.json", headers=_SIN_CACHE)

@app.get("/sw.js")
async def sw():
    return Response(FRONTEND.render("sw.js"), media_type="text/javascript; charset=utf-8", headers=_SIN_CACHE)

@app.get("/vendor/{ruta:path}")
async def vendor(ruta: str):
    r = FRONTEND.resolver_vendor(ruta)
    if r is None: raise HTTPException(404, "No encontrado.")
    return FileResponse(r[0], media_type=r[1], headers={"Cache-Control": "public, max-age=31536000, immutable"})

def _es_loopback(req: Request) -> bool:
    """True si la petición viene de ESTE equipo (127.0.0.1 / ::1)."""
    host = getattr(getattr(req, "client", None), "host", "") or ""
    return host in ("127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1")

@app.get("/api/modo")
async def api_modo(req: Request):
    """Para la pestaña «Configuración». Solo se ofrece en modo escritorio Y a quien abre la página en este mismo
    equipo; en headless o desde otro PC devuelve {"configuracion": false} y no revela rutas. Nunca devuelve secretos:
    solo si cada variable está rellena (sí/no)."""
    if config.HEADLESS or not _es_loopback(req):
        return {"configuracion": False}
    carpeta = Path(config.DATA_DIR)
    env_path = carpeta / ".env"
    def _hay(nombre): return bool(os.getenv(nombre, "").strip())
    return {
        "configuracion": True,
        "exe": bool(getattr(sys, "frozen", False)),
        "carpeta_datos": str(carpeta),
        "env_existe": env_path.is_file(),
        "token_api_existe": Path(config.API_TOKEN_PATH).is_file(),
        "ruta_token_api": str(config.API_TOKEN_PATH),
        "telegram_token": _hay("AVAMET_TELEGRAM_TOKEN"),
        "telegram_clave": len(os.getenv("AVAMET_TELEGRAM_CLAVE", "").strip()) >= telegram.CLAVE_MIN,
        "admin_id": _hay("AVAMET_ADMIN_CHAT_ID"),
        "puerto": config.PORT,
    }

# ---- Diagnóstico de red (misma protección que Configuración: solo escritorio y solo desde este equipo) -----------------
@app.get("/api/diagnostico/log")
async def api_diagnostico_log(req: Request):
    """Descarga diagnostico.log: los eventos de fallo de la última hora (AVAMET, METAR, Telegram...)."""
    _exigir_configuracion_local(req, escritura=False)
    texto = await asyncio.to_thread(diagnostico.texto_descarga, VERSION)
    return Response(texto, media_type="text/plain; charset=utf-8",
                    headers={**_SIN_CACHE, "Content-Disposition": 'attachment; filename="diagnostico.log"'})

@app.get("/api/actualizacion")
async def api_actualizacion(req: Request):
    """¿Hay una versión nueva en GitHub? Solo avisa (no descarga ni instala nada). Solo desde este equipo."""
    _exigir_configuracion_local(req, escritura=False)
    return await asyncio.to_thread(actualizacion.consultar)

@app.post("/api/red/comprobar")
async def api_red_comprobar(req: Request):
    """Botón «Comprobar ahora»: sondea Internet, AVAMET y aviationweather.gov y devuelve el estado de red."""
    _exigir_configuracion_local(req, escritura=True)
    return await asyncio.to_thread(diagnostico.comprobar_todo)

# ---- Formulario de Configuración (solo escritorio y solo desde este equipo) ------------------------------------------
def _hosts_locales() -> set:
    p = config.PORT
    return {f"127.0.0.1:{p}", f"localhost:{p}", f"[::1]:{p}"}

def _exigir_configuracion_local(req: Request, escritura: bool) -> None:
    """403 salvo que la petición sea del PROPIO equipo y de la propia página. Protege contra:
      - otros equipos de la red (cliente no loopback),
      - páginas web ajenas abiertas en tu navegador (cabecera Origin distinta; en escritura además se exige la cabecera
        X-AVAMET-Config, que un sitio ajeno no puede enviar sin permiso CORS),
      - DNS rebinding (la cabecera Host debe ser 127.0.0.1 / localhost con nuestro puerto)."""
    if config.HEADLESS:
        raise HTTPException(403, "La configuración desde la web solo está disponible en modo escritorio.")
    if not _es_loopback(req):
        raise HTTPException(403, "Solo se puede configurar desde el propio equipo (127.0.0.1).")
    host = (req.headers.get("host") or "").lower()
    if host not in _hosts_locales():
        raise HTTPException(403, "Abre la aplicación como http://127.0.0.1:%d para configurarla." % config.PORT)
    origen = (req.headers.get("origin") or "").lower()
    if origen and origen not in {"http://" + h for h in _hosts_locales()}:
        raise HTTPException(403, "Origen no permitido.")
    if escritura and req.headers.get("x-avamet-config") != "1":
        raise HTTPException(403, "Petición no permitida.")

def _ruta_env() -> Path:
    return Path(config.DATA_DIR) / ".env"

def _env_con_prioridad(claves) -> list:
    """Otros .env que config.py carga ANTES que el de la carpeta de datos y que ya definen alguna de estas claves:
    tras reiniciar, mandarían ellos (python-dotenv no pisa lo ya cargado)."""
    destino = _ruta_env().resolve()
    res = []
    for cand in (Path.cwd() / ".env", Path(config.RAIZ_PROYECTO) / ".env"):
        try:
            if cand.resolve() != destino and entorno.claves_definidas_en(cand, claves):
                res.append(str(cand))
        except OSError:
            pass
    return res

def _estado_telegram() -> dict:
    ok, motivo = telegram.configuracion_valida()
    return {"activo": ok, "motivo": motivo}

@app.get("/api/configuracion")
async def api_configuracion_leer(req: Request):
    """Estado actual para rellenar el formulario. Nunca devuelve la contraseña ni el token completo."""
    _exigir_configuracion_local(req, escritura=False)
    token = os.getenv("AVAMET_TELEGRAM_TOKEN", "").strip()
    ruta = _ruta_env()
    return {
        "archivo": str(ruta),
        "env_existe": ruta.is_file(),
        "token_configurado": bool(token),
        "token_mascara": entorno.enmascarar_token(token),
        "clave_configurada": bool(os.getenv("AVAMET_TELEGRAM_CLAVE", "").strip()),
        "admin_id": os.getenv("AVAMET_ADMIN_CHAT_ID", "").strip(),
        "telegram": _estado_telegram(),
        "eumetsat": {"clave_configurada": bool(os.getenv("EUMETSAT_CONSUMER_KEY", "").strip()),
                     "secreto_configurado": bool(os.getenv("EUMETSAT_CONSUMER_SECRET", "").strip()),
                     "clave_mascara": entorno.enmascarar_clave(os.getenv("EUMETSAT_CONSUMER_KEY", "").strip()),
                     "activo": meteosat.configurado(), "paquetes": meteosat.paquetes_instalados()},
    }

async def _reiniciar_telegram() -> None:
    """Para el bot de Telegram (si corría) y lo arranca de nuevo con las variables de entorno actuales."""
    viejo = next((t for t in _TAREAS_FONDO if t.get_name() == "telegram"), None)
    if viejo is not None and not viejo.done():
        viejo.cancel()
        await asyncio.wait([viejo], timeout=2)
    nuevo = asyncio.create_task(telegram.bucle_suscripciones(), name="telegram")
    nuevo.add_done_callback(_tarea_murio)
    if viejo in _TAREAS_FONDO:
        _TAREAS_FONDO[_TAREAS_FONDO.index(viejo)] = nuevo
    else:
        _TAREAS_FONDO.append(nuevo)

@app.post("/api/configuracion")
async def api_configuracion_guardar(req: Request, payload: dict = Body(...)):
    """Guarda token / contraseña / Admin ID en el .env de la carpeta de datos y los aplica sin reiniciar la aplicación."""
    _exigir_configuracion_local(req, escritura=True)
    try:
        cambios = entorno.validar_campos(payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
    try:
        ruta = await asyncio.to_thread(entorno.actualizar_env, _ruta_env(), cambios)
    except OSError as e:
        raise HTTPException(500, f"No se pudo escribir {_ruta_env()}: {type(e).__name__}: {telegram._limpiar(str(e))}")
    cambio_bot = any(k in cambios for k in ("AVAMET_TELEGRAM_TOKEN", "AVAMET_TELEGRAM_CLAVE"))
    for k, v in cambios.items():
        os.environ[k] = v                      # admin y contraseña se leen en cada uso; el token, al reiniciar el bot
    if cambio_bot:
        await _reiniciar_telegram()
    if any(k.startswith("EUMETSAT_") for k in cambios):
        meteosat.limpiar_cache()                 # la próxima vuelta del bucle consulta con las claves nuevas
    avisos = []
    previos = _env_con_prioridad(cambios.keys())
    if previos:
        avisos.append("Hay otro .env con prioridad que ya define estas variables y, tras reiniciar la aplicación, mandaría sobre "
                      "el guardado: " + ", ".join(previos))
    print("Configuración guardada desde la web: " + ", ".join(sorted(cambios)) + f" -> {ruta}")
    return {"ok": True, "actualizados": sorted(cambios), "archivo": str(ruta), "bot_reiniciado": cambio_bot,
            "telegram": _estado_telegram(), "avisos": avisos,
            "eumetsat": {"activo": meteosat.configurado(), "paquetes": meteosat.paquetes_instalados()}}

_ULTIMO_TS_METEOSAT = None


@app.get("/api/meteosat/informe")
async def api_meteosat_informe(req: Request, dias: int = 30):
    """Comparación satélite <-> estación de los últimos `dias` días (modo sombra). Solo desde el equipo local."""
    _exigir_configuracion_local(req, escritura=False)
    dias = max(1, min(int(dias), 365))
    filas = await asyncio.to_thread(database.leer_satelite, ESTACION_CENTRAL_ID, dias)
    return satelite_informe.informe(filas, radio_km=meteosat.RADIO_AVISO_KM)


@app.get("/api/meteosat/traza")
async def api_meteosat_traza(req: Request):
    """Pasos de las últimas consultas de satélite (texto plano, sin claves). Solo desde el equipo local."""
    _exigir_configuracion_local(req, escritura=False)
    return Response("\n".join(meteosat.traza()) or "Sin consultas todavía.", media_type="text/plain; charset=utf-8")

@app.get("/api/nubes_ir")
async def api_nubes_ir():
    """Capa «Nubes IR» del mapa: servidor WMS, capa y hora del último barrido de EUMETView (sin claves; ver nubes_ir.py)."""
    return await asyncio.to_thread(nubes_ir.obtener)

@app.get("/api/historial")
async def api_historial():
    return await asyncio.to_thread(database.obtener_historial_24h, ESTACION_CENTRAL_ID)

@app.get("/stream")
async def stream():
    q: asyncio.Queue = asyncio.Queue()
    SUSCRIPTORES.append(q)
    async def generar():
        try:
            yield f"data: {json.dumps(ESTADO, ensure_ascii=False)}\n\n"
            while True:
                try:
                    nuevo = await asyncio.wait_for(q.get(), timeout=config.SSE_LATIDO_S)
                except asyncio.TimeoutError:
                    # Latido: el navegador sabe así que la conexión sigue viva aunque no haya datos nuevos (el estado se
                    # emite cada ~60 s). Si llegara a cortarse sin avisar (microcorte de WiFi), el latido deja de llegar y el
                    # watchdog de la página reconecta.
                    yield "event: ping\ndata: 1\n\n"
                    continue
                yield f"data: {json.dumps(nuevo, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError: pass
        finally:
            if q in SUSCRIPTORES: SUSCRIPTORES.remove(q)
    return StreamingResponse(generar(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.get("/api/localidades")
async def api_localidades():
    if not COORDS: raise HTTPException(503, "Cargando...")
    return [{"id": eid, "nombre": info["nombre"], "municipio": info.get("municipio", ""), "lat": info["lat"], "lon": info["lon"]} for eid, info in COORDS.items()]

@app.post("/api/ubicacion")
async def api_ubicacion(req: Request, payload: dict = Body(...)):
    global ESTACION_CENTRAL_ID, ESTACION_CENTRAL_NOMBRE, _TS_RADIO, _VERSION_UBICACION, _LLUVIA_ANTERIOR, _ULTIMA_LLUVIA_TS, _ULTIMA_LLUVIA_INFO, _ULTIMO_VIENTO_FUERTE_TS, ESTADO, _RADIO_CACHE
    _exigir_token(req)
    if not COORDS: raise HTTPException(503, "Cargando datos...")
    eid = payload.get("estacion_id")
    lat, lon = payload.get("lat"), payload.get("lon")
    if eid:
        if eid not in COORDS: raise HTTPException(404, "No encontrada.")
    elif lat is not None and lon is not None:
        res = avamet.encontrar_mas_cercana(float(lat), float(lon), COORDS)
        if not res: raise HTTPException(404, "No hay cercana.")
        eid = res[0]
    else: raise HTTPException(400, "Faltan datos.")

    ESTACION_CENTRAL_ID = eid
    ESTACION_CENTRAL_NOMBRE = COORDS[eid].get("municipio") or COORDS[eid].get("nombre", eid)
    
    _LLUVIA_ANTERIOR = {"central": None, "estaciones": {}}
    _ULTIMA_LLUVIA_TS, _ULTIMA_LLUVIA_INFO = 0.0, {"central": False, "estaciones": []}
    _ULTIMO_VIENTO_FUERTE_TS = 0.0
    
    _recalcular_radio()
    _TS_RADIO = 0.0
    _RADIO_CACHE = []
    _VERSION_UBICACION += 1
    GESTOR.reiniciar_peligro()
    # Hasta que llegue la primera lectura de la NUEVA estación, el estado es NO SEGURO (INICIANDO): ni Alpaca ni
    # el fichero Boltwood pueden seguir diciendo "seguro" con los datos de la estación anterior.
    ESTADO = {"estado": "INICIANDO", "mensaje": f"Cambiando de ubicación a {ESTACION_CENTRAL_NOMBRE}…",
              "timestamp": None, "progreso": None}
    await _broadcast(ESTADO)
    await _escribir_boltwood({"estado": "INICIANDO"}, {})
    DESPERTAR.set()
    return {"ok": True, "central": {"id": eid, "nombre": COORDS[eid].get("nombre", eid), "municipio": COORDS[eid].get("municipio", "")}, "estaciones_en_radio": len(ESTACIONES_CERCA)}

@app.post("/api/shutdown")
async def apagar_servidor(req: Request, background_tasks: BackgroundTasks):
    _exigir_token(req)
    def enviar_senal_apagado():
        time.sleep(1)                       # deja que la respuesta HTTP llegue al navegador
        # os._exit NO ejecuta el lifespan: el APAGADO hay que escribirlo aquí, antes de salir.
        # Así N.I.N.A. ve "no seguro" al instante y cierra el techo.
        if config.BOLTWOOD_ENABLED:
            if _boltwood_apagado():
                print("Boltwood: escrito estado APAGADO (no seguro) antes de salir")
        print("Apagando AVAMET Monitor (salida inmediata)…")
        apagado.terminar_ya(0)          # mata el proceso ya, sin esperar a ningún hilo
    background_tasks.add_task(enviar_senal_apagado)
    return {"ok": True, "mensaje": "Apagándose..."}

# ============================================================
# ENDPOINTS ASCOM ALPACA (Para N.I.N.A.)
# ============================================================
def _client_tx(req: Request) -> int:
    """ClientTransactionID de la petición. Los nombres de parámetros Alpaca no distinguen mayúsculas, y un valor no
    numérico o negativo ya no provoca un 500 (B1): se usa 0, como cuando falta."""
    for clave, valor in req.query_params.items():
        if str(clave).lower() == "clienttransactionid":
            try:
                n = int(str(valor).strip())
            except ValueError:
                return 0
            return n if 0 <= n <= 4294967295 else 0
    return 0

def _alpaca_respuesta(valor, req: Request):
    client_tx = _client_tx(req)
    server_tx = random.randint(1, 1000000)
    return {"Value": valor, "ErrorNumber": 0, "ErrorMessage": "", "ClientTransactionID": client_tx, "ServerTransactionID": server_tx}

def _alpaca_ok(req: Request):
    client_tx = _client_tx(req)
    server_tx = random.randint(1, 1000000)
    return {"ErrorNumber": 0, "ErrorMessage": "", "ClientTransactionID": client_tx, "ServerTransactionID": server_tx}

@app.get("/management/apiversions")
async def alpaca_apiversions():
    return {"Value": [1], "ErrorNumber": 0, "ErrorMessage": ""}

@app.get("/management/v1/description")
async def alpaca_mgmt_description():
    return {
        "Value": {
            "ServerName": "AVAMET Monitor",
            "Manufacturer": "AVAMET Monitor",
            "ManufacturerVersion": VERSION,
            "Location": "Local"
        },
        "ErrorNumber": 0,
        "ErrorMessage": ""
    }

@app.get("/management/v1/configureddevices")
async def alpaca_configured_devices():
    return {"Value": [{"DeviceName": "AVAMET Safety Monitor", "DeviceType": "SafetyMonitor", "DeviceNumber": 0, "UniqueID": "avamet-safetymonitor-001"}], "ErrorNumber": 0, "ErrorMessage": ""}

# --- Estado de conexión ASCOM ---
# Estándar: IsSafe lanza NotConnectedException si el dispositivo no está conectado. En Alpaca eso es una respuesta
# HTTP 200 con ErrorNumber 0x407 (1031) y SIN "Value". El estado es único para todo el dispositivo y se pierde al
# reiniciar la aplicación (el cliente debe volver a conectar).
ALPACA_ERR_NO_CONECTADO = 0x407
ALPACA_ERR_VALOR_INVALIDO = 0x401
_ALPACA_CONECTADO: bool = False
_ALPACA_ULTIMO_AVISO_S: float = 0.0

def _alpaca_error(numero: int, mensaje: str, req: Request) -> dict:
    return {"ErrorNumber": numero, "ErrorMessage": mensaje, "ClientTransactionID": _client_tx(req),
            "ServerTransactionID": random.randint(1, 1000000)}

def alpaca_conectado() -> bool:
    return _ALPACA_CONECTADO or not config.ALPACA_ESTRICTO

@app.get("/api/v1/safetymonitor/0/issafe")
async def alpaca_issafe(req: Request):
    global _NINA_ULTIMO_PING, _ALPACA_ULTIMO_AVISO_S
    if not alpaca_conectado():
        ahora = time.time()
        if ahora - _ALPACA_ULTIMO_AVISO_S > 60:          # un aviso por minuto como mucho, para no llenar el log
            _ALPACA_ULTIMO_AVISO_S = ahora
            print("Alpaca: IsSafe rechazado (NotConnected 0x407): el cliente no ha enviado PUT connected=true. "
                  "Si tu cliente gestiona la conexión por su cuenta, usa AVAMET_ALPACA_ESTRICTO=0.")
        return _alpaca_error(ALPACA_ERR_NO_CONECTADO,
                             "El monitor de seguridad no está conectado: envía PUT connected=true antes de consultar IsSafe.", req)
    _NINA_ULTIMO_PING = time.time()                       # el indicador N.I.N.A. solo se enciende si se le RESPONDE
    return _alpaca_respuesta(es_seguro(), req)

@app.get("/api/v1/safetymonitor/0/connected")
async def alpaca_connected(req: Request): return _alpaca_respuesta(alpaca_conectado(), req)

@app.put("/api/v1/safetymonitor/0/connected")
async def alpaca_connect(req: Request):
    """PUT con cuerpo application/x-www-form-urlencoded: Connected=True|False (el nombre no distingue mayúsculas)."""
    global _ALPACA_CONECTADO, _NINA_ULTIMO_PING
    from urllib.parse import parse_qsl
    cuerpo = (await req.body()).decode("utf-8", errors="replace")
    params = {k.lower(): v for k, v in list(parse_qsl(cuerpo, keep_blank_values=True)) + list(req.query_params.items())}
    valor = str(params.get("connected", "")).strip().lower()
    if valor not in ("true", "false"):
        return Response("Falta el parámetro Connected o no es True/False.", status_code=400, media_type="text/plain; charset=utf-8")
    _ALPACA_CONECTADO = (valor == "true")
    if not _ALPACA_CONECTADO:
        _NINA_ULTIMO_PING = 0.0
    print(f"Alpaca: cliente {'CONECTADO' if _ALPACA_CONECTADO else 'DESCONECTADO'}")
    return _alpaca_ok(req)
@app.put("/api/v1/safetymonitor/0/connecting")
async def alpaca_connecting(req: Request): return _alpaca_ok(req)
@app.get("/api/v1/safetymonitor/0/connecting")
async def alpaca_get_connecting(req: Request): return _alpaca_respuesta(False, req)
@app.get("/api/v1/safetymonitor/0/disconnecting")
async def alpaca_get_disconnecting(req: Request): return _alpaca_respuesta(False, req)
@app.get("/api/v1/safetymonitor/0/description")
async def alpaca_description(req: Request): return _alpaca_respuesta("AVAMET Monitor", req)
@app.get("/api/v1/safetymonitor/0/driverinfo")
async def alpaca_driverinfo(req: Request): return _alpaca_respuesta("AVAMET Monitor", req)
@app.get("/api/v1/safetymonitor/0/driverversion")
async def alpaca_driverversion(req: Request): return _alpaca_respuesta(VERSION, req)
@app.get("/api/v1/safetymonitor/0/interfaceversion")
async def alpaca_interfaceversion(req: Request): return _alpaca_respuesta(1, req)
@app.get("/api/v1/safetymonitor/0/name")
async def alpaca_name(req: Request): return _alpaca_respuesta("AVAMET Safety Monitor", req)
@app.get("/api/v1/safetymonitor/0/supportedactions")
async def alpaca_supportedactions(req: Request): return _alpaca_respuesta([], req)