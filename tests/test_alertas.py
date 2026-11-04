"""Tests de viento PELIGRO, histéresis de lluvia y detección de incrementos.
Ejecutar: pytest -v   o   python tests/test_alertas.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import alertas, config   # noqa: E402

config.UMBRAL_VIENTO_KMH = 40
config.UMBRAL_VIENTO_MAX_KMH = 45
config.RETENCION_VIENTO_S = 900
config.UMBRAL_VIENTO_PELIGRO_KMH = 45
config.UMBRAL_RACHA_PELIGRO_KMH = 55
config.UMBRAL_HUMEDAD_PCT = 90
config.RETENCION_LLUVIA_S = 1800
VACIO = {"central": False, "estaciones": []}


def test_cond_viento_limites():
    assert alertas.cond_viento(39, 44) == 1
    assert alertas.cond_viento(39, 54) == 2         # racha 54 >= 45: ahora es VIGILANCIA
    assert alertas.cond_viento(40, 0) == 2          # >= 40 -> ventoso
    assert alertas.cond_viento(45, 0) == 2          # 45 NO es > 45
    assert alertas.cond_viento(45.1, 0) == 3        # > 45 -> muy ventoso
    assert alertas.cond_viento(10, 44.9) == 1       # racha < 45 -> calma
    assert alertas.cond_viento(10, 45) == 2         # racha >= 45 -> ventoso (VIGILANCIA)
    assert alertas.cond_viento(10, 55) == 2         # racha 55 NO es > 55
    assert alertas.cond_viento(10, 55.1) == 3       # racha > 55 -> muy ventoso


def test_evaluar_viento_peligro_y_vigilancia():
    n, al = alertas.evaluar({"Viento": "50 km/h O", "Viento máximo": "60 km/h", "Humedad": "50 %"}, [], False, [])
    assert n == "PELIGRO" and al[0]["nivel"] == "CRITICO"
    n, al = alertas.evaluar({"Viento": "42 km/h O", "Viento máximo": "50 km/h", "Humedad": "50 %"}, [], False, [])
    assert n == "VIGILANCIA"
    n, al = alertas.evaluar({"Viento": "10 km/h O", "Viento máximo": "56 km/h", "Humedad": "50 %"}, [], False, [])
    assert n == "PELIGRO"                           # racha > 55


def test_retencion_30_minutos_desde_el_ultimo_incremento():
    t0 = 1_000_000.0
    r = alertas.actualizar_retencion_lluvia(0.0, VACIO, t0, True, [])
    assert r["ahora"] and r["vigente"] and r["central"] and r["ts"] == t0
    ts, info = r["ts"], r["info"]
    # 1 min después, sin incremento: sigue retenida
    r = alertas.actualizar_retencion_lluvia(ts, info, t0 + 60, False, [])
    assert not r["ahora"] and r["vigente"] and r["central"] and r["hace_s"] == 60
    # 29 min 59 s: todavía
    r = alertas.actualizar_retencion_lluvia(ts, info, t0 + 1799, False, [])
    assert r["vigente"] and r["central"]
    # 30 min exactos: caduca y se olvida todo
    r = alertas.actualizar_retencion_lluvia(ts, info, t0 + 1800, False, [])
    assert not r["vigente"] and not r["central"] and r["ts"] == 0.0 and r["estaciones"] == []


def test_un_nuevo_incremento_reinicia_los_30_minutos():
    t0 = 2_000_000.0
    r = alertas.actualizar_retencion_lluvia(0.0, VACIO, t0, True, [])
    r = alertas.actualizar_retencion_lluvia(r["ts"], r["info"], t0 + 1000, True, [])      # llueve otra vez
    assert r["ts"] == t0 + 1000
    r = alertas.actualizar_retencion_lluvia(r["ts"], r["info"], t0 + 1000 + 1799, False, [])
    assert r["vigente"] and r["central"]                                                  # 30 min desde el ÚLTIMO


def test_retencion_acumula_estaciones_y_central():
    t0 = 3_000_000.0
    r = alertas.actualizar_retencion_lluvia(0.0, VACIO, t0, False, ["Onda"])
    assert r["estaciones"] == ["Onda"] and not r["central"]
    r = alertas.actualizar_retencion_lluvia(r["ts"], r["info"], t0 + 120, True, ["Borriana"])
    assert r["central"] and r["estaciones"] == ["Onda", "Borriana"]


def test_evaluar_marca_la_lluvia_retenida():
    n, al = alertas.evaluar({"Lluvia hoy": "3.5 mm", "Humedad": "50 %"}, [], True, [], lluvia_retenida_min=12.0)
    assert n == "PELIGRO" and "retenido" in al[0]["mensaje"] and "12 min" in al[0]["mensaje"]
    n, al = alertas.evaluar({"Lluvia hoy": "3.5 mm", "Humedad": "50 %"}, [], True, [])
    assert "retenido" not in al[0]["mensaje"]


def test_viento_vigilancia_entre_40_y_45():
    for v, esperado in ((39.9, "SEGURO"), (40, "VIGILANCIA"), (45, "VIGILANCIA"), (45.1, "PELIGRO")):
        n, _ = alertas.evaluar({"Viento": f"{v} km/h O", "Viento máximo": f"{v} km/h", "Humedad": "50 %"}, [], False, [])
        assert n == esperado, (v, n)
    n, _ = alertas.evaluar({"Viento": "10 km/h O", "Viento máximo": "45 km/h", "Humedad": "50 %"}, [], False, [])
    assert n == "VIGILANCIA"                        # racha 45: ya no hay hueco entre 45 y 55


def test_retencion_viento_15_minutos():
    t0 = 5_000_000.0
    r = alertas.actualizar_retencion_viento(0.0, t0, True)
    assert r["ahora"] and r["vigente"] and r["ts"] == t0
    r = alertas.actualizar_retencion_viento(r["ts"], t0 + 60, False)               # viento ya bajo: sigue retenido
    assert not r["ahora"] and r["vigente"] and r["hace_s"] == 60
    r = alertas.actualizar_retencion_viento(t0, t0 + 899, False)
    assert r["vigente"]
    r = alertas.actualizar_retencion_viento(t0, t0 + 900, False)                   # 15 min exactos: caduca
    assert not r["vigente"] and r["ts"] == 0.0 and r["hace_s"] is None


def test_nueva_racha_fuerte_reinicia_los_15_minutos():
    t0 = 6_000_000.0
    r = alertas.actualizar_retencion_viento(0.0, t0, True)
    r = alertas.actualizar_retencion_viento(r["ts"], t0 + 800, True)               # otra racha fuerte
    assert r["ts"] == t0 + 800
    r = alertas.actualizar_retencion_viento(r["ts"], t0 + 800 + 899, False)
    assert r["vigente"]


def test_evaluar_viento_retenido_es_peligro_aunque_ahora_haya_calma():
    calma = {"Viento": "10 km/h O", "Viento máximo": "15 km/h", "Humedad": "50 %"}
    n, al = alertas.evaluar(calma, [], False, [])
    assert n == "SEGURO"
    n, al = alertas.evaluar(calma, [], False, [], viento_retenido_min=7.0)
    assert n == "PELIGRO" and "retenido" in al[0]["mensaje"] and "7 min" in al[0]["mensaje"]
    fuerte = {"Viento": "50 km/h O", "Viento máximo": "60 km/h", "Humedad": "50 %"}
    n, al = alertas.evaluar(fuerte, [], False, [], viento_retenido_min=7.0)       # si hoy sigue fuerte, mensaje normal
    assert n == "PELIGRO" and "retenido" not in al[0]["mensaje"]


def test_viento_fuerte_ahora():
    assert alertas.viento_fuerte_ahora({"Viento": "46 km/h O", "Viento máximo": "46 km/h"}) is True
    assert alertas.viento_fuerte_ahora({"Viento": "10 km/h O", "Viento máximo": "56 km/h"}) is True
    assert alertas.viento_fuerte_ahora({"Viento": "45 km/h O", "Viento máximo": "55 km/h"}) is False
    assert alertas.viento_fuerte_ahora({}) is False


def test_hay_lluvia_nueva():
    assert alertas.hay_lluvia_nueva(None, 3.0) is False        # sin línea base: nunca
    assert alertas.hay_lluvia_nueva(3.0, 3.0) is False
    assert alertas.hay_lluvia_nueva(0.1, 0.2) is True          # 0.1 mm cuenta (round evita 0.0999...)
    assert alertas.hay_lluvia_nueva(0.2, 0.3) is True
    assert alertas.hay_lluvia_nueva(3.5, 0.0) is False         # a medianoche se reinicia el acumulado


if __name__ == "__main__":
    fallos = 0
    for nombre, fn in sorted(globals().items()):
        if nombre.startswith("test_") and callable(fn):
            try:
                fn(); print(f"OK    {nombre}")
            except Exception as e:                                          # noqa: BLE001
                fallos += 1; print(f"FALLO {nombre}: {type(e).__name__}: {e}")
    sys.exit(1 if fallos else 0)
