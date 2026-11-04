"""Capa «Nubes IR» del mapa (backend/nubes_ir.py). Sin red: la descarga se sustituye. Ejecutar: python tests/test_nubes_ir.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import nubes_ir as n   # noqa: E402

# Fragmento literal de https://view.eumetsat.int/geoserver/msg_fes/ir108/wms?service=WMS&version=1.3.0&request=GetCapabilities (3-nov-2026)
REAL = """<Layer queryable="1" opaque="0"><Name>ir108</Name>
<Dimension name="time" default="2026-11-03T19:00:00Z" units="ISO8601" nearestValue="1">2020-09-01T00:00:00.000Z/2026-11-03T19:00:00.000Z/PT15M</Dimension>
<Style><Name>raster</Name></Style></Layer>"""


def test_url_de_capabilities_y_servicio_de_una_capa():
    assert n.url_capabilities_capa("msg_fes:ir108") == \
        "https://view.eumetsat.int/geoserver/msg_fes/ir108/wms?service=WMS&version=1.3.0&request=GetCapabilities"
    assert n.wms_de_capa(" msg_fes:ir108 ") == ("https://view.eumetsat.int/geoserver/msg_fes/ir108/wms", "ir108")
    assert n.url_capabilities_capa("ir108") is None and n.url_capabilities_capa(":ir108") is None and n.url_capabilities_capa("a:b:c") is None
    assert n.wms_de_capa("ir108") is None


def test_tiempo_por_defecto():
    assert n.tiempo_por_defecto(REAL) == "2026-11-03T19:00:00Z"
    assert n.tiempo_por_defecto('<Dimension name="elevation" default="0">0</Dimension><Dimension default="X" name="time">t</Dimension>') == "X"
    assert n.tiempo_por_defecto("<Layer><Name>a</Name></Layer>") is None
    assert n.tiempo_por_defecto('<Dimension name="time" units="ISO8601">t</Dimension>') is None
    assert n.tiempo_por_defecto(None) is None


def test_fotogramas():
    f = n.fotogramas("2026-11-04T00:15:00Z", 4)                      # cruza la medianoche
    assert f == ["2026-11-03T23:30:00Z", "2026-11-03T23:45:00Z", "2026-11-04T00:00:00Z", "2026-11-04T00:15:00Z"]
    assert n.fotogramas("2026-11-04T00:15:00Z", 1) == ["2026-11-04T00:15:00Z"]
    assert len(n.fotogramas("2026-11-04T00:15:00Z")) == n.N_FOTOGRAMAS and n.fotogramas("2026-11-04T00:15:00Z")[-1] == "2026-11-04T00:15:00Z"
    assert n.fotogramas(None) == [] and n.fotogramas("basura") == []


def test_cache_fallo_y_ultima_hora_conocida():
    n.limpiar_cache()
    llamadas = []
    def bien(url): llamadas.append(url); return REAL
    r = n.obtener(ahora=1000.0, descargar=bien)
    # las teselas se piden al servicio de la capa (el WMS global daba HTTP 500 con time el 3-nov-2026)
    assert r == {"capa": "ir108", "wms": "https://view.eumetsat.int/geoserver/msg_fes/ir108/wms", "tiempo": "2026-11-03T19:00:00Z",
                 "fotogramas": n.fotogramas("2026-11-03T19:00:00Z"), "error": None}
    n.obtener(ahora=1000.0 + n.CACHE_S - 1, descargar=bien)
    assert len(llamadas) == 1                                        # dentro de la caché no se vuelve a preguntar
    def mal(url): raise OSError("sin red")
    r = n.obtener(ahora=1000.0 + n.CACHE_S + 1, descargar=mal)
    assert r["tiempo"] == "2026-11-03T19:00:00Z" and "sin red" in r["error"]     # se mantiene la última hora conocida
    assert r["fotogramas"] == n.fotogramas("2026-11-03T19:00:00Z")
    n.limpiar_cache()
    r = n.obtener(ahora=5000.0, descargar=mal)
    assert r["tiempo"] is None and "sin red" in r["error"]
    n.limpiar_cache()
    r = n.obtener(ahora=9000.0, descargar=lambda url: "<WMS_Capabilities/>")
    assert r["tiempo"] is None and "hora" in r["error"]
    n.limpiar_cache()


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"): v(); print("ok", k)
