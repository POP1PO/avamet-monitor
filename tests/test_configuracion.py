"""Formulario de Configuración: validación, escritura segura del .env y protecciones del endpoint.
Se ejecuta con:  python tests/test_configuracion.py
"""
import asyncio, os, sys, tempfile, types
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
TMP = Path(tempfile.mkdtemp())
os.environ["AVAMET_DATA_DIR"] = str(TMP)
for k in ("AVAMET_TELEGRAM_TOKEN", "AVAMET_TELEGRAM_CLAVE", "AVAMET_ADMIN_CHAT_ID"): os.environ.pop(k, None)

try:
    import fastapi  # noqa
except ImportError:                      # sin fastapi: versión mínima para poder importar backend.main
    fa = types.ModuleType("fastapi"); fr = types.ModuleType("fastapi.responses")
    class _App:
        def __getattr__(self, n): return lambda *a, **k: (lambda f: f)
    class HTTPException(Exception):
        def __init__(self, status_code, detail=None, headers=None): self.status_code, self.detail = status_code, detail
    fa.FastAPI = lambda *a, **k: _App(); fa.Body = lambda *a, **k: None; fa.HTTPException = HTTPException
    fa.BackgroundTasks = object; fa.Request = object
    fr.FileResponse = fr.Response = fr.StreamingResponse = object
    sys.modules["fastapi"] = fa; sys.modules["fastapi.responses"] = fr

from backend import config, entorno, main, telegram

TOKEN = "123456789:AAEhBOweik6ad5ErTz1qXyZ0_abcDEFghi"
PUERTO = config.PORT


def peticion(host="127.0.0.1", cliente="127.0.0.1", origen=None, cabecera=True, hostname=None):
    h = {"host": hostname or f"127.0.0.1:{PUERTO}"}
    if origen: h["origin"] = origen
    if cabecera: h["x-avamet-config"] = "1"
    return types.SimpleNamespace(client=types.SimpleNamespace(host=cliente), headers=h)


def codigo(coro_fn, *a, **k):
    try:
        asyncio.run(coro_fn(*a, **k))
    except Exception as e:
        return getattr(e, "status_code", type(e).__name__)
    return 200


# ---------- validación ----------
def test_validar():
    r = entorno.validar_campos({"token": TOKEN, "clave": "ClaveSecreta1", "admin_id": " 42 "})
    assert r == {"AVAMET_TELEGRAM_TOKEN": TOKEN, "AVAMET_TELEGRAM_CLAVE": "ClaveSecreta1", "AVAMET_ADMIN_CHAT_ID": "42"}
    assert entorno.validar_campos({"admin_id": "-100123"}) == {"AVAMET_ADMIN_CHAT_ID": "-100123"}       # grupos: negativo
    for malo in ({}, {"token": ""}, {"token": "abc"}, {"clave": "corta"}, {"clave": "con espacio 123"}, {"clave": 'comi"llas123'},
                 {"clave": "con#almohadilla1"}, {"clave": "dolar$dolar123"}, {"clave": "a\nb" * 5}, {"admin_id": "12ab"},
                 {"admin_id": "1; rm"}, "no es dict"):
        try:
            entorno.validar_campos(malo); raise AssertionError(f"debía fallar: {malo!r}")
        except ValueError:
            pass


def test_mascara_no_revela():
    m = entorno.enmascarar_token(TOKEN)
    assert m == "123456789:••••••••Fghi" or m.endswith("Fghi")
    assert TOKEN.split(":")[1][:-4] not in m and entorno.enmascarar_token("") == ""


# ---------- escritura del .env ----------
def test_actualiza_sin_borrar_lo_demas():
    f = TMP / "a" / ".env"; f.parent.mkdir()
    f.write_text("# Comentario\nAVAMET_TELEGRAM_TOKEN=viejo\n# AVAMET_ADMIN_CHAT_ID=comentada\nAVAMET_PORT=9000\nAVAMET_TELEGRAM_TOKEN=duplicado\n", encoding="utf-8")
    entorno.actualizar_env(f, {"AVAMET_TELEGRAM_TOKEN": TOKEN, "AVAMET_ADMIN_CHAT_ID": "7"})
    t = f.read_text(encoding="utf-8").splitlines()
    assert t == ["# Comentario", f"AVAMET_TELEGRAM_TOKEN={TOKEN}", "# AVAMET_ADMIN_CHAT_ID=comentada", "AVAMET_PORT=9000", "AVAMET_ADMIN_CHAT_ID=7"], t
    assert (f.parent / ".env.bak").read_text(encoding="utf-8").startswith("# Comentario")           # copia previa
    assert not (f.parent / ".env.tmp").exists()


def test_conserva_crlf_y_bom():
    f = TMP / "b.env"
    f.write_bytes("﻿AVAMET_PORT=1\r\n# nota\r\n".encode("utf-8"))
    entorno.actualizar_env(f, {"AVAMET_ADMIN_CHAT_ID": "5"})
    b = f.read_bytes()
    assert b == b"AVAMET_PORT=1\r\n# nota\r\nAVAMET_ADMIN_CHAT_ID=5\r\n", b               # CRLF conservado, BOM fuera


