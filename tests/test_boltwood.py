"""Tests del fichero Boltwood (formato nuevo, manual Cloud Sensor II §17.1.1).

Ejecutar:   pytest -v            (desde la raíz del proyecto)
        o:  python tests/test_boltwood.py   (runner mínimo incluido, sin pytest)

Requiere zona horaria Europe/Madrid (en Windows: pip install tzdata).
"""
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import alertas, avamet, boltwood, config   # noqa: E402

# Configuración fija para que los tests no dependan del entorno
config.ZONA_HORARIA = "Europe/Madrid"
config.EDAD_MAX_DATOS_S = 600
config.EDAD_MAX_CICLO_S = 300
config.UMBRAL_VIENTO_KMH = 40
config.UMBRAL_VIENTO_MAX_KMH = 45
config.RETENCION_VIENTO_S = 900
config.UMBRAL_VIENTO_PELIGRO_KMH = 45
config.UMBRAL_RACHA_PELIGRO_KMH = 55
config.RETENCION_LLUVIA_S = 1800
config.BOLTWOOD_COND_NUBES = 0
config.BOLTWOOD_COND_LUZ = 0
avamet.zona_horaria.cache_clear()

# "Ahora" simulado: 2026-11-01 05:30:00 hora de Madrid
AHORA = datetime(2026, 11, 1, 5, 30, tzinfo=ZoneInfo("Europe/Madrid")).timestamp()
NOW_VB6 = "046327.22917"     # 46327 días desde 1899-12-30 hasta 2026-11-01, + 5.5/24


def _datos(temp="15.1 °C", hum="90 %", viento="10 km/h ONO", racha="18 km/h", fecha="01-11-2026 05:25"):
    return {"Fecha lectura": fecha, "Temperatura": temp, "Temp mín": temp, "Temp máx": "17.7 °C",
            "Humedad": hum, "Presión": "1021 hPa", "Viento": viento, "Viento máximo": racha,
            "Lluvia hoy": "0.0 mm", "Lluvia mensual": "0.0 mm", "Lluvia anual": "366.6 mm"}


def _estado(nivel, datos, flags=None):
    return {"estado": nivel,
            "timestamp": datetime.fromtimestamp(AHORA).isoformat(timespec="seconds"),
            "central": {"datos": datos}, "flags": flags or {}}


def _esperada(sky, amb, sen, wind, hum, dew, hea, R, W, since, c, w, r, d, C, A):
    """Línea de referencia armada a mano con las columnas del manual (cada argumento ya viene
    con su ancho: SkyT 6, AmbT 6, SenT 7, Wind 6, Hum 3, DewPt 6, Hea 3, Since 5)."""
    return "".join([
        "2026-11-01", " ",            # Date   cols 1-10
        "05:30:00.00", " ",           # Time   cols 12-22
        "C", " ", "K", " ",           # T col 24, V col 26
        sky, " ",                     # SkyT   cols 28-33
        amb, sen, " ",                # AmbT 35-40, SenT 41-47
        wind, " ",                    # Wind   cols 49-54
        hum, " ",                     # Hum    cols 56-58
        dew, " ",                     # DewPt  cols 60-65
        hea, " ",                     # Hea    cols 67-69
        R, " ", W, " ",               # R col 71, W col 73
        since, " ",                   # Since  cols 75-79
        NOW_VB6, " ",                 # Now()  cols 81-92
        c, " ", w, " ", r, " ", d, " ", C, " ", A,   # c 94, w 96, r 98, d 100, C 102, A 104
    ])


# ---------------------------------------------------------------- los 3 estados pedidos
def test_seguro():
    linea = boltwood.construir_linea(_estado("SEGURO", _datos()), ahora=AHORA)
    esperada = _esperada("   0.0", "  15.1", "   15.1", "  10.0", " 90", "  13.5", "  0",
                         "0", "0", "00300", "0", "1", "1", "0", "0", "0")
    assert linea == esperada
    assert len(linea) == 104
    assert alertas.evaluar_seguridad(_estado("SEGURO", _datos()), ahora=AHORA) == (True, "OK")


