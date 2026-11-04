"""Cache-busting y acceso a frontend/vendor. pytest -v  o  python tests/test_estaticos.py"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.estaticos import Frontend   # noqa: E402


def _front():
    d = Path(tempfile.mkdtemp())
    (d / "index.html").write_text('<script src="/vendor/chart/chart.umd.js?v=__BUILD__"></script>', encoding="utf-8")
    (d / "sw.js").write_text("const CACHE_NAME = 'avamet-__BUILD__';", encoding="utf-8")
    (d / "manifest.json").write_text("{}", encoding="utf-8")
    for r in ("leaflet/leaflet.js", "leaflet/leaflet.css", "chart/chart.umd.js"):
        (d / "vendor" / r).parent.mkdir(parents=True, exist_ok=True)
        (d / "vendor" / r).write_text("// " + r, encoding="utf-8")
    return d, Frontend(d, "1.0.0")


def test_build_se_sustituye_y_es_estable():
    d, f = _front()
    b = f.build_id()
    assert len(b) == 10 and b == f.build_id()
    assert "__BUILD__" not in f.render("index.html") and f"avamet-{b}" in f.render("sw.js")


def test_build_cambia_si_cambia_cualquier_fichero_del_frontend():
    d, f = _front(); b0 = f.build_id()
    (d / "index.html").write_text("<p>nuevo</p>__BUILD__", encoding="utf-8")
    b1 = f.build_id(); assert b1 != b0
    (d / "vendor" / "chart" / "chart.umd.js").write_text("// otra versión", encoding="utf-8")
    b2 = f.build_id(); assert b2 not in (b0, b1)
    assert f.render("sw.js") != "const CACHE_NAME = 'avamet-%s';" % b0           # el SW cambia => el navegador lo actualiza


def test_build_cambia_con_la_version_de_la_app():
    d, f = _front(); b = f.build_id()
    assert Frontend(d, "1.0.1").build_id() != b


def test_resolver_vendor_valido():
    d, f = _front()
    ruta, tipo = f.resolver_vendor("leaflet/leaflet.js")
    assert ruta == (d / "vendor" / "leaflet" / "leaflet.js").resolve() and tipo.startswith("text/javascript")
    assert f.resolver_vendor("leaflet/leaflet.css")[1].startswith("text/css")


def test_resolver_vendor_rechaza_intentos_de_salir_de_la_carpeta():
    d, f = _front()
    (d / "secreto.js").write_text("x", encoding="utf-8")
    for malo in ("../secreto.js", "leaflet/../../secreto.js", "/etc/passwd", "..\\secreto.js", "leaflet/leaflet.js\x00.png",
                 "", "leaflet", "leaflet/noexiste.js", "leaflet/leaflet.exe"):
        assert f.resolver_vendor(malo) is None, malo
    if hasattr(os, "symlink"):
        try:
            os.symlink(d / "secreto.js", d / "vendor" / "enlace.js")
            assert f.resolver_vendor("enlace.js") is None                  # un enlace simbólico no puede sacar ficheros
        except OSError:
            pass


def test_vendor_faltante():
    d, f = _front(); assert f.vendor_faltante() == []
    (d / "vendor" / "chart" / "chart.umd.js").unlink()
    assert f.vendor_faltante() == ["chart/chart.umd.js"]
    assert Frontend(Path(tempfile.mkdtemp()), "x").vendor_faltante() == list(__import__("backend.estaticos", fromlist=["x"]).VENDOR_REQUERIDOS)


def test_index_real_usa_vendor_y_marcador():
    raiz = Path(__file__).resolve().parent.parent / "frontend"
    h = (raiz / "index.html").read_text(encoding="utf-8")
    assert "/vendor/chart/chart.umd.js?v=__BUILD__" in h and "/vendor/leaflet/leaflet.js?v=__BUILD__" in h
    assert "__BUILD__" in (raiz / "sw.js").read_text(encoding="utf-8")


if __name__ == "__main__":
    fallos = 0
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            try: f(); print("OK  ", n)
            except Exception as e: fallos += 1; print("FALLA", n, repr(e))
    sys.exit(1 if fallos else 0)