def test_crea_si_no_existe_y_lee_de_vuelta():
    f = TMP / "nuevo" / ".env"
    entorno.actualizar_env(f, {"AVAMET_TELEGRAM_CLAVE": "ClaveSecreta1"})
    assert f.is_file() and entorno.claves_definidas_en(f) == {"AVAMET_TELEGRAM_CLAVE"}
    from dotenv import dotenv_values                                    # y python-dotenv lo interpreta igual
    assert dotenv_values(f)["AVAMET_TELEGRAM_CLAVE"] == "ClaveSecreta1"


def test_rechaza_inyeccion():
    try:
        entorno.actualizar_env(TMP / "c.env", {"AVAMET_X": "uno\nAVAMET_ADMIN_CHAT_ID=1"}); raise AssertionError
    except ValueError:
        pass
    try:
        entorno.actualizar_env(TMP / "c.env", {"MALA CLAVE": "x"}); raise AssertionError
    except ValueError:
        pass


# ---------- endpoint ----------
def test_proteccion_del_endpoint():
    config.HEADLESS = False
    ok = {"admin_id": "42"}
    assert codigo(main.api_configuracion_leer, peticion()) == 200
    assert codigo(main.api_configuracion_guardar, peticion(cliente="192.168.1.9"), ok) == 403          # otro equipo
    assert codigo(main.api_configuracion_leer, peticion(cliente="10.0.0.5")) == 403
    assert codigo(main.api_configuracion_guardar, peticion(hostname="atacante.com"), ok) == 403         # DNS rebinding
    assert codigo(main.api_configuracion_guardar, peticion(origen="http://evil.example"), ok) == 403    # web ajena
    assert codigo(main.api_configuracion_guardar, peticion(cabecera=False), ok) == 403                  # sin cabecera X-AVAMET-Config
    assert codigo(main.api_configuracion_guardar, peticion(origen=f"http://127.0.0.1:{PUERTO}"), {}) == 400   # vacío
    config.HEADLESS = True
    assert codigo(main.api_configuracion_guardar, peticion(), ok) == 403                               # headless
    assert codigo(main.api_configuracion_leer, peticion()) == 403
    config.HEADLESS = False


def test_guardar_aplica_y_reinicia_bot():
    config.HEADLESS = False
    config.DATA_DIR = TMP / "datos"
    arrancado = []

    async def bucle_falso():
        arrancado.append(os.environ.get("AVAMET_TELEGRAM_TOKEN"))
        await asyncio.sleep(3600)

    async def escenario():
        orig = telegram.bucle_suscripciones
        telegram.bucle_suscripciones = bucle_falso
        try:
            viejo = asyncio.create_task(bucle_falso(), name="telegram")
            main._TAREAS_FONDO[:] = [viejo]
            await asyncio.sleep(0)
            r = await main.api_configuracion_guardar(peticion(origen=f"http://127.0.0.1:{PUERTO}"),
                                                     {"token": TOKEN, "clave": "ClaveSecreta1", "admin_id": "42"})
            await asyncio.sleep(0.05)
            assert viejo.cancelled() or viejo.done()                              # el bot viejo se paró
            nuevo = main._TAREAS_FONDO[0]
            assert nuevo is not viejo and nuevo.get_name() == "telegram" and not nuevo.done()   # y hay uno nuevo
            nuevo.cancel()
            return r
        finally:
            telegram.bucle_suscripciones = orig
    r = asyncio.run(escenario())
    assert r["ok"] and r["bot_reiniciado"] and sorted(r["actualizados"]) == ["AVAMET_ADMIN_CHAT_ID", "AVAMET_TELEGRAM_CLAVE", "AVAMET_TELEGRAM_TOKEN"]
    assert r["telegram"]["activo"] is True and r["avisos"] == []
    assert arrancado[-1] == TOKEN                                                    # arrancó YA con el token nuevo
    assert os.environ["AVAMET_ADMIN_CHAT_ID"] == "42" and telegram.es_admin(42)    # efecto inmediato sin reiniciar
    assert (TMP / "datos" / ".env").is_file()
    # lectura: nunca devuelve el token completo ni la contraseña
    j = asyncio.run(main.api_configuracion_leer(peticion()))
    assert j["token_configurado"] and j["clave_configurada"] and j["admin_id"] == "42" and j["env_existe"]
    assert TOKEN not in repr(j) and "ClaveSecreta1" not in repr(j)


def test_aviso_si_otro_env_tiene_prioridad():
    config.HEADLESS = False
    config.DATA_DIR = TMP / "datos2"
    cwd = TMP / "cwd"; cwd.mkdir(); (cwd / ".env").write_text("AVAMET_ADMIN_CHAT_ID=1\n")
    viejo = os.getcwd(); os.chdir(cwd)
    try:
        r = asyncio.run(main.api_configuracion_guardar(peticion(), {"admin_id": "99"}))
    finally:
        os.chdir(viejo)
    assert len(r["avisos"]) == 1 and str(cwd / ".env") in r["avisos"][0] and r["bot_reiniciado"] is False


def test_index_formulario():
    h = (RAIZ / "frontend" / "index.html").read_text(encoding="utf-8")
    for x in ('id="cfg-form"', 'id="cfg-token"', 'id="cfg-clave"', 'id="cfg-admin"', "💾 Guardar Configuración",
              "X-AVAMET-Config", "/api/configuracion", "Configuración detectada"):
        assert x in h, x


if __name__ == "__main__":
    n = 0
    for nombre, f in list(globals().items()):
        if nombre.startswith("test_") and callable(f):
            f(); n += 1; print("OK", nombre)
    print(f"{n} pruebas OK")
