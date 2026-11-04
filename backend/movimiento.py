"""Estimación del movimiento de las nubes a partir del viento (estación central + METAR de aeropuertos).

Media SIMPLE de los vectores de viento (cada punto vale lo mismo). Antes pesaban 1/distancia²: con la central a
0,1 km su peso era ~48 000 veces el de un aeropuerto a 22 km, y una central en calma anulaba cualquier tormenta lejana.
(1/distancia tampoco servía: seguía siendo ~800 a 1.) Desventaja asumida: un aeropuerto lejano cuenta como la central.

Módulo puro (sin red ni FastAPI) para poder probarlo. Regla de oro: un dato de dirección que no se entiende
(VRB = viento variable, vacío, "Calma", una abreviatura desconocida) NO es 0° (Norte): se descarta ese punto.
"""
from __future__ import annotations

import re
from math import radians, sin, cos, atan2, sqrt, degrees

ROSA = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"]

# Abreviaturas en español (las que usa AVAMET). Se aceptan también las inglesas de uso común.
_DIR_A_GRADOS = {**{n: i * 22.5 for i, n in enumerate(ROSA)},
                 "W": 270.0, "WNW": 292.5, "NW": 315.0, "NNW": 337.5, "WSW": 247.5, "SW": 225.0, "SSW": 202.5}
_RE_VIENTO = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*km/h\s*([A-Za-zÑñ]+)?\s*$")
_DESCONOCIDAS: set[str] = set()


def direccion_a_grados(texto) -> float | None:
    """'ONO' -> 292.5 ; 'VRB', '', None, 'xyz' -> None (nunca 0.0)."""
    if texto is None:
        return None
    clave = str(texto).strip().upper()
    if clave in _DIR_A_GRADOS:
        return _DIR_A_GRADOS[clave]
    if clave and clave not in _DESCONOCIDAS:
        _DESCONOCIDAS.add(clave)
        print(f"Movimiento de nubes: dirección de viento no reconocida ({clave!r}); se ignora ese punto")
    return None


def _punto_central(central: dict) -> dict | None:
    texto = central.get("Viento", "")
    if not isinstance(texto, str) or "km/h" not in texto:
        return None
    m = _RE_VIENTO.match(texto)
    if not m:
        print(f"Movimiento de nubes: no se entiende el viento de la estación ({texto!r}); se ignora ese punto")
        return None
    grados = direccion_a_grados(m.group(2))
    if grados is None:
        return None
    return {"dist": 0.1, "vel": float(m.group(1).replace(",", ".")), "dir": grados}


def estimar_movimiento_nubes(central: dict, metars: dict, aeropuertos_cerca: list) -> dict:
    puntos = []
    p = _punto_central(central)
    if p:
        puntos.append(p)
    for aero in aeropuertos_cerca:
        m = metars.get(aero["icao"])
        # viento_dir None = variable (VRB) o ausente: sin dirección no se puede promediar el vector
        if m and m.get("viento_nudos") is not None and m.get("viento_dir") is not None:
            puntos.append({"dist": max(aero["distancia_km"], 0.1), "vel": m["viento_nudos"] * 1.852, "dir": m["viento_dir"]})

    if not puntos:
        return {"direccion_destino": "Desconocida", "grados_destino": 0, "velocidad_kmh": 0, "categoria": "No estimable"}

    sum_u, sum_v, sum_peso = 0.0, 0.0, 0.0
    for p in puntos:
        peso = 1.0                                   # media simple: todos los puntos pesan igual
        rad = radians(p["dir"])
        sum_u += (-p["vel"] * sin(rad)) * peso
        sum_v += (-p["vel"] * cos(rad)) * peso
        sum_peso += peso

    u_media, v_media = sum_u / sum_peso, sum_v / sum_peso
    dir_origen = (degrees(atan2(-u_media, -v_media)) + 360) % 360
    vel_media = sqrt(u_media ** 2 + v_media ** 2)
    dir_destino = (dir_origen + 180) % 360
    idx_destino = round(dir_destino / 22.5) % 16

    if vel_media < 10: cat = "Lento"
    elif vel_media < 30: cat = "Moderado"
    else: cat = "Rápido"

    return {"direccion_destino": ROSA[idx_destino], "grados_destino": round(dir_destino),
            "velocidad_kmh": round(vel_media, 1), "categoria": cat}
