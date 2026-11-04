"""Seeing de 7Timer. Se ejecuta con:  python tests/test_seeing.py"""
import os, sys, tempfile, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.environ["AVAMET_DATA_DIR"] = tempfile.mkdtemp()
from backend import seeing


def _serie(init_utc, n=24):
    return {"product": "astro", "init": init_utc.strftime("%Y%m%d%H"),
            "dataseries": [{"timepoint": 3 * i, "cloudcover": 1 + i % 4, "seeing": 2 + i % 3, "transparency": 3, "lifted_index": 2} for i in range(1, n + 1)]}


def test_interpretar_solo_horas_de_noche():
    init = datetime(2026, 11, 2, 6, tzinfo=timezone.utc)
    r = seeing.interpretar(_serie(init), init.timestamp() + 3 * 3600)
    assert r["seeing"]["media"] >= 2 and 1 <= r["seeing"]["peor"] <= 8 and 1 <= r["horas"] <= 9
    assert r["transparencia"]["media"] == 3.0


def test_sin_dato_se_ignora_y_sin_noche_falla():
    init = datetime(2026, 11, 2, 6, tzinfo=timezone.utc)
    d = _serie(init)
    for p in d["dataseries"]: p["seeing"] = -9999
    try: seeing.interpretar(d, init.timestamp()); assert False
    except ValueError: pass


def test_cache_una_peticion_y_reintento_tras_fallo():
    seeing._CACHE.clear()
    init = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    llamadas = []
    orig = seeing._descargar
    try:
        def ok(lat, lon): llamadas.append(1); return _serie(init - timedelta(hours=2))
        seeing._descargar = ok
        t = time.time()
        a = seeing.obtener(40.25, -0.06, t); b = seeing.obtener(40.25, -0.06, t + 600)
        assert a["error"] is None and a["datos"] and b["datos"] == a["datos"] and len(llamadas) == 1
        def falla(lat, lon): llamadas.append(2); raise OSError("sin red")
        seeing._descargar = falla
        c = seeing.obtener(40.25, -0.06, t + 3700)          # caducó la caché -> falla, pero conserva el último dato bueno
        assert c["error"] and c["datos"] == a["datos"]
        n = len(llamadas); seeing.obtener(40.25, -0.06, t + 3800); assert len(llamadas) == n    # no reintenta antes de 10 min
        seeing.obtener(40.25, -0.06, t + 3700 + 700); assert len(llamadas) == n + 1
    finally:
        seeing._descargar = orig


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"): v(); print("ok", k)
