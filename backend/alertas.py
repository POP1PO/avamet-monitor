import re
import time
from datetime import datetime

from . import avamet, config

def _num(valor: str) -> float:
    try:
        return float(str(valor).split()[0].replace(",", "."))
    except (ValueError, IndexError):
        return 0.0

def hay_lluvia_nueva(anterior, actual: float, umbral: float = 0.1) -> bool:
    """True si la lluvia acumulada de hoy subió al menos `umbral` mm desde la lectura anterior.
    `anterior=None` = sin línea base (arranque / tras un fallo): nunca cuenta como lluvia nueva.
    round(...,2) evita perder 0.1 mm por redondeo binario (p. ej. 0.3-0.2)."""
    return anterior is not None and round(actual - anterior, 2) >= umbral


def registrar_lluvia_estacion(seg: dict, eid: str, valor: float, ahora: float, umbral: float = 0.1) -> None:
    """Anota la lectura de "Lluvia hoy" de una estación y, si subió >= umbral respecto a la anterior, el instante.
    seg[eid] = {"valor": último acumulado, "ts_subida": epoch del último aumento (0.0 = ninguno visto)}.
    La primera lectura solo fija la línea base: no cuenta como aumento (igual que hay_lluvia_nueva)."""
    previo = seg.get(eid)
    ts = previo["ts_subida"] if previo else 0.0
    if previo is not None and hay_lluvia_nueva(previo["valor"], valor, umbral):
        ts = ahora
    seg[eid] = {"valor": valor, "ts_subida": ts}


def lluvia_reciente(seg: dict, eid: str, ahora: float, ventana_s: float | None = None) -> bool:
    """¿El acumulado de esta estación subió en los últimos `ventana_s` s (por defecto config.VENTANA_LLUVIA_RADAR_S)?"""
    ventana_s = config.VENTANA_LLUVIA_RADAR_S if ventana_s is None else ventana_s
    ts = (seg.get(eid) or {}).get("ts_subida", 0.0)
    return ts > 0 and 0 <= ahora - ts <= ventana_s


def cond_viento(viento: float, racha: float) -> int:
    """Condición de viento estilo Boltwood. Fuente única para evaluar() y boltwood.py.
    3 = muy ventoso (PELIGRO): viento > UMBRAL_VIENTO_PELIGRO_KMH o racha > UMBRAL_RACHA_PELIGRO_KMH
    2 = ventoso (VIGILANCIA):  viento >= UMBRAL_VIENTO_KMH       o racha >= UMBRAL_VIENTO_MAX_KMH
    1 = calma"""
    if viento > config.UMBRAL_VIENTO_PELIGRO_KMH or racha > config.UMBRAL_RACHA_PELIGRO_KMH:
        return 3
    if viento >= config.UMBRAL_VIENTO_KMH or racha >= config.UMBRAL_VIENTO_MAX_KMH:
        return 2
    return 1


def viento_fuerte_ahora(datos: dict) -> bool:
    """True si la lectura actual está en condición de viento 3 (PELIGRO)."""
    return cond_viento(_num(datos.get("Viento", "0")), _num(datos.get("Viento máximo", "0"))) == 3


def actualizar_retencion_viento(ultima_ts: float, ahora: float, fuerte_ahora: bool,
                                retencion_s: float | None = None) -> dict:
    """Histéresis de viento (función pura). Mientras no pasen `retencion_s` segundos desde el
    ÚLTIMO viento/racha en PELIGRO, se mantiene PELIGRO. Devuelve ts, ahora, vigente, hace_s."""
    retencion_s = config.RETENCION_VIENTO_S if retencion_s is None else retencion_s
    if fuerte_ahora:
        ultima_ts = ahora
    elif ultima_ts and (ahora - ultima_ts) >= retencion_s:
        ultima_ts = 0.0
    vigente = bool(ultima_ts) and (ahora - ultima_ts) < retencion_s
    return {"ts": ultima_ts, "ahora": bool(fuerte_ahora), "vigente": vigente,
            "hace_s": (ahora - ultima_ts) if ultima_ts else None}


