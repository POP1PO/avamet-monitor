"""Pruebas de backend/actualizacion.py (sin red)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import actualizacion as a

REL = {"name": "Última versión (nº 57)", "html_url": "https://github.com/x/y/releases/tag/ultima",
       "assets": [{"name": "AVAMET_Monitor-windows-x64.zip", "browser_download_url": "https://d/win.zip"},
                  {"name": "AVAMET_Monitor-linux-x86_64.tar.gz", "browser_download_url": "https://d/lin.tgz"}]}


def test_numero_de_titulo():
    assert a.numero_de_titulo("Última versión (nº 57)") == 57
    assert a.numero_de_titulo("Versión nº 8") == 8
    assert a.numero_de_titulo("sin numero") is None
    assert a.numero_de_titulo("") is None


def test_hay_version_nueva():
    r = a.evaluar(REL, 50, "win32")
    assert r["disponible"] and r["ultima"] == 57 and r["actual"] == 50 and r["descarga"] == "https://d/win.zip"
    assert a.evaluar(REL, 50, "linux")["descarga"] == "https://d/lin.tgz"


def test_al_dia_o_mas_nueva_no_avisa():
    assert not a.evaluar(REL, 57, "linux")["disponible"]
    assert not a.evaluar(REL, 60, "linux")["disponible"]


def test_titulo_ilegible_no_avisa_y_dice_por_que():
    r = a.evaluar({"name": "raro", "assets": []}, 50)
    assert not r["disponible"] and r["error"]


def test_sin_build_queda_desactivada():
    assert a.evaluar(REL, None)["activo"] is False


def test_consultar_no_lanza_con_fallo_de_red():
    a.BUILD = 10
    a._cache.update(t=0.0, dato=None, ttl=0.0)
    def falla(*x, **k): raise OSError("sin red")
    orig = a.requests.get; a.requests.get = falla
    try:
        r = a.consultar(forzar=True)
    finally:
        a.requests.get = orig; a.BUILD = None
    assert r["disponible"] is False and "No se pudo comprobar" in r["error"]


def test_desactivada_por_entorno():
    import os
    a.BUILD = 10; os.environ["AVAMET_COMPROBAR_ACTUALIZACIONES"] = "0"
    try:
        assert a.consultar()["activo"] is False
    finally:
        del os.environ["AVAMET_COMPROBAR_ACTUALIZACIONES"]; a.BUILD = None


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"): f(); print("OK", n)
