"""Escritor del fichero Boltwood Cloud Sensor II ("Single Line Data File", formato NUEVO).

Fuente del formato: Cloud Sensor II User's Manual (Diffraction Limited / Boltwood Systems),
sección 17.1.1 "New Format". Una sola línea de 104 columnas, columnas fijas:

  Campo   Columnas   Contenido
  Date    1-10       fecha local yyyy-mm-dd
  Time    12-22      hora local hh:mm:ss.ss
  T       24         unidades de temperatura (C/F)
  V       26         unidades de viento (K=km/h, M=mph, m=m/s)
  SkyT    28-33      temperatura cielo-ambiente (999. caliente saturado, -999. frío saturado, -998. mojado)
  AmbT    35-40      temperatura ambiente
  SenT    41-47      temperatura de la caja del sensor
  Wind    49-54      velocidad del viento (negativos = códigos de fallo, ver manual)
  Hum     56-58      humedad relativa %
  DewPt   60-65      punto de rocío
  Hea     67-69      calentador %
  R       71         lluvia: 0 seco, 1 lluvia último minuto, 2 lluvia ahora
  W       73         mojado: 0 seco, 1 mojado último minuto, 2 mojado ahora
  Since   75-79      segundos desde el último dato válido
  Now()   81-92      fecha/hora (días VB6, desde 1899-12-30) en que se escribió el fichero
  c       94         nubes: 0 desconocido, 1 despejado, 2 nublado, 3 muy nublado
  w       96         viento: 0 desconocido, 1 calma, 2 ventoso, 3 muy ventoso
  r       98         lluvia: 0 desconocido, 1 seco, 2 mojado, 3 lluvia
  d       100        luz: 0 desconocido, 1 oscuro, 2 luz, 3 mucha luz
  C       102        cierre de techo solicitado (0/1)
  A       104        alerta (0/1)

MAPEO AVAMET -> Boltwood (lo que NO mide AVAMET se documenta aquí):
  - T='C', V='K' siempre.
  - AmbT  = "Temperatura" de la central. SenT = AmbT (no hay sensor de caja).
  - SkyT  = NO MEDIDO. Valor sintético coherente con config.BOLTWOOD_COND_NUBES y los umbrales
            por defecto del manual (-25 / -10 °C): 0->0.0, 1->-30.0, 2->-15.0, 3->-5.0.
  - Wind  = "Viento" (km/h). Hum = "Humedad". DewPt = fórmula de Magnus (17.62 / 243.12).
  - Hea   = 0 (no hay calentador).
  - Lluvia (incluye la retención de 30 min de la histéresis):
      central:  detectada en este ciclo -> R=2, W=2, r=3 ; solo retenida -> R=1, W=1, r=3
      radio:    (detectada o retenida)  -> R=0, W=1, r=2
      ninguna:  R=0, W=0, r=1
  - w     : alertas.cond_viento(): 3 muy ventoso (viento > UMBRAL_VIENTO_PELIGRO_KMH o racha >
            UMBRAL_RACHA_PELIGRO_KMH, y entonces el nivel es PELIGRO), 2 ventoso (VIGILANCIA), 1 calma.
            También w=3 mientras dure la retención de viento (config.RETENCION_VIENTO_S, 15 min
            desde el último viento/racha en PELIGRO), aunque la lectura actual ya sea más baja.
  - c, d  : NO MEDIDOS -> config.BOLTWOOD_COND_NUBES / BOLTWOOD_COND_LUZ (por defecto 0).
  - Since = segundos desde "Fecha lectura" de AVAMET (no desde ESTADO["timestamp"]); 99999 si no hay.
  - C (cierre) = 0 si alertas.evaluar_seguridad dice seguro, 1 si no.
  - A (alerta) = 1 si no es seguro o el nivel es VIGILANCIA; 0 si SEGURO.
  - SIN DATOS / DATO CADUCADO / CICLO CADUCADO: c=w=r=d=0, SkyT=AmbT=SenT=0.0, Wind=-4.0
    (código "sensor con fallo"), Hum=0, DewPt=0.0, R=W=0, C=1, A=1. En DATO_CADUCADO se conserva
    el Since real para que el consumidor vea la edad del dato.
"""
from __future__ import annotations

import math
import os
import time
from datetime import datetime
from pathlib import Path

from . import alertas, avamet, config

_SKY_SINTETICO = {0: 0.0, 1: -30.0, 2: -15.0, 3: -5.0}
_EPOCH_VB6 = datetime(1899, 12, 30)


