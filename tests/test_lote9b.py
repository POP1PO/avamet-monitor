"""Mini-lote 9b: media simple del viento (radar) y ASCOM NotConnected. pytest -v  o  python tests/test_lote9b.py"""
import asyncio
import sys
import types
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from backend import config, movimiento   # noqa: E402

LEJANO = [{"icao": "LEVC", "distancia_km": 80.0}]


# ---------- 1. Media simple ----------
def test_central_en_calma_no_anula_una_tormenta_lejana():
    # Central 0 km/h ONO (con dirección) + aeropuerto a 80 km con 20 kt (37 km/h) del SO (225°)
    r = movimiento.estimar_movimiento_nubes({"Viento": "0 km/h ONO"}, {"LEVC": {"viento_nudos": 20.0, "viento_dir": 225.0}}, LEJANO)
    assert r["direccion_destino"] == "NE" and r["grados_destino"] == 45
    assert round(r["velocidad_kmh"], 1) == 18.5 and r["velocidad_kmh"] > 5      # media (0 + 37,04) / 2: radar activo
    # Central en calma SIN dirección: no aporta punto; manda el aeropuerto entero
    r = movimiento.estimar_movimiento_nubes({"Viento": "0 km/h"}, {"LEVC": {"viento_nudos": 20.0, "viento_dir": 225.0}}, LEJANO)
    assert r["grados_destino"] == 45 and round(r["velocidad_kmh"], 1) == 37.0


def test_ya_no_domina_la_central():
    c = {"Viento": "12 km/h ONO"}                                       # hacia ESE (112,5°)
    m = {"LECH": {"viento_nudos": 10, "viento_dir": 270.0}, "LEVC": {"viento_nudos": 5, "viento_dir": 250.0}}
    ae = [{"icao": "LECH", "distancia_km": 22.0}, {"icao": "LEVC", "distancia_km": 60.0}]
    r = movimiento.estimar_movimiento_nubes(c, m, ae)
    assert r["grados_destino"] == 92 and r["velocidad_kmh"] == 12.8      # los tres puntos cuentan igual


def test_dos_puntos_opuestos_se_compensan_y_sin_puntos_no_estimable():
    c = {"Viento": "10 km/h O"}                                         # hacia el E
    m = {"LEVC": {"viento_nudos": 10 / 1.852, "viento_dir": 90.0}}      # hacia el O, misma velocidad
    r = movimiento.estimar_movimiento_nubes(c, m, LEJANO)
    assert r["velocidad_kmh"] < 0.1 and r["categoria"] == "Lento"        # radar apagado (< 5 km/h)
    assert movimiento.estimar_movimiento_nubes({}, {}, [])["categoria"] == "No estimable"


# ---------- 2. ASCOM: NotConnected ----------
def _main():
    if "backend.main" in sys.modules: return sys.modules["backend.main"]
    class HTTPException(Exception):
        def __init__(self, status_code, detail=None, headers=None): self.status_code = status_code
    fa = types.ModuleType("fastapi")
    class _App:
        def __init__(self, *a, **k): pass
        def _d(self, *a, **k): return lambda f: f
        get = put = post = mount = _d
        def add_middleware(self, *a, **k): pass
    fa.FastAPI = _App; fa.Request = object; fa.Body = lambda *a, **k: None; fa.HTTPException = HTTPException; fa.BackgroundTasks = object
    fr = types.ModuleType("fastapi.responses"); fr.FileResponse = fr.StreamingResponse = fr.Response = object
    sys.modules.setdefault("fastapi", fa); sys.modules.setdefault("fastapi.responses", fr)
    import backend.main as m
    return m


class Req:
    def __init__(self, cuerpo=b"", **q):
        self._c, self.query_params = cuerpo, q
    async def body(self): return self._c


def _run(co): return asyncio.run(co)


def test_issafe_sin_conectar_devuelve_error_ascom_1031():
    m = _main(); m._ALPACA_CONECTADO = False; m._NINA_ULTIMO_PING = 0.0; config.ALPACA_ESTRICTO = True
    r = _run(m.alpaca_issafe(Req(ClientTransactionID="7")))
    assert r["ErrorNumber"] == 1031 == 0x407 and "no está conectado" in r["ErrorMessage"]
    assert "Value" not in r and r["ClientTransactionID"] == 7           # NUNCA un True/False falso
    assert m._NINA_ULTIMO_PING == 0.0                                    # el indicador N.I.N.A. no se enciende
    assert _run(m.alpaca_connected(Req()))["Value"] is False


def test_conectar_issafe_desconectar():
    m = _main(); m._ALPACA_CONECTADO = False; config.ALPACA_ESTRICTO = True
    m.ESTADO = {"estado": "INICIANDO"}                                   # no seguro, pero responde (sin error)
    ok = _run(m.alpaca_connect(Req(b"Connected=True&ClientID=1&ClientTransactionID=5")))
    assert ok["ErrorNumber"] == 0
    r = _run(m.alpaca_issafe(Req()))
    assert r["ErrorNumber"] == 0 and r["Value"] is False and m._NINA_ULTIMO_PING > 0
    assert _run(m.alpaca_connected(Req()))["Value"] is True
    _run(m.alpaca_connect(Req(b"connected=false")))                      # clave en minúsculas y valor False
    assert _run(m.alpaca_issafe(Req()))["ErrorNumber"] == 1031 and m._NINA_ULTIMO_PING == 0.0


def test_connected_invalido_es_400_y_no_cambia_el_estado():
    m = _main(); m._ALPACA_CONECTADO = False; config.ALPACA_ESTRICTO = True
    m.Response = lambda contenido, status_code=200, media_type=None: ("RESP", status_code, contenido)
    for malo in (b"", b"Connected=quizas", b"Otro=True"):
        r = _run(m.alpaca_connect(Req(malo)))
        assert r[0] == "RESP" and r[1] == 400
    assert m._ALPACA_CONECTADO is False
    assert _run(m.alpaca_connect(Req(b"", Connected="true")))["ErrorNumber"] == 0    # también por query string
    assert m._ALPACA_CONECTADO is True; m._ALPACA_CONECTADO = False


def test_modo_no_estricto_mantiene_el_comportamiento_antiguo():
    m = _main(); m._ALPACA_CONECTADO = False; config.ALPACA_ESTRICTO = False
    try:
        r = _run(m.alpaca_issafe(Req()))
        assert r["ErrorNumber"] == 0 and "Value" in r
        assert _run(m.alpaca_connected(Req()))["Value"] is True
    finally:
        config.ALPACA_ESTRICTO = True


def test_resto_de_endpoints_no_exigen_conexion():
    m = _main(); m._ALPACA_CONECTADO = False; config.ALPACA_ESTRICTO = True
    for f in (m.alpaca_name, m.alpaca_description, m.alpaca_driverinfo, m.alpaca_driverversion, m.alpaca_interfaceversion, m.alpaca_supportedactions):
        assert _run(f(Req()))["ErrorNumber"] == 0


if __name__ == "__main__":
    fallos = 0
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            try: f(); print("OK  ", n)
            except Exception as e: fallos += 1; print("FALLA", n, repr(e))
    sys.exit(1 if fallos else 0)
