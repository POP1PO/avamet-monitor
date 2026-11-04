"""Arranque de AVAMET Monitor.

  Escritorio (Windows / .exe):   python run.py            -> abre el navegador en http://127.0.0.1:8000
  Headless (Raspberry Pi):       python run.py --headless -> sin navegador, escucha en 0.0.0.0 (toda la red local)

Opciones (también por variable de entorno; la línea de comandos manda sobre el entorno):
  --headless          AVAMET_HEADLESS=1   no abre navegador y escucha en 0.0.0.0 salvo que indiques otra --host
  --host DIRECCIÓN    AVAMET_HOST         por defecto 127.0.0.1 (0.0.0.0 con --headless)
  --port PUERTO       AVAMET_PORT         por defecto 8000
  --data-dir CARPETA  AVAMET_DATA_DIR     por defecto ./data/ en Linux (ver backend/config.py)

Si no se indica --headless, en Linux sin entorno gráfico (sin DISPLAY ni WAYLAND_DISPLAY) se asume headless.
"""
import argparse
import os
import sys
import time
import socket
import threading
import traceback
from pathlib import Path

VERSION = "1.0.0"


def _es_si(valor: str) -> bool:
    return valor.strip().lower() in ("1", "true", "yes", "si", "sí", "on")


def _leer_argumentos(argv=None):
    ap = argparse.ArgumentParser(description=f"AVAMET Monitor v{VERSION}")
    ap.add_argument("--headless", action="store_true", help="sin navegador; escucha en 0.0.0.0")
    ap.add_argument("--host", help="dirección de escucha")
    ap.add_argument("--port", type=int, help="puerto")
    ap.add_argument("--data-dir", help="carpeta de datos")
    return ap.parse_args(argv)


