"""Lote 8: /pueblo solo para el administrador, seguimiento de lluvia del radar y badge con dato caducado.
Ejecutar: pytest -v   o   python tests/test_lote8.py"""
import asyncio
import json
import os
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import alertas, config, telegram   # noqa: E402

TOKEN_OK = "123456789:AAEoGsNabcdefghijklmnopqrstuvwxyz0"


# ---------- Telegram: /pueblo solo administrador ----------
def test_es_admin():
    os.environ.pop("AVAMET_ADMIN_CHAT_ID", None)
    assert not telegram.es_admin(111) and not telegram.es_admin("")        # sin configurar: nadie
    os.environ["AVAMET_ADMIN_CHAT_ID"] = " 111 "
    assert telegram.es_admin(111) and telegram.es_admin("111")
    assert not telegram.es_admin(1111) and not telegram.es_admin(11) and not telegram.es_admin(None)
    os.environ.pop("AVAMET_ADMIN_CHAT_ID", None)


def _simular_bot(admin, mensajes_entrantes):
    """Ejecuta el bucle real del bot con Telegram simulado. Devuelve (mensajes_enviados, llamadas_a_ubicacion)."""
    tmp = Path(tempfile.mkdtemp())
    telegram.SUSCRIPTORES_FILE = tmp / "subs.json"
    telegram.COORDS_FILE = tmp / "coords.json"
    telegram.COORDS_FILE.write_text(json.dumps({"c01": {"municipio": "Vila-real", "nombre": "Vila-real"}}), encoding="utf-8")
    telegram.SUSCRIPTORES_FILE.write_text(json.dumps({"1": {"nombre": "admin"}, "2": {"nombre": "otro"}}), encoding="utf-8")
    os.environ["AVAMET_TELEGRAM_TOKEN"] = TOKEN_OK
    os.environ["AVAMET_TELEGRAM_CLAVE"] = "clave-de-prueba-1"
    if admin is None: os.environ.pop("AVAMET_ADMIN_CHAT_ID", None)
    else: os.environ["AVAMET_ADMIN_CHAT_ID"] = admin
    enviados, ubicaciones, llamadas = [], [], {"n": 0}

    def get(url, **k):
        llamadas["n"] += 1
        ups = [{"update_id": i + 1, "message": {"text": t, "chat": {"id": cid, "first_name": f"u{cid}"}}}
               for i, (cid, t) in enumerate(mensajes_entrantes)] if llamadas["n"] == 1 else []
        return types.SimpleNamespace(json=lambda: {"ok": True, "result": ups})

    def post(url, **k):
        ubicaciones.append(k.get("json")); return types.SimpleNamespace(ok=True, status_code=200)

    orig = (telegram.requests.get, telegram.requests.post, telegram.enviar_mensaje, telegram.seguridad.obtener_token)
    telegram.requests.get, telegram.requests.post = get, post
    telegram.enviar_mensaje = lambda cid, txt: enviados.append((cid, txt)) or True
    telegram.seguridad.obtener_token = lambda: "tok"

    async def correr():
        t = asyncio.create_task(telegram.bucle_suscripciones())
        await asyncio.sleep(0.6); t.cancel()
        try: await t
        except BaseException: pass
    try: asyncio.run(correr())
    finally: telegram.requests.get, telegram.requests.post, telegram.enviar_mensaje, telegram.seguridad.obtener_token = orig
    os.environ.pop("AVAMET_ADMIN_CHAT_ID", None)
    return enviados, ubicaciones


def test_pueblo_admin_puede():
    env, ubi = _simular_bot("1", [(1, "/pueblo vila")])
    assert ubi == [{"estacion_id": "c01"}] and any("Ubicación cambiada" in t for _, t in env)


def test_pueblo_no_admin_denegado():
    env, ubi = _simular_bot("1", [(2, "/pueblo vila")])
    assert ubi == [] and any(c == 2 and "No tienes permisos" in t for c, t in env)


def test_pueblo_sin_admin_configurado_nadie():
    env, ubi = _simular_bot(None, [(1, "/pueblo vila"), (2, "/lugar vila")])
    assert ubi == [] and sum("No tienes permisos" in t for _, t in env) == 2
    assert all("AVAMET_ADMIN_CHAT_ID" in t for _, t in env)


def test_pueblo_no_suscrito_sigue_pidiendo_start_y_id_funciona():
    env, ubi = _simular_bot("1", [(9, "/pueblo vila"), (9, "/id")])
    assert ubi == [] and any("/start" in t for _, t in env) and any("<code>9</code>" in t for _, t in env)


# ---------- Radar: solo cuenta lluvia que SUBIÓ en los últimos 30 min ----------
def test_radar_primera_lectura_no_cuenta_aunque_haya_lluvia_acumulada():
    seg = {}; t0 = 1_000_000.0
    alertas.registrar_lluvia_estacion(seg, "e1", 12.0, t0)             # llovió ayer/esta mañana
    assert not alertas.lluvia_reciente(seg, "e1", t0)
    alertas.registrar_lluvia_estacion(seg, "e1", 12.0, t0 + 60)        # sigue igual
    assert not alertas.lluvia_reciente(seg, "e1", t0 + 60)


def test_radar_subida_cuenta_30_min_y_caduca():
    seg = {}; t0 = 1_000_000.0
    alertas.registrar_lluvia_estacion(seg, "e1", 2.0, t0)
    alertas.registrar_lluvia_estacion(seg, "e1", 2.2, t0 + 60)         # +0.2 mm
    assert alertas.lluvia_reciente(seg, "e1", t0 + 60)
    alertas.registrar_lluvia_estacion(seg, "e1", 2.2, t0 + 60 + 1799)
    assert alertas.lluvia_reciente(seg, "e1", t0 + 60 + 1799)          # 29 min 59 s: aún
    assert not alertas.lluvia_reciente(seg, "e1", t0 + 60 + 1801)      # pasados 30 min: ya no
    alertas.registrar_lluvia_estacion(seg, "e1", 2.4, t0 + 4000)       # nueva subida
    assert alertas.lluvia_reciente(seg, "e1", t0 + 4000)


def test_radar_subida_bajo_umbral_y_reinicio_de_contador():
    seg = {}; t0 = 1_000_000.0
    alertas.registrar_lluvia_estacion(seg, "e1", 2.00, t0)
    alertas.registrar_lluvia_estacion(seg, "e1", 2.05, t0 + 60)        # 0.05 < 0.1
    assert not alertas.lluvia_reciente(seg, "e1", t0 + 60)
    alertas.registrar_lluvia_estacion(seg, "e1", 0.0, t0 + 120)        # medianoche: el contador vuelve a 0
    assert not alertas.lluvia_reciente(seg, "e1", t0 + 120)
    assert not alertas.lluvia_reciente(seg, "otra", t0)                # estación desconocida


if __name__ == "__main__":
    fallos = 0
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            try: f(); print("OK  ", n)
            except Exception as e: fallos += 1; print("FALLA", n, repr(e))
    sys.exit(1 if fallos else 0)