def actualizar_retencion_lluvia(ultima_ts: float, info: dict, ahora: float,
                                central_ahora: bool, estaciones_ahora: list,
                                retencion_s: float | None = None) -> dict:
    """Histéresis de lluvia (función pura, sin estado global).

    Mientras no pasen `retencion_s` segundos desde el ÚLTIMO incremento de lluvia detectado,
    se sigue considerando que llueve. Devuelve:
      ts, info      nuevo estado a guardar (info = {"central": bool, "estaciones": [nombres]})
      ahora         True si en este ciclo se detectó un incremento
      vigente       True si la retención sigue activa (incluye el ciclo en que se detecta)
      hace_s        segundos desde el último incremento (None si no hay)
      central       lluvia en la central efectiva (detectada ahora o retenida)
      estaciones    estaciones del radio con lluvia efectiva (detectada ahora o retenida)
    """
    retencion_s = config.RETENCION_LLUVIA_S if retencion_s is None else retencion_s
    info = {"central": bool((info or {}).get("central")), "estaciones": list((info or {}).get("estaciones", []))}
    if ultima_ts and (ahora - ultima_ts) >= retencion_s:      # caducó: se olvida todo
        ultima_ts, info = 0.0, {"central": False, "estaciones": []}
    detectada = bool(central_ahora or estaciones_ahora)
    if detectada:
        ultima_ts = ahora
        if central_ahora:
            info["central"] = True
        for n in estaciones_ahora:
            if n not in info["estaciones"]:
                info["estaciones"].append(n)
    vigente = bool(ultima_ts) and (ahora - ultima_ts) < retencion_s
    return {
        "ts": ultima_ts, "info": info, "ahora": detectada, "vigente": vigente,
        "hace_s": (ahora - ultima_ts) if ultima_ts else None,
        "central": vigente and info["central"],
        "estaciones": list(info["estaciones"]) if vigente else [],
    }


def evaluar(central: dict, radio: list[dict], esta_lloviendo_central: bool, estaciones_lloviendo: list,
            lluvia_retenida_min: float | None = None,
            viento_retenido_min: float | None = None) -> tuple[str, list[dict]]:
    """`lluvia_retenida_min`: si no es None, la lluvia NO es de este ciclo sino retenida por la
    histéresis (minutos desde el último incremento). `viento_retenido_min`: igual para el viento
    (minutos desde el último viento/racha en PELIGRO); solo se usa si ahora el viento ya no lo es."""
    alertas: list[dict] = []
    nivel = "SEGURO"
    sufijo = ""
    if lluvia_retenida_min is not None:
        sufijo = f" [retenido: última lluvia hace {lluvia_retenida_min:.0f} min; PELIGRO hasta {config.RETENCION_LLUVIA_S // 60} min]"

    if esta_lloviendo_central:
        lluvia_central = _num(central.get("Lluvia hoy", "0"))
        alertas.append({"nivel": "CRITICO", "mensaje": f"Lluvia en estación central ({lluvia_central:.1f} mm acumulados){sufijo}"})
        nivel = "PELIGRO"

    for est_nombre in estaciones_lloviendo:
        dist = next((e["distancia_km"] for e in radio if e["nombre"] == est_nombre), None)
        donde = f"a {dist:.1f} km en {est_nombre}" if dist is not None else f"en {est_nombre}"
        alertas.append({"nivel": "PELIGRO", "mensaje": f"Lluvia {donde}{sufijo}"})
        nivel = "PELIGRO"

    viento = _num(central.get("Viento", "0"))
    viento_max = _num(central.get("Viento máximo", "0"))
    cv = cond_viento(viento, viento_max)
    if cv == 3:
        alertas.append({"nivel": "CRITICO", "mensaje": f"Viento muy fuerte: {viento:.0f} km/h (racha {viento_max:.0f}) — supera el límite de seguridad"})
        nivel = "PELIGRO"
    elif viento_retenido_min is not None:
        alertas.append({"nivel": "CRITICO", "mensaje": (
            f"Viento fuerte retenido: último viento/racha en peligro hace {viento_retenido_min:.0f} min "
            f"(PELIGRO hasta {config.RETENCION_VIENTO_S // 60} min); ahora {viento:.0f} km/h (racha {viento_max:.0f})")})
        nivel = "PELIGRO"
    elif cv == 2:
        alertas.append({"nivel": "AVISO", "mensaje": f"Viento fuerte: {viento:.0f} km/h (máx {viento_max:.0f})"})
        if nivel == "SEGURO": nivel = "VIGILANCIA"

    humedad = _num(central.get("Humedad", "0"))
    if humedad >= config.UMBRAL_HUMEDAD_PCT:
        alertas.append({"nivel": "AVISO", "mensaje": f"Humedad elevada: {humedad:.0f} %"})
        if nivel == "SEGURO": nivel = "VIGILANCIA"

    return nivel, alertas


