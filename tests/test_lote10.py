"""Lote 10: /api/modo (pestaña Configuración solo en escritorio y desde este equipo) y contenido del .spec.
Se ejecuta con:  python tests/test_lote10.py
"""
import asyncio, os, re, sys, tempfile, types
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
tmp = tempfile.mkdtemp()
os.environ["AVAMET_DATA_DIR"] = tmp
for k in ("AVAMET_TELEGRAM_TOKEN", "AVAMET_TELEGRAM_CLAVE", "AVAMET_ADMIN_CHAT_ID"): os.environ.pop(k, None)

try:
    import fastapi  # noqa
    REAL = True
except ImportError:
    REAL = False
    # fastapi no instalado: se usa una versión mínima para poder importar backend.main
    import importlib.util
    fa = types.ModuleType("fastapi"); fr = types.ModuleType("fastapi.responses")
    class _App:
        def __getattr__(self, n): return lambda *a, **k: (lambda f: f)
    fa.FastAPI = lambda *a, **k: _App(); fa.Body = lambda *a, **k: None; fa.HTTPException = Exception
    fa.BackgroundTasks = object; fa.Request = object
    fr.FileResponse = fr.Response = fr.StreamingResponse = object
    sys.modules["fastapi"] = fa; sys.modules["fastapi.responses"] = fr

from backend import config, main


class Cli:  # request mínimo
    def __init__(self, host): self.client = types.SimpleNamespace(host=host)


def llamar(host):
    return asyncio.run(main.api_modo(Cli(host)))


def test_headless_oculta():
    config.HEADLESS = True
    assert llamar("127.0.0.1") == {"configuracion": False}


def test_otro_equipo_oculta():
    config.HEADLESS = False
    assert llamar("192.168.1.50") == {"configuracion": False}
    assert llamar("") == {"configuracion": False}
    assert "carpeta_datos" not in llamar("10.0.0.2")          # no revela rutas


def test_escritorio_local_y_sin_secretos():
    config.HEADLESS = False
    os.environ["AVAMET_TELEGRAM_TOKEN"] = "123456789:SECRETO_SECRETO"
    os.environ["AVAMET_TELEGRAM_CLAVE"] = "corta"
    os.environ["AVAMET_ADMIN_CHAT_ID"] = "42"
    m = llamar("127.0.0.1")
    assert m["configuracion"] is True and m["telegram_token"] is True and m["admin_id"] is True
    assert m["telegram_clave"] is False                       # < 8 caracteres
    assert "SECRETO" not in repr(m) and "corta" not in repr(m)  # nunca devuelve valores
    os.environ["AVAMET_TELEGRAM_CLAVE"] = "ochocaracteres"
    assert llamar("::1")["telegram_clave"] is True
    assert m["carpeta_datos"] == str(Path(tmp))
    assert m["env_existe"] is False
    (Path(tmp) / ".env").write_text("X=1")
    assert llamar("127.0.0.1")["env_existe"] is True


def test_spec_completo():
    spec = (RAIZ / "AVAMET_Monitor.spec").read_text(encoding="utf-8")
    # los módulos de backend/ se añaden SOLOS (os.listdir): no hay lista que se quede desfasada
    assert "os.listdir(os.path.join(_RAIZ, 'backend'))" in spec and "'backend.' + f[:-3]" in spec
    assert "'coordenadas.json'" in spec and "collect_data_files('tzdata')" in spec
    assert "('frontend', 'frontend')" in spec                 # incluye frontend/vendor/
    for paquete in ("uvicorn", "fastapi", "starlette", "pydantic", "bs4", "requests", "urllib3", "certifi", "anyio", "h11"):
        assert f"'{paquete}'" in spec, paquete


def test_index_pestana():
    h = (RAIZ / "frontend" / "index.html").read_text(encoding="utf-8")
    assert re.search(r'id="btn-config"[^>]*\bhidden\b', h)   # oculto por defecto
    assert 'id="modal-config"' in h and "/api/modo" in h and 'data-tab="ayuda"' not in h and 'data-tab="config"' not in h


def test_cabeceras_anticache_y_sw():
    h = main._SIN_CACHE
    assert h["Cache-Control"] == "no-cache, no-store, must-revalidate" and h["Pragma"] == "no-cache" and h["Expires"] == "0"
    import inspect
    for f in (main.index, main.sw, main.manifest):          # las tres rutas usan esas cabeceras
        assert "headers=_SIN_CACHE" in inspect.getsource(f), f.__name__
    sw = (RAIZ / "frontend" / "sw.js").read_text(encoding="utf-8")
    assert "skipWaiting()" in sw and "clients.claim()" in sw
    assert "certifi" in (RAIZ / "AVAMET_Monitor.spec").read_text(encoding="utf-8")


if __name__ == "__main__":
    n = 0
    for nombre, f in list(globals().items()):
        if nombre.startswith("test_") and callable(f):
            f(); n += 1; print("OK", nombre)
    print(f"{n} pruebas OK")
