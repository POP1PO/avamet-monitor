"""Estación de respaldo: si la estación principal deja de dar señal de vida, se usan los datos de la estación cercana
más próxima que SÍ esté enviando datos, y se vuelve a la principal en cuanto vuelve a publicar.

Reglas (decididas el 3-nov-2026):
- La principal "deja de dar señal de vida" cuando su lectura no es ACTIVA (alertas.estado_estacion): sensor congelado más
  de config.EDAD_MAX_DATOS_S, lectura sin fecha válida o AVAMET no devuelve una lectura completa.
- El respaldo es la estación más cercana a la principal, dentro de config.RESPALDO_MAX_KM, cuya lectura sea ACTIVA.
  Si ya se estaba usando una, se mantiene mientras siga activa (no se salta de una a otra en cada ciclo).
- Se vuelve a la principal cuando ha publicado config.RESPALDO_LECTURAS_VUELTA lecturas NUEVAS (horas de lectura
  distintas y recientes). Con una sola lectura suelta no basta: evita ir y volver si el sensor da datos a ratos.
- Si no hay ninguna estación de respaldo válida, el comportamiento es el de siempre: SIN DATOS => NO SEGURO.

Este módulo es lógica pura (sin red ni estado global): main.py la llama en cada ciclo y guarda el estado devuelto.
"""

PRINCIPAL = "PRINCIPAL"     # la principal está bien: se usa ella
CAMBIO = "CAMBIO"           # la principal acaba de dejar de dar datos: pasar al respaldo
RESPALDO = "RESPALDO"       # seguir con el respaldo (la principal aún no se ha recuperado del todo)
VUELTA = "VUELTA"           # la principal ha vuelto: en este ciclo ya se usa ella


def estado_inicial(principal_id: str | None = None) -> dict:
    return {"principal_id": principal_id, "en_respaldo": False, "desde_ts": None, "fecha_al_caer": None,
            "fechas_nuevas": [], "usando_id": None}


def decidir(estado: dict | None, principal_id: str, principal_activa: bool, fecha_principal: str | None,
            ahora: float, lecturas_vuelta: int = 2) -> tuple[dict, str]:
    """Devuelve (nuevo_estado, acción). `fecha_principal` es la «Fecha lectura» de la principal tal cual la da AVAMET
    (o None si no hay lectura). No modifica `estado`: devuelve una copia."""
    if not estado or estado.get("principal_id") != principal_id:      # arranque o cambio de ubicación: se empieza de cero
        estado = estado_inicial(principal_id)
    e = {**estado, "fechas_nuevas": list(estado.get("fechas_nuevas") or [])}
    lecturas_vuelta = max(1, int(lecturas_vuelta))

    if not e["en_respaldo"]:
        if principal_activa:
            return e, PRINCIPAL
        e.update({"en_respaldo": True, "desde_ts": ahora, "fecha_al_caer": fecha_principal, "fechas_nuevas": [], "usando_id": None})
        return e, CAMBIO

    if not principal_activa:
        e["fechas_nuevas"] = []                 # volvió a caerse: se empieza a contar de nuevo
        return e, RESPALDO
    if fecha_principal and fecha_principal != e.get("fecha_al_caer") and fecha_principal not in e["fechas_nuevas"]:
        e["fechas_nuevas"].append(fecha_principal)
    if len(e["fechas_nuevas"]) >= lecturas_vuelta:
        return estado_inicial(principal_id), VUELTA
    return e, RESPALDO


def candidatos(cercanas: list, max_km: float, preferida: str | None = None, maximo: int = 3) -> list:
    """Estaciones cercanas a la principal dentro de `max_km`, de la más cercana a la más lejana. Si `preferida` (la que
    ya se está usando) está en la lista, va la primera. Devuelve como mucho `maximo`."""
    if not max_km or max_km <= 0:
        return []
    lista = [c for c in (cercanas or []) if isinstance(c.get("distancia_km"), (int, float)) and c["distancia_km"] <= max_km]
    lista.sort(key=lambda c: c["distancia_km"])
    if preferida:
        lista.sort(key=lambda c: c.get("id") != preferida)        # orden estable: la preferida primero, el resto igual
    return lista[:max(0, int(maximo))]


def mensaje(principal_nombre: str, respaldo: dict, motivo: str = "") -> str:
    """Texto del aviso que se muestra mientras se usa el respaldo."""
    dist = respaldo.get("distancia_km")
    donde = f" (a {dist:.1f} km)" if isinstance(dist, (int, float)) else ""
    porque = f": {motivo}" if motivo else ""
    return (f"Usando la estación de respaldo {respaldo.get('nombre') or respaldo.get('id')}{donde} porque "
            f"{principal_nombre or 'la estación principal'} no envía datos{porque}. Se volverá a ella en cuanto publique de nuevo.")