def test_vigilancia():
    est = _estado("VIGILANCIA", _datos(temp="12.3 °C", hum="92 %", viento="42 km/h O", racha="50 km/h"))
    linea = boltwood.construir_linea(est, ahora=AHORA)
    esperada = _esperada("   0.0", "  12.3", "   12.3", "  42.0", " 92", "  11.0", "  0",
                         "0", "0", "00300", "0", "2", "1", "0", "0", "1")     # ventoso (w=2), C=0, A=1
    assert linea == esperada
    assert alertas.evaluar_seguridad(est, ahora=AHORA) == (True, "OK")        # VIGILANCIA sigue siendo seguro


def test_sin_datos():
    est = _estado("SIN_DATOS", {"Error": "timeout"})
    linea = boltwood.construir_linea(est, central={"Error": "timeout"}, ahora=AHORA)
    esperada = _esperada("   0.0", "   0.0", "    0.0", "  -4.0", "  0", "   0.0", "  0",
                         "0", "0", "99999", "0", "0", "0", "0", "1", "1")      # todo desconocido, C=1, A=1
    assert linea == esperada
    assert alertas.evaluar_seguridad(est, {"Error": "timeout"}, AHORA) == (False, "SIN_DATOS")


# ---------------------------------------------------------------- casos extra críticos
def test_peligro_lluvia_central():
    est = _estado("PELIGRO", _datos(temp="14.0 °C", hum="95 %", viento="8 km/h N", racha="12 km/h"),
                  flags={"lluvia_central": True})
    linea = boltwood.construir_linea(est, ahora=AHORA)
    esperada = _esperada("   0.0", "  14.0", "   14.0", "   8.0", " 95", "  13.2", "  0",
                         "2", "2", "00300", "0", "1", "3", "0", "1", "1")      # R=2 W=2 r=3 C=1 A=1
    assert linea == esperada
    assert alertas.evaluar_seguridad(est, ahora=AHORA) == (False, "NIVEL")


def test_dato_caducado_conserva_los_segundos():
    est = _estado("SEGURO", _datos(fecha="01-11-2026 05:10"))                  # 20 min de antigüedad
    linea = boltwood.construir_linea(est, ahora=AHORA)
    esperada = _esperada("   0.0", "   0.0", "    0.0", "  -4.0", "  0", "   0.0", "  0",
                         "0", "0", "01200", "0", "0", "0", "0", "1", "1")
    assert linea == esperada
    assert alertas.evaluar_seguridad(est, ahora=AHORA) == (False, "DATO_CADUCADO")


def test_peligro_viento_fuerte_escribe_w3():
    est = _estado("PELIGRO", _datos(temp="14.0 °C", hum="60 %", viento="50 km/h O", racha="62 km/h"))
    linea = boltwood.construir_linea(est, ahora=AHORA)
    esperada = _esperada("   0.0", "  14.0", "   14.0", "  50.0", " 60", "   6.4", "  0",
                         "0", "0", "00300", "0", "3", "1", "0", "1", "1")     # w=3, SIN lluvia (r=1, W=0), C=1, A=1
    assert linea == esperada
    assert alertas.evaluar_seguridad(est, ahora=AHORA) == (False, "NIVEL")


def test_lluvia_retenida_mantiene_r3_pero_R1():
    est = _estado("PELIGRO", _datos(), flags={"lluvia_central": True, "lluvia_ahora": False, "lluvia_retenida": True})
    campos = boltwood.construir_linea(est, ahora=AHORA).split()
    assert campos[-6:] == ["0", "1", "3", "0", "1", "1"]          # c w r d C A  (r=3 sigue siendo lluvia)
    assert campos[-9:-8] == ["1"] and campos[-10] == "1"             # R=1, W=1


def test_viento_retenido_escribe_w3_con_viento_actual_bajo():
    est = _estado("PELIGRO", _datos(), flags={"viento_peligro": True, "viento_retenido": True})
    linea = boltwood.construir_linea(est, ahora=AHORA)
    esperada = _esperada("   0.0", "  15.1", "   15.1", "  10.0", " 90", "  13.5", "  0",
                         "0", "0", "00300", "0", "3", "1", "0", "1", "1")     # viento actual 10 km/h pero w=3, C=1
    assert linea == esperada


def test_presion_ausente_no_invalida_la_estacion():
    d = _datos(); del d["Presión"]
    est = _estado("SEGURO", d)
    assert alertas.evaluar_seguridad(est, ahora=AHORA) == (True, "OK")
    assert avamet.datos_validos(d) is True
    d2 = _datos(); del d2["Humedad"]
    assert avamet.datos_validos(d2) is False                                   # humedad sí es imprescindible