NIVELES_CON_DATOS = ("SEGURO", "VIGILANCIA", "PELIGRO")


# ---- Estado de la ESTACIÓN (distinto del estado de la RED) -----------------------------------------------------------
# La red puede funcionar perfectamente y aun así el sensor de la estación elegida estar congelado: AVAMET sigue
# publicando su página, pero con una «Fecha lectura» antigua. Aquí se distingue lo uno de lo otro.
def estado_estacion(datos: dict, nombre: str = "", ahora: float | None = None) -> dict:
    """Clasifica la lectura de UNA estación según la edad de su «Fecha lectura» (límite: config.EDAD_MAX_DATOS_S, el mismo
    que usa evaluar_seguridad). Devuelve {"estado", "edad_min", "max_min", "nombre", "mensaje"} con estado:
      ACTIVA       lectura reciente (≤ límite)
      CONGELADA    la web responde y la lectura es válida, pero es más vieja que el límite: el sensor no está enviando datos
      SIN_FECHA    la lectura no trae fecha, o no se puede interpretar (o está en el futuro): no se sabe si es reciente
      SIN_LECTURA  AVAMET no devolvió una lectura completa de la estación
    """
    ahora = time.time() if ahora is None else ahora
    max_min = config.EDAD_MAX_DATOS_S // 60
    base = {"nombre": nombre, "max_min": max_min, "edad_min": None}
    if not avamet.datos_validos(datos):
        return {**base, "estado": "SIN_LECTURA", "mensaje": f"AVAMET no devolvió una lectura completa de {nombre or 'esta estación'}."}
    edad = avamet.edad_lectura_s(datos, ahora)
    if edad is None:
        return {**base, "estado": "SIN_FECHA",
                "mensaje": f"La fecha de la última lectura de {nombre or 'esta estación'} falta o no se puede interpretar, "
                           f"así que no se puede saber si es reciente."}
    edad_min = int(edad // 60)
    if edad > config.EDAD_MAX_DATOS_S:
        return {**base, "estado": "CONGELADA", "edad_min": edad_min,
                "mensaje": f"El sensor de esta estación lleva {edad_min} minutos sin enviar datos a AVAMET (aunque tu red funciona bien)."}
    return {**base, "estado": "ACTIVA", "edad_min": edad_min, "mensaje": ""}


def aviso_sin_datos_estacion(est: dict) -> str:
    """Texto del aviso SIN_DATOS cuando la causa es la estación (no la red). Vacío si la estación no es la causa."""
    nombre = est.get("nombre") or "esta estación"
    if est.get("estado") == "CONGELADA":
        return (f"⚠️ SIN DATOS FÍABLES: El sensor de {nombre} no se actualiza desde hace {est['edad_min']} min. "
                f"(Tu conexión a Internet y la web de AVAMET funcionan correctamente, pero este sensor está inactivo).")
    if est.get("estado") == "SIN_FECHA":
        return (f"⚠️ SIN DATOS FÍABLES: AVAMET no indica una fecha válida para la última lectura de {nombre}, "
                f"así que no se puede saber si es reciente. (Tu conexión a Internet y la web de AVAMET responden).")
    return ""


def estaciones_alternativas(radio: list, ahora: float | None = None, maximo: int = 6) -> list:
    """Estaciones del radio cuya lectura es reciente AHORA (para ofrecer un cambio cuando la central está congelada),
    de la más cercana a la más lejana: [{"id", "nombre", "distancia_km", "edad_min"}]."""
    ahora = time.time() if ahora is None else ahora
    res = []
    for est in radio or []:
        datos = est.get("datos") or {}
        if "Error" in datos:
            continue
        e = estado_estacion(datos, est.get("nombre", ""), ahora)
        if e["estado"] == "ACTIVA":
            res.append({"id": est.get("id"), "nombre": est.get("nombre", ""), "distancia_km": est.get("distancia_km"), "edad_min": e["edad_min"]})
    res.sort(key=lambda x: (x["distancia_km"] if isinstance(x["distancia_km"], (int, float)) else 1e9))
    return res[:maximo]


def evaluar_seguridad(estado: dict, datos: dict | None = None, ahora: float | None = None) -> tuple[bool, str]:
    """FUENTE ÚNICA DE VERDAD de "¿es seguro?". La usan Alpaca (main.es_seguro) y Boltwood.

    Devuelve (seguro, motivo). Motivos:
      OK              datos frescos y nivel SEGURO/VIGILANCIA
      NIVEL           datos frescos pero nivel PELIGRO
      SIN_DATOS       estado INICIANDO/ERROR/SIN_DATOS/APAGADO o lectura incompleta/errónea
      DATO_CADUCADO   la lectura de la estación es más vieja que config.EDAD_MAX_DATOS_S
      CICLO_CADUCADO  el bucle principal no actualiza ESTADO desde hace config.EDAD_MAX_CICLO_S
    `ahora` es un epoch en segundos (por defecto time.time()).
    """
    ahora = time.time() if ahora is None else ahora
    if estado.get("estado") not in NIVELES_CON_DATOS:
        return False, "SIN_DATOS"
    if datos is None:
        datos = (estado.get("central") or {}).get("datos") or {}
    if not avamet.datos_validos(datos):
        return False, "SIN_DATOS"
    edad = avamet.edad_lectura_s(datos, ahora)
    if edad is None or edad > config.EDAD_MAX_DATOS_S:
        return False, "DATO_CADUCADO"
    try:
        ts_ciclo = datetime.fromisoformat(estado["timestamp"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return False, "CICLO_CADUCADO"
    if ahora - ts_ciclo > config.EDAD_MAX_CICLO_S:
        return False, "CICLO_CADUCADO"
    if estado.get("estado") == "PELIGRO":
        return False, "NIVEL"
    return True, "OK"


_RE_LLUVIA_METAR = re.compile(r"Lluvia|Llovizna|Chubascos|Tormenta|Granizo|Nieve|Cinarra|Hielo")
RADIO_AVISO_AEROPUERTO_KM = 60.0


def avisos_lluvia_aeropuertos(aeropuertos: list, metars: dict, radio_km: float = RADIO_AVISO_AEROPUERTO_KM) -> list[str]:
    """Mensajes de los aeropuertos a <= radio_km cuyo METAR (el que se usa para calcular) informa lluvia, chubascos, tormenta...
    No dependen de hacia dónde se muevan las nubes. Si hay alguno, el nivel no debe quedarse en SEGURO (VIGILANCIA, sin alarma):
    el pluviómetro tarda en registrar el inicio de la lluvia y el aeropuerto avisa antes."""
    msgs = []
    for a in aeropuertos or []:
        m = (metars or {}).get(a.get("icao")) or {}
        fen = m.get("fenomenos")
        dist = a.get("distancia_km")
        if isinstance(fen, str) and isinstance(dist, (int, float)) and dist <= radio_km and _RE_LLUVIA_METAR.search(fen):
            msgs.append(f"{fen} en el aeropuerto {a.get('nombre') or a.get('icao')} a {dist:.0f} km")
    return msgs