def _es_headless(args) -> bool:
    if args.headless or _es_si(os.getenv("AVAMET_HEADLESS", "")):
        return True
    # Linux sin escritorio (p. ej. servicio systemd o SSH): no hay navegador que abrir.
    return sys.platform.startswith("linux") and not (os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY"))


# La configuración se fija ANTES de importar backend.config (que lee estas variables al importarse).
ARGS = _leer_argumentos() if __name__ == "__main__" else _leer_argumentos([])
HEADLESS = _es_headless(ARGS)
if __name__ == "__main__":
    # Se lo comunica a backend.config (la interfaz oculta la pestaña «Configuración» en modo headless).
    os.environ["AVAMET_HEADLESS"] = "1" if HEADLESS else "0"
if ARGS.data_dir: os.environ["AVAMET_DATA_DIR"] = ARGS.data_dir
if ARGS.port:     os.environ["AVAMET_PORT"] = str(ARGS.port)
if ARGS.host:     os.environ["AVAMET_HOST"] = ARGS.host
elif HEADLESS and not os.getenv("AVAMET_HOST", "").strip():
    os.environ["AVAMET_HOST"] = "0.0.0.0"

if getattr(sys, 'frozen', False):
    sys.path.insert(0, sys._MEIPASS)
    # Misma carpeta que el resto de datos (config.carpeta_datos): respeta AVAMET_DATA_DIR y no falla si APPDATA no
    # existe (M9: antes Path(None) -> TypeError fuera de Windows).
    from backend import config as _config
    APP_DATA = _config.DATA_DIR
    APP_DATA.mkdir(parents=True, exist_ok=True)
    LOG_FILE = APP_DATA / "error_log.txt"
    from backend.registro import FicheroRotativo
    sys.stdout = sys.stderr = FicheroRotativo(LOG_FILE)       # línea a línea y con rotación (1 MB x 3 copias)
elif HEADLESS:
    # La salida va a la consola / al journal de systemd: que cada línea salga al momento.
    for _f in (sys.stdout, sys.stderr):
        try: _f.reconfigure(line_buffering=True)
        except Exception: pass

import uvicorn
from backend.config import HOST, PORT
from backend.main import app


from backend.instancia import host_de_prueba as _host_de_prueba, es_instancia_avamet


def puerto_ocupado(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(2)
        return s.connect_ex((_host_de_prueba(host), port)) == 0


def esperar_y_abrir_navegador():
    import requests, webbrowser
    url = f"http://{_host_de_prueba(HOST)}:{PORT}"
    for _ in range(120):
        try:
            requests.get(url, timeout=1)
            break
        except requests.ConnectionError:
            time.sleep(0.5)
    webbrowser.open(url)


def _terminar(codigo: int):
    """Sale sin esperar a los hilos de trabajo.

    Hay hilos del pool de asyncio (asyncio.to_thread) bloqueados en peticiones de red, p. ej. la consulta
    larga de Telegram (hasta 25 s). Python los espera al cerrar y, si pulsas Ctrl+C otra vez, imprime un
    KeyboardInterrupt dentro de threading._shutdown. Cuando uvicorn ya ha cerrado (lifespan incluido) no
    queda nada que guardar, así que se sale directamente.
    """
    from backend.apagado import terminar_ya
    terminar_ya(codigo)


if __name__ == "__main__":
    if puerto_ocupado(HOST, PORT):
        if not es_instancia_avamet(HOST, PORT):
            # M9: el puerto lo usa OTRA aplicación; no se abre el navegador sobre ella.
            print(f"[{time.strftime('%H:%M:%S')}] ERROR: el puerto {PORT} está ocupado por otra aplicación (no es AVAMET Monitor). "
                  f"Ciérrala o usa otro puerto: --port 8001 / AVAMET_PORT.", file=sys.stderr)
            _terminar(1)
        if HEADLESS:
            print(f"[{time.strftime('%H:%M:%S')}] ERROR: AVAMET Monitor ya está en marcha en el puerto {PORT}.", file=sys.stderr)
            _terminar(1)
        import webbrowser
        print(f"\n[{time.strftime('%H:%M:%S')}] La aplicación ya está en ejecución. Abriendo el navegador...")
        webbrowser.open(f"http://{_host_de_prueba(HOST)}:{PORT}")
        time.sleep(2)
        sys.exit(0)

    print(f"\n--- INICIANDO AVAMET MONITOR v{VERSION} {time.strftime('%Y-%m-%d %H:%M:%S')} ---")
    from backend import config
    print(f"Modo: {'HEADLESS' if HEADLESS else 'escritorio'} | escuchando en http://{HOST}:{PORT} | datos en: {config.DATA_DIR}\n")
    if HOST not in ("127.0.0.1", "localhost", "::1"):
        print("Accesible desde la red local. /api/shutdown y /api/ubicacion exigen el token de api_token.txt; "
              "el resto de endpoints (incluido Alpaca) NO tienen autenticación: no expongas el puerto a internet.\n")

    codigo = 0
    try:
        if not HEADLESS:
            threading.Thread(target=esperar_y_abrir_navegador, daemon=True).start()
        # uvicorn gestiona Ctrl+C y SIGTERM (systemctl stop): cierra, ejecuta el lifespan y run() retorna.
        uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, reload=False, log_config=None)).run()
    except KeyboardInterrupt:
        pass                                  # segundo Ctrl+C durante el cierre: salida limpia, sin traza
    except SystemExit as e:
        # uvicorn termina con sys.exit(1) si no puede enlazar el puerto (p. ej. otra instancia arrancó justo entre
        # nuestra comprobación y el enlace): salida con código 1 y mensaje claro, sin colgarse.
        codigo = e.code if isinstance(e.code, int) else 1
        if codigo: print(f"[{time.strftime('%H:%M:%S')}] uvicorn terminó con código {codigo} (¿puerto {PORT} ocupado?).", file=sys.stderr)
    except Exception:
        codigo = 1
        if getattr(sys, 'frozen', False):
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"\n--- ERROR FATAL {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
                f.write(traceback.format_exc())
        else:
            traceback.print_exc()
    print(f"[{time.strftime('%H:%M:%S')}] AVAMET Monitor detenido.")
    _terminar(codigo)
