import asyncio
import hmac
import html
import json
import os
import re
import sys
import threading
import time
import unicodedata
from datetime import datetime
from pathlib import Path
import requests
from . import seguridad
from . import config
from . import diagnostico
from .config import PORT

SUSCRIPTORES_FILE = config.DATA_DIR / "suscriptores.json"
COORDS_FILE = config.DATA_DIR / "coordenadas.json"

# ============================================================
# CONFIGURACIÓN (NADA secreto en el código)
#   AVAMET_TELEGRAM_TOKEN  token del bot (@BotFather), formato 123456789:AA...
#   AVAMET_TELEGRAM_CLAVE  contraseña de suscripción (mínimo 8 caracteres)
# Van en variables de entorno o en el fichero .env (ver .env.example).
# Si falta alguna, el bot NO arranca y lo avisa por consola.
# ============================================================
_RE_TOKEN = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{20,}$")
CLAVE_MIN = 8
MAX_FALLOS_CLAVE = 5          # intentos de contraseña fallidos por chat ...
VENTANA_CLAVE_S = 900         # ... en 15 min => bloqueo temporal

ESPERANDO_CLAVE = {}
_FALLOS_CLAVE: dict[str, list[float]] = {}


def _token() -> str:
    return os.getenv("AVAMET_TELEGRAM_TOKEN", "").strip()


def _admin_chat_id() -> str:
    """AVAMET_ADMIN_CHAT_ID: único chat que puede cambiar la ubicación con /pueblo. Vacío = nadie."""
    return os.getenv("AVAMET_ADMIN_CHAT_ID", "").strip()


def es_admin(chat_id) -> bool:
    """True solo si hay administrador configurado y es este chat (comparación exacta, como texto)."""
    admin = _admin_chat_id()
    return bool(admin) and str(chat_id) == admin


def _clave() -> str:
    return os.getenv("AVAMET_TELEGRAM_CLAVE", "").strip()


def configuracion_valida() -> tuple[bool, str]:
    """(True, "") si hay token con formato válido y contraseña; si no, (False, motivo)."""
    if not _token():
        return False, "falta AVAMET_TELEGRAM_TOKEN"
    if not _RE_TOKEN.match(_token()):
        return False, "AVAMET_TELEGRAM_TOKEN no tiene el formato de un token de Telegram (123456789:AA...)"
    if not _clave():
        return False, "falta AVAMET_TELEGRAM_CLAVE"
    if len(_clave()) < CLAVE_MIN:
        return False, f"AVAMET_TELEGRAM_CLAVE es demasiado corta (mínimo {CLAVE_MIN} caracteres)"
    return True, ""


async def _en_hilo_demonio(fn, *args, **kwargs):
    """Como asyncio.to_thread, pero en un hilo DEMONIO.

    asyncio.run() espera a los hilos de su executor al cerrar. La consulta larga de Telegram (getUpdates,
    hasta 25 s) los mantiene ocupados y, al parar la app (Ctrl+C o `systemctl stop`), el cierre se quedaba
    esperando y un segundo Ctrl+C mostraba un KeyboardInterrupt dentro de threading. Un hilo demonio no
    bloquea ni el cierre de asyncio ni la salida del proceso.
    """
    loop = asyncio.get_running_loop()
    fut = loop.create_future()

    def _poner(resultado=None, error=None):
        if fut.cancelled() or fut.done():
            return
        if error is not None: fut.set_exception(error)
        else: fut.set_result(resultado)

    def _trabajo():
        try:
            res, err = fn(*args, **kwargs), None
        except BaseException as e:           # noqa: BLE001 - se reenvía tal cual al que espera
            res, err = None, e
        try:
            loop.call_soon_threadsafe(_poner, res, err)
        except RuntimeError:                  # el bucle ya está cerrado (la app se está apagando)
            pass

    threading.Thread(target=_trabajo, name="telegram-http", daemon=True).start()
    return await fut


def _api() -> str:
    return f"https://api.telegram.org/bot{_token()}"


def _norm(texto: str) -> str:
    """Minúsculas y sin tildes: 'Castelló' == 'castello', 'Vila-real' == 'vila-real'."""
    return unicodedata.normalize("NFD", str(texto or "").lower()).encode("ascii", "ignore").decode("ascii")


def buscar_estaciones(coords: dict, busqueda: str) -> list:
    """[(id, info)] cuyo municipio o nombre contiene `busqueda` (sin tildes ni mayúsculas); las coincidencias
    EXACTAS de municipio van primero (así 'Vila' no elige antes una estación de nombre más largo)."""
    b = _norm(busqueda).strip()
    if not b: return []
    hallados = [(eid, info) for eid, info in coords.items()
                if b in _norm(info.get("municipio") or info.get("nombre", "")) or b in _norm(info.get("nombre", ""))]
    hallados.sort(key=lambda t: _norm(t[1].get("municipio") or t[1].get("nombre", "")) != b)
    return hallados


