"""M4: dirección de viento desconocida / VRB nunca es 0° (Norte). pytest -v  o  python tests/test_movimiento.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import aeropuertos, movimiento   # noqa: E402

AERO = [{"icao": "LEVC", "distancia_km": 40.0}, {"icao": "LEBL", "distancia_km": 80.0}]


def test_direccion_a_grados():
    assert movimiento.direccion_a_grados("N") == 0.0
    assert movimiento.direccion_a_grados("ONO") == 292.5
    assert movimiento.direccion_a_grados(" so ") == 225.0
    assert movimiento.direccion_a_grados("W") == 270.0
    for malo in ("VRB", "", None, "Calma", "XYZ", "—"):
        assert movimiento.direccion_a_grados(malo) is None, malo      # NUNCA 0.0


def test_central_con_direccion_desconocida_se_ignora():
    r = movimiento.estimar_movimiento_nubes({"Viento": "20 km/h XYZ"}, {}, [])
    assert r["direccion_destino"] == "Desconocida"            # antes: dir 0 -> "viento del Norte" inventado
    r = movimiento.estimar_movimiento_nubes({"Viento": "20.0 km/h"}, {}, [])
    assert r["direccion_destino"] == "Desconocida"


def test_viento_del_oeste_va_hacia_el_este():
    r = movimiento.estimar_movimiento_nubes({"Viento": "20.0 km/h O"}, {}, [])
    assert r["direccion_destino"] == "E" and r["grados_destino"] == 90 and round(r["velocidad_kmh"]) == 20
    r = movimiento.estimar_movimiento_nubes({"Viento": "12,5 km/h N"}, {}, [])
    assert r["direccion_destino"] == "S" and r["velocidad_kmh"] == 12.5


def test_metar_vrb_no_cuenta_pero_el_resto_si():
    metars = {"LEVC": {"viento_nudos": 10.0, "viento_dir": None},        # VRB: sin dirección
              "LEBL": {"viento_nudos": 10.0, "viento_dir": 270.0}}       # del oeste
    r = movimiento.estimar_movimiento_nubes({}, metars, AERO)
    assert r["direccion_destino"] == "E" and round(r["velocidad_kmh"], 1) == 18.5
    r = movimiento.estimar_movimiento_nubes({}, {"LEVC": metars["LEVC"]}, AERO[:1])
    assert r["direccion_destino"] == "Desconocida"


class _Resp:
    def __init__(self, datos): self._d = datos
    def raise_for_status(self): pass
    def json(self): return self._d


def test_obtener_metars_vrb_y_ausentes_son_none():
    datos = [
        {"icaoId": "LEVC", "temp": 18, "dewp": 12, "wspd": 8, "wdir": "VRB", "wxString": "", "clouds": []},
        {"icaoId": "LEBL", "temp": 0, "dewp": -1, "wspd": 0, "wdir": 0, "clouds": []},        # 0 °C y calma REALES
        {"icaoId": "LEZL", "clouds": []},                                                       # sin ningún dato
    ]
    original = aeropuertos.requests.get
    aeropuertos.requests.get = lambda *a, **k: _Resp(datos)
    try:
        r = aeropuertos.obtener_metars(["LEVC", "LEBL", "LEZL"])
    finally:
        aeropuertos.requests.get = original
    assert r["LEVC"]["viento_dir"] is None and r["LEVC"]["viento_nudos"] == 8.0
    assert r["LEBL"]["temperatura"] == 0.0 and r["LEBL"]["viento_dir"] == 0.0 and r["LEBL"]["viento_nudos"] == 0.0
    assert r["LEZL"]["temperatura"] is None and r["LEZL"]["viento_dir"] is None and r["LEZL"]["viento_nudos"] is None


if __name__ == "__main__":
    fallos = 0
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            try: f(); print("OK  ", n)
            except Exception as e: fallos += 1; print("FALLA", n, repr(e))
    sys.exit(1 if fallos else 0)