def _punto_rocio(t: float, hr: float) -> float:
    """Fórmula de Magnus (constantes 17.62 / 243.12)."""
    hr = min(max(hr, 1.0), 100.0)
    g = math.log(hr / 100.0) + 17.62 * t / (243.12 + t)
    return 243.12 * g / (17.62 - g)


def _lim(v: float, lo: float, hi: float) -> float:
    v = min(max(v, lo), hi)
    return round(v, 1) + 0.0          # +0.0 evita "-0.0"


def construir_linea(estado: dict, central: dict | None = None, ahora: float | None = None) -> str:
    """Devuelve la línea Boltwood (104 caracteres, sin salto de línea)."""
    ahora = time.time() if ahora is None else ahora
    datos = central if central is not None else ((estado.get("central") or {}).get("datos") or {})
    seguro, motivo = alertas.evaluar_seguridad(estado, datos, ahora)
    sin_datos = motivo in ("SIN_DATOS", "DATO_CADUCADO", "CICLO_CADUCADO")

    edad = avamet.edad_lectura_s(datos, ahora)
    since = 99999 if edad is None else min(int(edad), 99999)

    if sin_datos:
        sky = amb = sen = dew = 0.0
        wind, hum, hea = -4.0, 0, 0
        R = W = 0
        c = w = r = d = 0
    else:
        nivel = estado.get("estado")
        flags = estado.get("flags") or {}
        amb = alertas._num(datos.get("Temperatura"))
        sen = amb
        hum = int(round(min(max(alertas._num(datos.get("Humedad")), 0.0), 100.0)))
        dew = _punto_rocio(amb, float(hum))
        viento = alertas._num(datos.get("Viento"))
        racha = alertas._num(datos.get("Viento máximo"))
        wind, hea = viento, 0

        c = config.BOLTWOOD_COND_NUBES
        d = config.BOLTWOOD_COND_LUZ
        sky = _SKY_SINTETICO.get(c, 0.0)
        w = 3 if flags.get("viento_peligro") else alertas.cond_viento(viento, racha)

        if flags.get("lluvia_central"):
            if flags.get("lluvia_ahora", True):
                R, W, r = 2, 2, 3
            else:                              # solo retenida por la histéresis
                R, W, r = 1, 1, 3
        elif flags.get("lluvia_radio"):
            R, W, r = 0, 1, 2
        else:
            R, W, r = 0, 0, 1
        if nivel == "PELIGRO" and r == 1 and w < 3:   # red de seguridad si faltan los flags de lluvia
            r, W = 2, 1

    nivel = estado.get("estado")
    C = 0 if seguro else 1
    A = 1 if (not seguro or nivel == "VIGILANCIA") else 0

    tz = avamet.zona_horaria()
    dt = datetime.fromtimestamp(ahora, tz)
    fecha = dt.strftime("%Y-%m-%d")
    hora = dt.strftime("%H:%M:%S") + f".{dt.microsecond // 10000:02d}"
    now_vb6 = (dt.replace(tzinfo=None) - _EPOCH_VB6).total_seconds() / 86400.0

    sky, amb, sen = _lim(sky, -999.9, 999.9), _lim(amb, -99.9, 999.9), _lim(sen, -999.9, 999.9)
    wind, dew = _lim(wind, -99.9, 999.9), _lim(dew, -99.9, 999.9)

    return (
        f"{fecha} {hora} C K {sky:6.1f} {amb:6.1f}{sen:7.1f} {wind:6.1f} {hum:3d} {dew:6.1f} {hea:3d} "
        f"{R} {W} {since:05d} {now_vb6:012.5f} {c} {w} {r} {d} {C} {A}"
    )


def escribir(estado: dict, central: dict | None = None, ruta=None, ahora: float | None = None) -> Path:
    """Escribe el fichero de forma atómica (temporal + os.replace). Devuelve la ruta."""
    ruta = Path(ruta or config.BOLTWOOD_PATH)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    contenido = (construir_linea(estado, central, ahora) + "\r\n").encode("ascii")   # CRLF como Clarity II
    tmp = ruta.with_name(ruta.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(contenido)
        f.flush()
        os.fsync(f.fileno())
    for intento in range(5):             # en Windows el lector puede tener el fichero abierto un instante
        try:
            os.replace(tmp, ruta)
            break
        except PermissionError:
            if intento == 4:
                raise
            time.sleep(0.1)
    return ruta
