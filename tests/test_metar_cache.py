"""Caché de METAR y latido del flujo en tiempo real. Se ejecuta con:  python tests/test_metar_cache.py"""
import asyncio, os, sys, tempfile, types
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.environ["AVAMET_DATA_DIR"] = tempfile.mkdtemp()
from backend import aeropuertos


class _Resp:
    def __init__(self, datos, ok=True): self._d, self._ok = datos, ok
    def raise_for_status(self):
        if not self._ok: raise RuntimeError("HTTP 503")
    def json(self): return self._d


def _metar(icao, temp=15.0):
    return {"icaoId": icao, "temp": temp, "dewp": 10, "wspd": 12, "wdir": 270, "visib": "10+", "clouds": [], "wxString": "", "reportTime": "2026-11-02 00:20:00"}


def _con_api(fn):
    orig = aeropuertos.requests.get
    aeropuertos.requests.get = fn
    return orig


def _reset():
    aeropuertos._CACHE_METAR.clear()


def test_fallo_de_la_api_devuelve_ultimo_dato_bueno():
    _reset()
    orig = _con_api(lambda *a, **k: _Resp([_metar("LEVC", 15.0), _metar("LEZL", 17.0)]))
    try:
        ok = aeropuertos.obtener_metars(["LEVC", "LEZL"], ahora=1000.0)
        assert ok["LEVC"]["temperatura"] == 15.0 and not ok["LEVC"].get("desactualizado")
        def falla(*a, **k): raise aeropuertos.requests.ConnectionError("sin red")
        aeropuertos.requests.get = falla
        r = aeropuertos.obtener_metars(["LEVC", "LEZL"], ahora=1000.0 + 600)
        assert r["LEVC"]["temperatura"] == 15.0 and r["LEVC"]["desactualizado"] is True and r["LEVC"]["cache_edad_s"] == 600
        aeropuertos.requests.get = lambda *a, **k: _Resp(None, ok=False)           # HTTP 503
        assert aeropuertos.obtener_metars(["LEZL"], ahora=1000.0 + 900)["LEZL"]["desactualizado"] is True
        aeropuertos.requests.get = lambda *a, **k: _Resp({"error": "limite"})        # JSON que no es una lista
        assert aeropuertos.obtener_metars(["LEVC"], ahora=1000.0 + 60)["LEVC"]["desactualizado"] is True
    finally:
        aeropuertos.requests.get = orig


def test_aeropuerto_ausente_en_la_respuesta_se_rellena_de_cache():
    _reset()
    orig = _con_api(lambda *a, **k: _Resp([_metar("LEVC"), _metar("LEZL")]))
    try:
        aeropuertos.obtener_metars(["LEVC", "LEZL"], ahora=0.0)
        aeropuertos.requests.get = lambda *a, **k: _Resp([_metar("LEVC", 16.0)])     # LEZL no viene esta vez
        r = aeropuertos.obtener_metars(["LEVC", "LEZL"], ahora=300.0)
        assert r["LEVC"]["temperatura"] == 16.0 and not r["LEVC"].get("desactualizado")
        assert r["LEZL"]["desactualizado"] is True and r["LEZL"]["cache_edad_s"] == 300
    finally:
        aeropuertos.requests.get = orig


def test_caducidad_y_nunca_inventa():
    _reset()
    orig = _con_api(lambda *a, **k: _Resp([_metar("LEVC")]))
    try:
        aeropuertos.obtener_metars(["LEVC"], ahora=0.0)
        def falla(*a, **k): raise RuntimeError("caído")
        aeropuertos.requests.get = falla
        assert "LEVC" in aeropuertos.obtener_metars(["LEVC"], ahora=aeropuertos.CACHE_MAX_S - 1)
        assert aeropuertos.obtener_metars(["LEVC"], ahora=aeropuertos.CACHE_MAX_S + 1) == {}     # demasiado viejo: se descarta
        assert aeropuertos.obtener_metars(["LEXX"], ahora=5.0) == {}                              # nunca descargado: vacío, sin inventar
        assert aeropuertos.obtener_metars([]) == {}
    finally:
        aeropuertos.requests.get = orig


def test_la_cache_no_altera_el_original_ni_se_contamina():
    _reset()
    orig = _con_api(lambda *a, **k: _Resp([_metar("LEVC")]))
    try:
        aeropuertos.obtener_metars(["LEVC"], ahora=0.0)
        aeropuertos.requests.get = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
        r = aeropuertos.obtener_metars(["LEVC"], ahora=100.0); r["LEVC"]["temperatura"] = 99     # el que lo use no toca la caché
        assert aeropuertos.obtener_metars(["LEVC"], ahora=200.0)["LEVC"]["temperatura"] == 15.0
        assert "desactualizado" not in aeropuertos._CACHE_METAR["LEVC"][1]
    finally:
        aeropuertos.requests.get = orig


def test_para_calcular_solo_cache_reciente():
    m = {"A": {"temperatura": 1}, "B": {"temperatura": 2, "desactualizado": True, "cache_edad_s": 600},
         "C": {"temperatura": 3, "desactualizado": True, "cache_edad_s": aeropuertos.CACHE_CALCULO_S + 1}, "D": {}}
    assert set(aeropuertos.utiles_para_calculo(m)) == {"A", "B"}


def test_latido_sse_y_watchdog_en_el_codigo():
    from backend import config
    assert 1 <= config.SSE_LATIDO_S < 60
    main_py = (RAIZ / "backend" / "main.py").read_text(encoding="utf-8")
    assert 'event: ping' in main_py and "asyncio.wait_for(q.get(), timeout=config.SSE_LATIDO_S)" in main_py
    h = (RAIZ / "frontend" / "index.html").read_text(encoding="utf-8")
    for x in ("SSE_SIN_EVENTOS_MS = 60000", "addEventListener('ping'", "mensajeVacio(", "reconectar('60 s sin eventos')", "completarAeropuertos("):
        assert x in h, x


if __name__ == "__main__":
    n = 0
    for nombre, f in list(globals().items()):
        if nombre.startswith("test_") and callable(f):
            f(); n += 1; print("OK", nombre)
    print(f"{n} pruebas OK")
