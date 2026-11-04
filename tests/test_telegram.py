"""A4: configuración de Telegram sin secretos en el código. Ejecutar: pytest -v   o   python tests/test_telegram.py"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import telegram   # noqa: E402

TOKEN_OK = "123456789:AAEoGsNabcdefghijklmnopqrstuvwxyz0"
CLAVE_OK = "clave-de-prueba-1"


def _env(token=None, clave=None):
    os.environ.pop("AVAMET_TELEGRAM_TOKEN", None)
    os.environ.pop("AVAMET_TELEGRAM_CLAVE", None)
    if token is not None: os.environ["AVAMET_TELEGRAM_TOKEN"] = token
    if clave is not None: os.environ["AVAMET_TELEGRAM_CLAVE"] = clave
    telegram._FALLOS_CLAVE.clear()


def test_sin_secretos_en_el_codigo():
    fuente = (Path(telegram.__file__)).read_text(encoding="utf-8")
    assert "EstrellaPolar" not in fuente
    assert "TOKEN_OFUSCADO" not in fuente and "CLAVE_SECRETA" not in fuente


def test_configuracion_invalida():
    _env()
    assert telegram.configuracion_valida()[0] is False
    _env(token="", clave=CLAVE_OK)
    assert "TOKEN" in telegram.configuracion_valida()[1]
    _env(token="esto-no-es-un-token", clave=CLAVE_OK)
    assert "formato" in telegram.configuracion_valida()[1]
    _env(token=TOKEN_OK)
    assert "CLAVE" in telegram.configuracion_valida()[1]
    _env(token=TOKEN_OK, clave="corta")
    assert "corta" in telegram.configuracion_valida()[1]


def test_configuracion_valida():
    _env(token=TOKEN_OK, clave=CLAVE_OK)
    assert telegram.configuracion_valida() == (True, "")


def test_bot_no_arranca_ni_conecta_sin_config(capsys=None):
    _env()
    llamadas = []
    original = telegram.requests.get
    telegram.requests.get = lambda *a, **k: llamadas.append(a) or (_ for _ in ()).throw(AssertionError("no debe conectar"))
    try:
        asyncio.run(telegram.bucle_suscripciones())     # debe volver enseguida
    finally:
        telegram.requests.get = original
    assert llamadas == []


def test_envio_desactivado_sin_config():
    _env()
    assert telegram.enviar_mensaje("1", "x") is False
    assert telegram.enviar_a_todos_detalle("x") == (0, 0)


def test_clave_correcta_y_erronea():
    _env(token=TOKEN_OK, clave=CLAVE_OK)
    assert telegram.comprobar_clave(1, "mala", 0) == "MAL"
    assert telegram.comprobar_clave(1, CLAVE_OK, 1) == "OK"


def test_bloqueo_tras_5_fallos_y_desbloqueo():
    _env(token=TOKEN_OK, clave=CLAVE_OK)
    for i in range(5):
        assert telegram.comprobar_clave(7, "x", i) == "MAL"
    assert telegram.comprobar_clave(7, CLAVE_OK, 10) == "BLOQUEADO"      # ni la buena pasa
    assert telegram.comprobar_clave(8, CLAVE_OK, 10) == "OK"             # otro chat no afectado
    assert telegram.comprobar_clave(7, CLAVE_OK, telegram.VENTANA_CLAVE_S + 10) == "OK"


def test_limpiar_token_en_logs():
    _env(token=TOKEN_OK, clave=CLAVE_OK)
    msg = f"HTTPSConnectionPool: url: /bot{TOKEN_OK}/getUpdates"
    assert TOKEN_OK not in telegram._limpiar(msg)


def test_hilo_demonio_resultado_y_excepcion():
    async def prueba():
        assert await telegram._en_hilo_demonio(lambda a, b=0: a + b, 2, b=3) == 5
        try:
            await telegram._en_hilo_demonio(lambda: 1 / 0)
        except ZeroDivisionError:
            return "ok"
    assert asyncio.run(prueba()) == "ok"


def test_hilo_demonio_no_bloquea_el_cierre():
    """Una llamada lenta en curso (como el getUpdates largo) no debe retrasar el cierre de asyncio.run."""
    import subprocess, time
    codigo = (
        "import sys, asyncio, time\n"
        f"sys.path.insert(0, {str(Path(telegram.__file__).resolve().parent.parent)!r})\n"
        "from backend import telegram\n"
        "async def m():\n"
        "    t = asyncio.ensure_future(telegram._en_hilo_demonio(time.sleep, 6))\n"
        "    await asyncio.sleep(0.2); t.cancel()\n"
        "asyncio.run(m())\n")
    t0 = time.time()
    subprocess.run([sys.executable, "-c", codigo], check=True)
    assert time.time() - t0 < 4, "el cierre esperó al hilo"


if __name__ == "__main__":
    fallos = 0
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            try: f(); print("OK  ", n)
            except Exception as e: fallos += 1; print("FALLA", n, repr(e))
    sys.exit(1 if fallos else 0)
