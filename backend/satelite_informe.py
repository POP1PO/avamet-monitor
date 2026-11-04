"""Informe de comparación satélite <-> estación (modo sombra): ¿el satélite avisó antes de que lloviera en la estación?

Funciones puras sobre la lista de registros de database.leer_satelite(). NO cambia ningún nivel de alerta.
Definiciones (todas ajustables y SIN validar):
  - Aviso de satélite: estado CUBIERTO o POSIBLE NUBOSIDAD, o mínima de 10,8 µm DENTRO DEL RADIO DE AVISO (columna min_radio,
    meteosat.RADIO_AVISO_KM) por debajo de UMBRAL_MIN_REGIONAL_C. Antes se usaba la mínima de todo el recorte (~334 x 290 km):
    dio 39 falsas alarmas de 39 (vigilancia del 2-3 nov 2026). Las filas anteriores no tienen min_radio y solo cuentan por estado.
  - Episodio de lluvia: el acumulado «lluvia hoy» de la estación sube al menos LLUVIA_MIN_MM entre dos registros consecutivos separados
    como máximo SEPARACION_MAX_MIN minutos (el acumulado vuelve a 0 a medianoche: ese salto hacia abajo no cuenta).
  - Acierto: hay un aviso en los VENTANA_MIN minutos anteriores al episodio. Antelación = minutos entre el aviso más antiguo de esa
    ventana y el episodio. Falsa alarma: un aviso sin episodio de lluvia en los VENTANA_MIN minutos siguientes.
Resolución: los registros están separados ~30 min, así que la antelación tiene esa resolución y los episodios cortos se pueden perder.
"""
from datetime import datetime

LLUVIA_MIN_MM = 0.2
VENTANA_MIN = 120
SEPARACION_MAX_MIN = 90
UMBRAL_MIN_REGIONAL_C = -20.0
ESTADOS_AVISO = ("CUBIERTO", "POSIBLE NUBOSIDAD")


def _t(fila) -> datetime:
    return datetime.fromisoformat(fila["timestamp"])


def es_aviso(fila: dict) -> bool:
    if (fila.get("estado") or "") in ESTADOS_AVISO: return True
    m = fila.get("min_radio")
    return m is not None and m < UMBRAL_MIN_REGIONAL_C


def episodios_lluvia(filas: list) -> list:
    """Índices de las filas en las que la estación empieza a registrar lluvia nueva."""
    res = []
    for i in range(1, len(filas)):
        a, b = filas[i - 1].get("lluvia_hoy"), filas[i].get("lluvia_hoy")
        if a is None or b is None: continue
        if (_t(filas[i]) - _t(filas[i - 1])).total_seconds() / 60 > SEPARACION_MAX_MIN: continue
        if b - a >= LLUVIA_MIN_MM: res.append(i)
    return res


def informe(filas: list, radio_km: float | None = None) -> dict:
    filas = sorted(filas, key=lambda f: f["timestamp"])
    n = len(filas)
    if n < 2:
        return {"registros": n, "mensaje": "Todavía no hay datos suficientes: deja la aplicación funcionando con el satélite activado."}
    dias = (_t(filas[-1]) - _t(filas[0])).total_seconds() / 86400
    eps = episodios_lluvia(filas)
    avisos = [i for i, f in enumerate(filas) if es_aviso(f)]
    detalle, aciertos = [], 0
    for e in eps:
        t_ep = _t(filas[e])
        previos = [i for i in avisos if i < e and (t_ep - _t(filas[i])).total_seconds() / 60 <= VENTANA_MIN]
        if previos:
            aciertos += 1
            ant = round((t_ep - _t(filas[previos[0]])).total_seconds() / 60)
        else:
            ant = None
        detalle.append({"episodio_utc": filas[e]["timestamp"], "avisado": bool(previos), "antelacion_min": ant,
                        "t108": filas[e].get("t108"), "estado": filas[e].get("estado")})
    falsas = 0
    for i in avisos:
        t_i = _t(filas[i])
        if not any(0 <= (_t(filas[e]) - t_i).total_seconds() / 60 <= VENTANA_MIN for e in eps):
            falsas += 1
    ants = [d["antelacion_min"] for d in detalle if d["antelacion_min"] is not None]
    res = {"registros": n, "dias": round(dias, 1), "episodios_lluvia": len(eps), "episodios_avisados": aciertos,
           "avisos_totales": len(avisos), "falsas_alarmas": falsas,
           "antelacion_media_min": round(sum(ants) / len(ants)) if ants else None,
           "detalle": detalle[-30:]}
    if not eps:
        res["mensaje"] = "No ha llovido en la estación durante este periodo: aún no se puede evaluar la capacidad de aviso."
    elif len(eps) < 10:
        res["mensaje"] = f"Solo {len(eps)} episodios de lluvia: es muy pronto para sacar conclusiones fiables."
    res["registros_sin_radio"] = sum(1 for f in filas if f.get("min_radio") is None)
    res["definiciones"] = {"lluvia_min_mm": LLUVIA_MIN_MM, "ventana_min": VENTANA_MIN, "umbral_min_regional_c": UMBRAL_MIN_REGIONAL_C,
                           "radio_km": radio_km}
    return res
