"""scripts/descargar_vendor.py contra un servidor local simulado. pytest -v  o  python tests/test_descargar_vendor.py"""
import http.server
import sys
import tempfile
import threading
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "scripts"))
import descargar_vendor as dv   # noqa: E402

RELLENO = "x" * 20000
FICHEROS = {
    "/leaflet@1.9.4/dist/leaflet.js": "/* @preserve\n * Leaflet 1.9.4, a JS library */\n" + RELLENO,
    "/leaflet@1.9.4/dist/leaflet.css": "/* leaflet css */" + RELLENO,
    "/leaflet@1.9.4/LICENSE": "BSD-2-Clause",
    "/chart.js@4.4.1/dist/chart.umd.min.js": "/*!\n * Chart.js v4.4.1\n */\n" + RELLENO,      # el .umd.js da 404: prueba el respaldo
    "/chart.js@4.4.1/LICENSE.md": "MIT",
}
for i in dv.IMAGENES_LEAFLET:
    FICHEROS[f"/leaflet@1.9.4/dist/images/{i}"] = "PNG" * 10


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        d = FICHEROS.get(self.path)
        if d is None:
            self.send_response(404); self.end_headers(); return
        self.send_response(200); self.end_headers(); self.wfile.write(d.encode())
    def log_message(self, *a): pass


def _servidor():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def test_descarga_completa_y_verificada():
    srv, base = _servidor(); dest = Path(tempfile.mkdtemp())
    try:
        assert dv.main(["--cdn", base, "--destino", str(dest)]) == 0
    finally:
        srv.shutdown()
    for r in ("leaflet/leaflet.js", "leaflet/leaflet.css", "chart/chart.umd.js", "leaflet/images/layers.png", "VERSIONS.txt"):
        assert (dest / r).is_file(), r
    v = (dest / "VERSIONS.txt").read_text(encoding="utf-8")
    assert "Leaflet 1.9.4" in v and "Chart.js 4.4.1" in v and "sha256" in v
    assert dv.main(["--destino", str(dest), "--solo-comprobar"]) == 0


def test_rechaza_un_fichero_que_no_es_el_esperado():
    FICHEROS["/leaflet@1.9.4/dist/leaflet.js"] = "<html>Error 404</html>" + RELLENO       # sin cabecera de Leaflet
    srv, base = _servidor(); dest = Path(tempfile.mkdtemp())
    try:
        assert dv.main(["--cdn", base, "--destino", str(dest)]) == 1
        assert not (dest / "leaflet" / "leaflet.js").exists() and not (dest / "VERSIONS.txt").exists()
    finally:
        srv.shutdown(); FICHEROS["/leaflet@1.9.4/dist/leaflet.js"] = "/* @preserve\n * Leaflet 1.9.4 */\n" + RELLENO


def test_solo_comprobar_indica_lo_que_falta():
    assert dv.main(["--destino", tempfile.mkdtemp(), "--solo-comprobar"]) == 1


def test_verificar_cabecera():
    dv.verificar_cabecera("a", b"/* Leaflet 1.9.4 */" + b"x" * 20000, "leaflet", "1.9.4")
    for malo in (b"x" * 20000, b"/* Leaflet 1.8.0 */" + b"x" * 20000, b"corto"):
        try: dv.verificar_cabecera("a", malo, "leaflet", "1.9.4")
        except ValueError: continue
        raise AssertionError("debía fallar")


if __name__ == "__main__":
    fallos = 0
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            try: f(); print("OK  ", n)
            except Exception as e: fallos += 1; print("FALLA", n, repr(e))
    sys.exit(1 if fallos else 0)
