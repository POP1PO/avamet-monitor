"""AEMET OpenData: lógica pura y flujo de dos pasos con red simulada. Sin red y sin clave real."""
import os, sys, tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.environ["AVAMET_DATA_DIR"] = tempfile.mkdtemp()
from backend import aemet, entorno

CLAVE_FALSA = "clave.de.prueba-" + "x" * 30
REG = [
    {"idema": "8500A", "ubi": "VALENCIA", "lat": 39.48, "lon": -0.37, "fint": "2026-11-25T09:00:00", "prec": 0.0, "ta": 15.0, "vv": 2.0},
    {"idema": "8500A", "ubi": "VALENCIA", "lat": 39.48, "lon": -0.37, "fint": "2026-11-25T10:00:00", "prec": 1.2, "ta": 14.0, "hr": 90, "vv": 5.0, "dv": 200},
    {"idema": "9999Z", "ubi": "LEJOS", "lat": 28.0, "lon": -15.0, "fint": "2026-11-25T10:00:00", "prec": 0.0},
    {"idema": "SINPOS", "fint": "2026-11-25T10:00:00"},
    "basura",
]


def test_ultima_observacion_distancia_y_unidades():
    r = aemet.cercanas_de(REG, 39.5, -0.4, 60)
    assert [e["idema"] for e in r] == ["8500A"]
    e = r[0]
    assert e["fint_utc"].endswith("10:00:00") and e["lluvia_mm"] == 1.2 and e["temp_c"] == 14.0
    assert e["viento_kmh"] == 18.0            # 5 m/s * 3.6
    assert aemet.cercanas_de(REG, 39.5, -0.4, 1) == []


def test_clave_validada_y_guardada_sin_mostrarse():
    assert entorno.validar_campos({"aemet_key": CLAVE_FALSA}) == {"AEMET_API_KEY": CLAVE_FALSA}
    for mala in ("corta", "con espacios " + "x" * 30, "comilla\"" + "x" * 30):
        try: entorno.validar_campos({"aemet_key": mala}); assert False
        except ValueError as e: assert mala not in str(e)
    ruta = Path(tempfile.mkdtemp()) / ".env"
    entorno.actualizar_env(ruta, {"AEMET_API_KEY": CLAVE_FALSA})
    assert "AEMET_API_KEY=" + CLAVE_FALSA in ruta.read_text()


class _Resp:
    def __init__(self, codigo, cuerpo): self.status_code, self._c = codigo, cuerpo
    def json(self): return self._c


def test_dos_pasos_cabecera_y_sin_fuga_de_clave():
    os.environ["AEMET_API_KEY"] = CLAVE_FALSA
    aemet.limpiar_cache()
    llamadas = []
    def falso(url, headers=None, timeout=None):
        llamadas.append((url, dict(headers or {})))
        if url.startswith(aemet.BASE):
            return _Resp(200, {"estado": 200, "datos": "https://opendata.aemet.es/opendata/sh/abc"})
        return _Resp(200, REG)
    original = aemet.requests.get
    aemet.requests.get = falso
    try:
        r = aemet.cercanas(39.5, -0.4)
        assert r and len(llamadas) == 2
        assert llamadas[0][1].get("api_key") == CLAVE_FALSA          # paso 1: clave en cabecera
        assert "api_key" not in llamadas[1][1]                       # paso 2: URL temporal, sin clave
        assert all(CLAVE_FALSA not in u for u, _ in llamadas)        # nunca en la URL
        aemet.cercanas(39.5, -0.4); assert len(llamadas) == 2        # segunda vez: caché
        aemet.limpiar_cache()
        aemet.requests.get = lambda url, headers=None, timeout=None: _Resp(401, {})
        try: aemet.cercanas(39.5, -0.4); assert False
        except aemet.ErrorAemet as e: assert CLAVE_FALSA not in str(e) and "caducado" in str(e)
        aemet.limpiar_cache()
        aemet.requests.get = lambda url, headers=None, timeout=None: _Resp(200, {"estado": 200, "datos": "https://malo.example/x"})
        try: aemet.cercanas(39.5, -0.4); assert False
        except aemet.ErrorAemet: pass
    finally:
        aemet.requests.get = original
        os.environ.pop("AEMET_API_KEY", None); aemet.limpiar_cache()


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"): f(); print("OK", n)
