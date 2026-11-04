"""A5: token Bearer. Ejecutar: pytest -v   o   python tests/test_seguridad.py"""
import os
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config, seguridad   # noqa: E402

config.API_MAX_FALLOS = 5
config.API_VENTANA_FALLOS_S = 300
TOKEN = "t" * 20 + "ok-token-de-prueba"


def _reset(env=None, ruta=None):
    os.environ.pop("AVAMET_API_TOKEN", None)
    if env is not None:
        os.environ["AVAMET_API_TOKEN"] = env
    config.API_TOKEN_PATH = str(ruta or Path(tempfile.mkdtemp()) / "api_token.txt")
    seguridad._TOKEN = None
    seguridad._FALLOS.clear()


def test_token_desde_variable_de_entorno():
    _reset(env=TOKEN)
    assert seguridad.obtener_token() == TOKEN
    assert seguridad.comprobar("Bearer " + TOKEN, "a", 1000.0) == "OK"
    assert seguridad.comprobar("bearer " + TOKEN, "a", 1000.0) == "OK"          # esquema sin distinguir mayúsculas


def test_rechaza_sin_cabecera_esquema_erroneo_y_token_erroneo():
    _reset(env=TOKEN)
    assert seguridad.comprobar(None, "a", 1.0) == "NO_AUTORIZADO"
    assert seguridad.comprobar("", "a", 1.0) == "NO_AUTORIZADO"
    assert seguridad.comprobar("Basic " + TOKEN, "a", 1.0) == "NO_AUTORIZADO"
    assert seguridad.comprobar("Bearer " + TOKEN + "x", "a", 1.0) == "NO_AUTORIZADO"
    assert seguridad.comprobar(TOKEN, "a", 1.0) == "NO_AUTORIZADO"              # sin la palabra Bearer


def test_token_corto_en_entorno_se_ignora_y_se_genera_uno():
    _reset(env="corto")
    t = seguridad.obtener_token()
    assert t != "corto" and len(t) >= seguridad.LONGITUD_MIN


def test_se_genera_se_guarda_y_se_reutiliza():
    d = Path(tempfile.mkdtemp()); ruta = d / "sub" / "api_token.txt"
    _reset(ruta=ruta)
    t1 = seguridad.obtener_token()
    assert ruta.read_text().strip() == t1 and len(t1) >= 32
    if os.name == "posix":
        assert stat.S_IMODE(ruta.stat().st_mode) == 0o600                        # solo el propietario
    seguridad._TOKEN = None
    assert seguridad.obtener_token() == t1                                       # reinicio: mismo token


def test_bloqueo_tras_5_fallos_y_desbloqueo_por_ventana():
    _reset(env=TOKEN)
    for i in range(5):
        assert seguridad.comprobar("Bearer mal", "ip1", 100.0 + i) == "NO_AUTORIZADO"
    assert seguridad.comprobar("Bearer " + TOKEN, "ip1", 110.0) == "BLOQUEADO"   # ni el correcto pasa bloqueado
    assert seguridad.comprobar("Bearer " + TOKEN, "ip2", 110.0) == "OK"          # otra IP no se ve afectada
    assert seguridad.comprobar("Bearer " + TOKEN, "ip1", 100.0 + 301) == "OK"    # pasada la ventana


def test_el_exito_borra_los_fallos():
    _reset(env=TOKEN)
    for i in range(4):
        seguridad.comprobar("Bearer mal", "ip", 10.0 + i)
    assert seguridad.comprobar("Bearer " + TOKEN, "ip", 20.0) == "OK"
    for i in range(4):
        assert seguridad.comprobar("Bearer mal", "ip", 30.0 + i) == "NO_AUTORIZADO"   # el contador volvió a 0


if __name__ == "__main__":
    fallos = 0
    for nombre, fn in sorted(globals().items()):
        if nombre.startswith("test_") and callable(fn):
            try:
                fn(); print(f"OK    {nombre}")
            except Exception as e:                                          # noqa: BLE001
                fallos += 1; print(f"FALLO {nombre}: {type(e).__name__}: {e}")
    sys.exit(1 if fallos else 0)