def test_edad_maxima_600s():
    ok = _estado("SEGURO", _datos(fecha="01-11-2026 05:21"))        # 9 min  -> vale
    caducado = _estado("SEGURO", _datos(fecha="01-11-2026 05:19"))  # 11 min -> caducado
    assert alertas.evaluar_seguridad(ok, ahora=AHORA) == (True, "OK")
    assert alertas.evaluar_seguridad(caducado, ahora=AHORA) == (False, "DATO_CADUCADO")


def test_ciclo_caducado():
    est = _estado("SEGURO", _datos())
    est["timestamp"] = datetime.fromtimestamp(AHORA - 400).isoformat(timespec="seconds")
    assert alertas.evaluar_seguridad(est, ahora=AHORA) == (False, "CICLO_CADUCADO")


def test_estados_sin_datos_nunca_son_seguros():
    for nivel in ("INICIANDO", "ERROR", "SIN_DATOS", "APAGADO"):
        assert alertas.evaluar_seguridad({"estado": nivel}, ahora=AHORA)[0] is False


# ---------------------------------------------------------------- columnas según el manual
def test_columnas_del_manual():
    linea = boltwood.construir_linea(_estado("SEGURO", _datos()), ahora=AHORA)
    col = lambda a, b: linea[a - 1:b]                       # columnas 1-based inclusivas
    assert col(1, 10) == "2026-11-01"
    assert col(12, 22) == "05:30:00.00"
    assert col(24, 24) == "C" and col(26, 26) == "K"
    assert col(35, 40).strip() == "15.1"                    # AmbT
    assert col(49, 54).strip() == "10.0"                    # Wind
    assert col(56, 58).strip() == "90"                      # Hum
    assert col(75, 79) == "00300"                           # Since = edad de "Fecha lectura" (5 min)
    assert col(81, 92) == NOW_VB6
    assert [col(i, i) for i in (94, 96, 98, 100, 102, 104)] == ["0", "1", "1", "0", "0", "0"]
    assert len(linea.split()) == 21                         # 21 campos separados por espacios


def test_edad_lectura_usa_la_zona_de_madrid():
    assert avamet.edad_lectura_s({"Fecha lectura": "01-11-2026 05:25"}, AHORA) == 300.0
    assert avamet.edad_lectura_s({}, AHORA) is None
    assert avamet.edad_lectura_s({"Fecha lectura": "basura"}, AHORA) is None
    assert avamet.edad_lectura_s({"Fecha lectura": "01-11-2026 07:00"}, AHORA) is None   # futuro: zona mal puesta


# ---------------------------------------------------------------- escritura atómica
def test_escribir_fichero_atomico():
    with tempfile.TemporaryDirectory() as d:
        ruta = Path(d) / "sub" / "boltwood.dat"
        res = boltwood.escribir(_estado("SEGURO", _datos()), ruta=ruta, ahora=AHORA)
        assert res == ruta
        esperada = boltwood.construir_linea(_estado("SEGURO", _datos()), ahora=AHORA)
        assert ruta.read_bytes() == esperada.encode("ascii") + b"\r\n"      # una línea, CRLF, ASCII
        assert not list(ruta.parent.glob("*.tmp"))                           # sin temporales
        boltwood.escribir({"estado": "APAGADO"}, {}, ruta=ruta, ahora=AHORA)  # sobrescribe
        assert ruta.read_bytes().decode().split()[-6:] == ["0", "0", "0", "0", "1", "1"]


def test_a_float_presion_con_miles():
    assert avamet._a_float("1.021") == 1021.0
    assert avamet._a_float("1021,3") == 1021.3
    assert avamet._a_float("998.4") == 998.4
    assert avamet._a_float("x") is None


if __name__ == "__main__":
    fallos = 0
    for nombre, fn in sorted(globals().items()):
        if nombre.startswith("test_") and callable(fn):
            try:
                fn(); print(f"OK    {nombre}")
            except Exception as e:                                          # noqa: BLE001
                fallos += 1; print(f"FALLO {nombre}: {type(e).__name__}: {e}")
    sys.exit(1 if fallos else 0)