def _limpiar(texto: str) -> str:
    """Quita el token de cualquier texto que vaya a un log (los errores de requests incluyen la URL)."""
    t = _token()
    return texto.replace(t, "***") if t else texto


def comprobar_clave(chat_id, texto: str, ahora: float | None = None) -> str:
    """"OK", "MAL" o "BLOQUEADO" (demasiados fallos recientes de ese chat). Comparación en tiempo constante."""
    ahora = time.time() if ahora is None else ahora
    cid = str(chat_id)
    fallos = [t for t in _FALLOS_CLAVE.get(cid, []) if ahora - t < VENTANA_CLAVE_S]
    if len(fallos) >= MAX_FALLOS_CLAVE:
        _FALLOS_CLAVE[cid] = fallos
        return "BLOQUEADO"
    clave = _clave()
    if clave and hmac.compare_digest(texto.encode("utf-8"), clave.encode("utf-8")):
        _FALLOS_CLAVE.pop(cid, None)
        return "OK"
    fallos.append(ahora)
    _FALLOS_CLAVE[cid] = fallos
    return "MAL"


def cargar_suscriptores() -> dict:
    if not SUSCRIPTORES_FILE.exists(): return {}
    try:
        with open(SUSCRIPTORES_FILE, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return {}

def _guardar_suscriptores(subs: dict) -> None:
    tmp_file = SUSCRIPTORES_FILE.with_suffix(".tmp")
    with open(tmp_file, "w", encoding="utf-8") as f: json.dump(subs, f, ensure_ascii=False, indent=1)
    os.replace(tmp_file, SUSCRIPTORES_FILE)

def enviar_mensaje(chat_id, texto: str) -> bool:
    if not configuracion_valida()[0]: return False
    try:
        r = requests.post(f"{_api()}/sendMessage", data={"chat_id": chat_id, "text": texto, "parse_mode": "HTML"}, timeout=10)
        if not r.ok:
            diagnostico.registrar("TELEGRAM", f"HTTP_{r.status_code}", f"sendMessage rechazado (chat {chat_id})")
        return r.ok
    except requests.RequestException as e:
        diagnostico.registrar_excepcion("TELEGRAM", e, "sendMessage")
        return False

def enviar_a_todos_detalle(texto: str) -> tuple[int, int]:
    """(enviados, destinatarios). (0, 0) si el bot está desactivado o no hay suscriptores."""
    if not configuracion_valida()[0]:
        return 0, 0
    subs = cargar_suscriptores()
    enviados = 0
    for cid in subs:
        if enviar_mensaje(cid, texto): enviados += 1
    return enviados, len(subs)

def enviar_a_todos(texto: str) -> int:
    return enviar_a_todos_detalle(texto)[0]

async def bucle_suscripciones():
    ok, motivo = configuracion_valida()
    if not ok:
        print(f"⚠️  Telegram desactivado: {motivo}. El bot no arranca (configúralo en .env o en las variables de entorno).")
        return

    offset = 0
    errores = 0
    print("🤖 Bot de Telegram escuchando (Protegido con contraseña)...")

    while True:
        try:
            r = await _en_hilo_demonio(requests.get, f"{_api()}/getUpdates", params={"offset": offset, "timeout": 20}, timeout=25)
            data = r.json()
            if not data.get("ok"):
                diagnostico.registrar("TELEGRAM", f"API_{data.get('error_code', '?')}", _limpiar(str(data.get("description"))))
                if data.get("error_code") in (401, 404):
                    print("⛔ Telegram rechaza el token (¿revocado o mal copiado?). Bot detenido hasta reiniciar la app.")
                    return
                errores += 1
                if errores == 1 or errores % 10 == 0:
                    print(f"Telegram: respuesta no válida ({_limpiar(str(data.get('description')))}); reintentando")
                await asyncio.sleep(30)
                continue
            errores = 0
            if data.get("ok"):
                for upd in data.get("result", []):
                    offset = upd["update_id"] + 1
                    msg = upd.get("message") or upd.get("edited_message") or {}
                    texto = (msg.get("text") or "").strip()
                    chat = msg.get("chat") or {}
                    chat_id = chat.get("id")
                    nombre = html.escape(chat.get("first_name") or chat.get("title") or str(chat_id))
                    
                    if not chat_id or not texto: continue
                    cmd = texto.split()[0].lower() if texto else ""

                    if str(chat_id) in ESPERANDO_CLAVE:
                        res = comprobar_clave(chat_id, texto)
                        if res == "BLOQUEADO":
                            del ESPERANDO_CLAVE[str(chat_id)]
                            enviar_mensaje(chat_id, "⛔ Demasiados intentos fallidos. Inténtalo de nuevo en unos minutos.")
                        elif res == "OK":
                            subs = cargar_suscriptores()
                            subs[str(chat_id)] = {"nombre": html.unescape(nombre), "desde": datetime.now().isoformat(timespec="seconds")}
                            _guardar_suscriptores(subs)
                            enviar_mensaje(chat_id, f"✅ Contraseña correcta. Bienvenido {nombre}.\n\n<b>Comandos:</b>\n/pueblo &lt;nombre&gt;\n/status\n/stop")
                            del ESPERANDO_CLAVE[str(chat_id)]
                        else:
                            enviar_mensaje(chat_id, "❌ Contraseña incorrecta. Inténtalo de nuevo o envía /start.")
                        continue 

                    if cmd == "/start":
                        subs = cargar_suscriptores()
                        if str(chat_id) in subs:
                            enviar_mensaje(chat_id, f"✅ Ya estás suscrito, {nombre}.\n\n<b>Comandos:</b>\n/pueblo &lt;nombre&gt;\n/status\n/stop")
                        else:
                            ESPERANDO_CLAVE[str(chat_id)] = True
                            enviar_mensaje(chat_id, "🔒 <b>Acceso restringido</b>\n\nPara suscribirte a las alertas de AVAMET, por favor, escribe la contraseña secreta del observatorio:")
                    elif cmd == "/stop":
                        subs = cargar_suscriptores()
                        if str(chat_id) in subs:
                            del subs[str(chat_id)]
                            _guardar_suscriptores(subs)
                            enviar_mensaje(chat_id, "🔕 Ya no recibirás más alertas.")
                    elif cmd == "/id":
                        enviar_mensaje(chat_id, f"Tu chat ID es: <code>{chat_id}</code>")
                    elif cmd == "/status":
                        subs = cargar_suscriptores()
                        if str(chat_id) in subs: enviar_mensaje(chat_id, f"✅ Estás suscrito desde {subs[str(chat_id)].get('desde', '?')}.")
                        else: enviar_mensaje(chat_id, "❌ No estás suscrito. Usa /start.")
                    elif cmd in ["/pueblo", "/lugar"]:
                        subs = cargar_suscriptores()
                        if str(chat_id) not in subs:
                            enviar_mensaje(chat_id, "⛔ Primero debes identificarte con /start.")
                            continue
                        if not es_admin(chat_id):
                            # Cambiar la estación cambia la seguridad para TODOS (y para N.I.N.A.): solo el administrador.
                            msg = "⛔ No tienes permisos para cambiar la ubicación."
                            if not _admin_chat_id():
                                msg += " (No hay administrador configurado: AVAMET_ADMIN_CHAT_ID.)"
                            enviar_mensaje(chat_id, msg)
                            continue
                        partes = texto.split(maxsplit=1)
                        if len(partes) < 2:
                            enviar_mensaje(chat_id, "Uso: /pueblo &lt;nombre&gt;")
                            continue
                        busqueda = partes[1].strip()
                        if not COORDS_FILE.exists():
                            enviar_mensaje(chat_id, "⏳ El servidor aún está cargando estaciones.")
                            continue
                        try:
                            with open(COORDS_FILE, "r", encoding="utf-8") as f: coords = json.load(f)
                        except Exception:
                            enviar_mensaje(chat_id, "⚠️ Error leyendo base de datos.")
                            continue
                        coincidencias = buscar_estaciones(coords, busqueda)
                        if not coincidencias:
                            enviar_mensaje(chat_id, f"❌ No he encontrado '{html.escape(busqueda)}'.")
                        else:
                            eid_elegido, info_elegida = coincidencias[0]
                            try:
                                r_post = await _en_hilo_demonio(requests.post, f"http://127.0.0.1:{PORT}/api/ubicacion", json={"estacion_id": eid_elegido},
                                                 headers={"Authorization": f"Bearer {seguridad.obtener_token()}"}, timeout=5)
                                if r_post.ok: 
                                    mun = html.escape(info_elegida.get("municipio") or info_elegida.get("nombre", "Desconocido"))
                                    enviar_mensaje(chat_id, f"✅ Ubicación cambiada a: <b>{mun}</b>.")
                                else: 
                                    enviar_mensaje(chat_id, f"⚠️ Error HTTP {r_post.status_code}.")
                            except Exception as e: 
                                enviar_mensaje(chat_id, f"⚠️ Error de conexión: {html.escape(_limpiar(str(e)))}")
                    else:
                        enviar_mensaje(chat_id, "Comandos: /pueblo (solo administrador), /status, /stop, /id")
        except asyncio.CancelledError: return
        except Exception as e:
            errores += 1
            diagnostico.registrar_excepcion("TELEGRAM", e, "getUpdates")
            if errores == 1 or errores % 10 == 0:         # sin internet no se llena el log; y sin filtrar el token
                print(f"Error Telegram: {_limpiar(str(e))} (reintentando con espera creciente)")
            await asyncio.sleep(min(60, 3 * 2 ** min(errores, 5)))
            continue
        await asyncio.sleep(3)