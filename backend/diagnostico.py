"""Diagnóstico de red y registro de la última hora (diagnostico.log).

Para qué sirve: cuando se cae la conexión, saber POR QUÉ. Hay tres piezas:

1. Registro de la última hora (`registrar`): una línea por evento, «fecha | componente | tipo | detalle». El fichero solo conserva
   los eventos de los últimos `config.DIAG_VENTANA_S` segundos (1 h): cada minuto, como mucho, se reescribe sin las líneas viejas.
   Los fallos repetidos del mismo componente y tipo se agrupan (si no, 20 estaciones sin red llenarían el log en un ciclo).
   Cada línea se copia además a un fichero por día (`config.DIAG_HISTORICO_DIR/diagnostico_AAAA-MM-DD.log`) que se conserva
   `config.DIAG_HISTORICO_DIAS` días: así queda constancia de lo que pasó de madrugada aunque nadie descargue el log a tiempo.
2. Sondas (`comprobar_internet`, `comprobar_avamet`, `comprobar_aeropuertos`): cada una responde si la pieza funciona.
   - Internet general: conexión TCP a 1.1.1.1 y 8.8.8.8 (puerto 443, por IP, sin DNS). Es lo más parecido a un «ping» sin
     necesitar permisos de administrador (el ping ICMP real los pide en Linux y su salida cambia según el sistema).
   - AVAMET y aeropuertos: petición HEAD ligera (si el servidor contesta ≥ 500 a HEAD, se repite una vez con GET).
3. Diagnóstico (`diagnosticar`): cuando algo falla, decide la causa más probable:
   sin Internet  ->  «❌ Sin conexión a Internet en el observatorio»
   Internet sí, web no  ->  «⚠️ La web de AVAMET no responde (posible caída del servidor)» / «⚠️ La API de meteorología ... »
   Internet sí, pero el nombre no se resuelve (DNS)  ->  mensaje propio, porque «caída del servidor» sería engañoso.

El estado de red (`estado_red`) se va actualizando solo con cada ciclo correcto o fallido y lo muestra la pestaña ⚙️.
Ninguna función de este módulo lanza excepciones hacia fuera: diagnosticar no debe romper el bucle principal.
"""
import json
import os
import re
import socket
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

from . import config

COMPONENTES_VALIDOS = ("AVAMET", "METAR", "TELEGRAM", "RED", "BUCLE", "ARRANQUE")

URL_AVAMET = os.getenv("AVAMET_DIAG_URL_AVAMET", "https://www.avamet.org/")
URL_AEROPUERTOS = os.getenv("AVAMET_DIAG_URL_METAR", "https://aviationweather.gov/api/data/metar?ids=LEVC&format=json")
SONDAS_INTERNET = (("1.1.1.1", 443), ("8.8.8.8", 443))
_UA = {"User-Agent": "AVAMET-Monitor/1.0 (diagnostico de red)"}

MSG_SIN_INTERNET = "❌ Sin conexión a Internet en el observatorio"
MSG_AVAMET_CAIDA = "⚠️ La web de AVAMET no responde (posible caída del servidor)"
MSG_METAR_CAIDA = "⚠️ La API de meteorología de aeropuertos no responde"

DEDUP_S = 30          # el mismo (componente, tipo) no se vuelve a escribir antes de estos segundos (se cuenta cuántos se omiten)
PODA_CADA_S = 60      # como mucho una reescritura del fichero por minuto
CACHE_DIAG_S = 20     # un diagnóstico del mismo objetivo se reutiliza durante estos segundos
MAX_DETALLE = 300

_RE_LINEA = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| ")
_RE_TOKEN = re.compile(r"(?:bot)?\d{5,}:[A-Za-z0-9_-]{20,}")     # token de Telegram: nunca debe llegar al log


# ----------------------------------------------------------------------------------------------------------------------
# Limpieza y clasificación de errores
# ----------------------------------------------------------------------------------------------------------------------
def _limpiar(texto) -> str:
    """Una sola línea, sin tokens de Telegram, sin el separador «|» y recortada."""
    t = _RE_TOKEN.sub("***", str(texto))
    t = re.sub(r"\s+", " ", t).replace("|", "/").strip()
    return t[:MAX_DETALLE]


def clasificar_error(exc: BaseException) -> tuple:
    """(tipo, detalle) de una excepción de red. Tipos: TIMEOUT, DNS, CONEXION_RECHAZADA, RED_INACCESIBLE,
    CONEXION_CORTADA, SSL, SIN_CONEXION, HTTP_<codigo>, RESPUESTA_INVALIDA, o el nombre de la excepción."""
    detalle = _limpiar(f"{type(exc).__name__}: {exc}")
    texto = str(exc).lower()
    if isinstance(exc, requests.exceptions.Timeout) or isinstance(exc, socket.timeout) or isinstance(exc, TimeoutError):
        return "TIMEOUT", detalle
    if isinstance(exc, requests.exceptions.SSLError):
        return "SSL", detalle
    if isinstance(exc, requests.exceptions.HTTPError):
        codigo = getattr(getattr(exc, "response", None), "status_code", None)
        return (f"HTTP_{codigo}" if codigo else "HTTP"), detalle
    if isinstance(exc, (requests.exceptions.ConnectionError, OSError)):
        if isinstance(exc, socket.gaierror) or any(s in texto for s in (
                "name or service not known", "getaddrinfo", "temporary failure in name resolution", "nodename nor servname",
                "no address associated", "11001", "11002", "name resolution")):
            return "DNS", detalle
        if "refused" in texto or "10061" in texto:
            return "CONEXION_RECHAZADA", detalle
        if any(s in texto for s in ("unreachable", "10051", "10065", "no route to host")):
            return "RED_INACCESIBLE", detalle
        if "reset" in texto or "aborted" in texto or "remote end closed" in texto or "10054" in texto:
            return "CONEXION_CORTADA", detalle
        return "SIN_CONEXION", detalle
    if isinstance(exc, (ValueError, json.JSONDecodeError)):
        return "RESPUESTA_INVALIDA", detalle
    return type(exc).__name__, detalle


# ----------------------------------------------------------------------------------------------------------------------
# Registro de la última hora
# ----------------------------------------------------------------------------------------------------------------------
_RE_HISTORICO = re.compile(r"^diagnostico_(\d{4}-\d{2}-\d{2})\.log$")
LIMPIEZA_HISTORICO_S = 3600          # como mucho una limpieza de ficheros viejos por hora


class RegistroHora:
    """Fichero de texto que solo guarda los eventos de la última `ventana_s`. Seguro entre hilos; nunca lanza excepciones.
    Con `dir_historico` y `dias_historico` > 0, cada línea se copia también a `dir_historico/diagnostico_AAAA-MM-DD.log`
    (fecha local del evento) y se borran los ficheros de más de `dias_historico` días."""

    def __init__(self, ruta, ventana_s: int = 3600, dir_historico=None, dias_historico: int = 0):
        self.ruta = Path(ruta)
        self.ventana_s = ventana_s
        self.dir_historico = Path(dir_historico) if dir_historico else None
        self.dias_historico = max(0, int(dias_historico or 0))
        self._ultima_limpieza = 0.0
        self._cerrojo = threading.Lock()
        self._ultima_poda = 0.0
        self._recientes: dict = {}          # (componente, tipo) -> [instante de la última línea escrita, omitidos desde entonces]

    def _historico(self, linea: str, ahora: float) -> None:
        """Copia la línea al fichero del día y, como mucho una vez por hora, borra los de más de `dias_historico` días.
        Con el cerrojo ya tomado. Un fallo aquí no impide el registro de la última hora."""
        if not self.dir_historico or self.dias_historico <= 0:
            return
        try:
            self.dir_historico.mkdir(parents=True, exist_ok=True)
            dia = datetime.fromtimestamp(ahora).strftime("%Y-%m-%d")
            with open(self.dir_historico / f"diagnostico_{dia}.log", "a", encoding="utf-8") as f:
                f.write(linea)
            if ahora - self._ultima_limpieza >= LIMPIEZA_HISTORICO_S:
                self._ultima_limpieza = ahora
                limite = (datetime.fromtimestamp(ahora) - timedelta(days=self.dias_historico)).strftime("%Y-%m-%d")
                for p in self.dir_historico.iterdir():
                    m = _RE_HISTORICO.match(p.name)
                    if m and m.group(1) < limite and p.is_file():       # solo ficheros con este nombre exacto
                        p.unlink()
        except OSError:
            pass

    def ficheros_historico(self) -> list:
        """Nombres de los ficheros diarios que hay, del más reciente al más antiguo."""
        try:
            if not self.dir_historico or not self.dir_historico.is_dir():
                return []
            return sorted((p.name for p in self.dir_historico.iterdir() if _RE_HISTORICO.match(p.name)), reverse=True)
        except OSError:
            return []

    @staticmethod
    def _fmt(ts: float) -> str:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _ts(linea: str):
        m = _RE_LINEA.match(linea)
        if not m:
            return None
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            return None

    def _podar(self, ahora: float) -> None:
        """Reescribe el fichero sin las líneas de más de `ventana_s` (escritura atómica). Con el cerrojo ya tomado."""
        self._ultima_poda = ahora
        try:
            if not self.ruta.is_file():
                return
            lineas = self.ruta.read_text(encoding="utf-8", errors="replace").splitlines()
            limite = ahora - self.ventana_s
            quedan = [ln for ln in lineas if (t := self._ts(ln)) is not None and t >= limite]
            if len(quedan) == len(lineas):
                return
            tmp = self.ruta.with_name(self.ruta.name + ".tmp")
            tmp.write_text("".join(ln + "\n" for ln in quedan), encoding="utf-8")
            os.replace(tmp, self.ruta)
        except OSError:
            pass

    def registrar(self, componente: str, tipo: str, detalle: str = "", ahora: float | None = None) -> bool:
        """Añade una línea. Devuelve False si se omitió por repetida (se contará en la siguiente línea que sí se escriba)."""
        ahora = time.time() if ahora is None else ahora
        clave = (str(componente).upper(), str(tipo))
        with self._cerrojo:
            previo = self._recientes.get(clave)
            if previo and ahora - previo[0] < DEDUP_S:
                previo[1] += 1
                return False
            omitidos = previo[1] if previo else 0
            self._recientes[clave] = [ahora, 0]
            if len(self._recientes) > 200:                       # tabla acotada
                for k in [k for k, v in self._recientes.items() if ahora - v[0] > self.ventana_s]:
                    self._recientes.pop(k, None)
            texto = _limpiar(detalle)
            if omitidos:
                texto = (texto + f" [+{omitidos} igual(es) omitido(s)]").strip()
            linea = f"{self._fmt(ahora)} | {clave[0]} | {clave[1]} | {texto}\n"
            try:
                self.ruta.parent.mkdir(parents=True, exist_ok=True)
                with open(self.ruta, "a", encoding="utf-8") as f:
                    f.write(linea)
            except OSError:
                return False
            self._historico(linea, ahora)
            if ahora - self._ultima_poda >= PODA_CADA_S:
                self._podar(ahora)
            return True

    def leer(self, ahora: float | None = None) -> str:
        """Contenido de la última hora (poda antes de leer)."""
        ahora = time.time() if ahora is None else ahora
        with self._cerrojo:
            self._podar(ahora)
            try:
                return self.ruta.read_text(encoding="utf-8", errors="replace") if self.ruta.is_file() else ""
            except OSError:
                return ""


_REGISTRO: RegistroHora | None = None
_CERROJO_REG = threading.Lock()


def registro() -> RegistroHora:
    """Registro global, creado la primera vez (la ruta sale de config.DIAG_LOG_PATH)."""
    global _REGISTRO
    with _CERROJO_REG:
        if _REGISTRO is None:
            _REGISTRO = RegistroHora(config.DIAG_LOG_PATH, config.DIAG_VENTANA_S,
                                     config.DIAG_HISTORICO_DIR, config.DIAG_HISTORICO_DIAS)
        return _REGISTRO


def registrar(componente: str, tipo: str, detalle: str = "") -> bool:
    try:
        return registro().registrar(componente, tipo, detalle)
    except Exception:                        # noqa: BLE001 - registrar nunca debe tumbar la aplicación
        return False


def registrar_excepcion(componente: str, exc: BaseException, contexto: str = "") -> tuple:
    """Clasifica `exc`, la registra y devuelve (tipo, detalle)."""
    tipo, detalle = clasificar_error(exc)
    registrar(componente, tipo, (contexto + ": " if contexto else "") + detalle)
    return tipo, detalle


def texto_descarga(version: str = "") -> str:
    """Lo que recibe el usuario al pulsar «Descargar log»: cabecera + estado de red + eventos de la última hora."""
    ahora = time.time()
    try:
        cuerpo = registro().leer(ahora)
    except Exception:                        # noqa: BLE001
        cuerpo = ""
    red = estado_red()
    def _v(x): return {True: "OK", False: "FALLA", None: "sin comprobar"}[x]
    cab = [
        f"# AVAMET Monitor {version} - log de diagnostico (ultimos {config.DIAG_VENTANA_S // 60} min)",
        f"# Generado: {RegistroHora._fmt(ahora)} (hora de este equipo)",
        f"# Estado de red ahora: Internet={_v(red['internet'])} | AVAMET={_v(red['avamet'])} | Aeropuertos={_v(red['aeropuertos'])}",
        "# Formato: fecha y hora | componente | tipo de error | detalle",
    ]
    if config.DIAG_HISTORICO_DIAS > 0:
        cab.append(f"# Historico por dias ({config.DIAG_HISTORICO_DIAS} dias): carpeta {config.DIAG_HISTORICO_DIR}")
    cab.append("")
    return "\n".join(cab) + "\n" + (cuerpo if cuerpo.strip() else "(sin eventos en la ultima hora)\n")


# ----------------------------------------------------------------------------------------------------------------------
# Sondas
# ----------------------------------------------------------------------------------------------------------------------
def comprobar_internet(timeout: float = 2.5) -> dict:
    """¿Hay Internet en general? Conexión TCP por IP (sin DNS) a 1.1.1.1:443 y, si falla, a 8.8.8.8:443."""
    ultimo = ("SIN_CONEXION", "")
    t0 = time.monotonic()
    for host, puerto in SONDAS_INTERNET:
        try:
            with socket.create_connection((host, puerto), timeout=timeout):
                return {"ok": True, "ms": int((time.monotonic() - t0) * 1000), "tipo": "", "detalle": f"{host}:{puerto}"}
        except Exception as e:               # noqa: BLE001
            ultimo = clasificar_error(e)
    return {"ok": False, "ms": int((time.monotonic() - t0) * 1000), "tipo": ultimo[0], "detalle": ultimo[1]}


def _sonda_http(url: str, timeout=(4, 6)) -> dict:
    """HEAD ligero. Responde = cualquier contestación del servidor por debajo de 500 (incluso 403/404/405: la web está viva)."""
    t0 = time.monotonic()
    try:
        r = requests.head(url, headers=_UA, timeout=timeout, allow_redirects=True)
        codigo = r.status_code
        if codigo >= 500 and codigo != 501:           # algunos servidores fallan solo con HEAD: se confirma con GET
            r = requests.get(url, headers=_UA, timeout=timeout, stream=True)
            codigo = r.status_code
            r.close()
        ok = codigo < 500 or codigo == 501
        return {"ok": ok, "ms": int((time.monotonic() - t0) * 1000), "http": codigo,
                "tipo": "" if ok else f"HTTP_{codigo}", "detalle": f"HTTP {codigo}"}
    except Exception as e:                   # noqa: BLE001
        tipo, detalle = clasificar_error(e)
        return {"ok": False, "ms": int((time.monotonic() - t0) * 1000), "http": None, "tipo": tipo, "detalle": detalle}


def comprobar_avamet() -> dict:
    return _sonda_http(URL_AVAMET)


def comprobar_aeropuertos() -> dict:
    return _sonda_http(URL_AEROPUERTOS)


# ----------------------------------------------------------------------------------------------------------------------
# Estado de red (lo que enseña la pestaña ⚙️)
# ----------------------------------------------------------------------------------------------------------------------
_COMPONENTES_RED = {"AVAMET": "avamet", "METAR": "aeropuertos"}
_RED = {"internet": None, "avamet": None, "aeropuertos": None, "actualizado": None,
        "motivos": {"internet": "", "avamet": "", "aeropuertos": ""}}
_CERROJO_RED = threading.Lock()


def estado_red() -> dict:
    """Copia del estado actual: True = funciona, False = falla, None = aún sin comprobar."""
    with _CERROJO_RED:
        return {**_RED, "motivos": dict(_RED["motivos"])}


def marcar(que: str, ok: bool, motivo: str = "", ahora: float | None = None) -> None:
    """Actualiza una pieza ('internet', 'avamet' o 'aeropuertos'). Un acierto de AVAMET o de aeropuertos implica que hay Internet.
    Al pasar de FALLA a OK se deja constancia en el log («RECUPERADO»), para ver cuánto duró la caída."""
    ahora = time.time() if ahora is None else ahora
    recuperadas = []
    with _CERROJO_RED:
        if que not in ("internet", "avamet", "aeropuertos"):
            return
        implica = ["internet"] if (ok and que != "internet") else []
        for k in [que] + implica:
            if ok and _RED[k] is False:
                recuperadas.append(k)
            _RED[k] = ok
            _RED["motivos"][k] = "" if ok else motivo
        _RED["actualizado"] = datetime.fromtimestamp(ahora).isoformat(timespec="seconds")
    nombres = {"internet": "RED", "avamet": "AVAMET", "aeropuertos": "METAR"}
    for k in recuperadas:
        registrar(nombres[k], "RECUPERADO", "vuelve a responder")


def _reiniciar_estado_red() -> None:       # para las pruebas
    with _CERROJO_RED:
        _RED.update({"internet": None, "avamet": None, "aeropuertos": None, "actualizado": None,
                     "motivos": {"internet": "", "avamet": "", "aeropuertos": ""}})
    _CACHE_DIAG.clear()


# ----------------------------------------------------------------------------------------------------------------------
# Diagnóstico
# ----------------------------------------------------------------------------------------------------------------------
_CACHE_DIAG: dict = {}      # objetivo -> (instante, resultado)
_CERROJO_DIAG = threading.Lock()


def diagnosticar(objetivo: str, tipo_error: str = "", detalle: str = "", usar_cache: bool = True,
                 ahora: float | None = None) -> dict:
    """Averigua por qué falló `objetivo` ('avamet' o 'metar') y lo deja en el log y en el estado de red.

    `tipo_error`/`detalle`: lo que ya se sabe del fallo original (p. ej. 'TIMEOUT'); sirve para afinar el mensaje.
    Devuelve {"causa", "mensaje", "internet", "web", "tipo_error"}, con causa en SIN_INTERNET | WEB_CAIDA | DNS | RESPONDE.
    """
    ahora = time.time() if ahora is None else ahora
    objetivo = "metar" if str(objetivo).lower() in ("metar", "aeropuertos") else "avamet"
    with _CERROJO_DIAG:
        previo = _CACHE_DIAG.get(objetivo)
        if usar_cache and previo and ahora - previo[0] < CACHE_DIAG_S:
            return previo[1]
        try:
            res = _diagnosticar(objetivo, tipo_error, detalle, ahora)
        except Exception as e:               # noqa: BLE001 - el diagnóstico nunca debe romper el bucle
            res = {"causa": "DESCONOCIDA", "mensaje": f"⚠️ No se pudo diagnosticar la red ({type(e).__name__})",
                   "internet": None, "web": None, "tipo_error": tipo_error}
        _CACHE_DIAG[objetivo] = (ahora, res)
        return res


def _diagnosticar(objetivo: str, tipo_error: str, detalle: str, ahora: float) -> dict:
    comp = "AVAMET" if objetivo == "avamet" else "METAR"
    clave = "avamet" if objetivo == "avamet" else "aeropuertos"
    nombre_web = "www.avamet.org" if objetivo == "avamet" else "aviationweather.gov"
    caida = MSG_AVAMET_CAIDA if objetivo == "avamet" else MSG_METAR_CAIDA

    net = comprobar_internet()
    if not net["ok"]:
        marcar("internet", False, MSG_SIN_INTERNET, ahora)
        registrar(comp, "SIN_INTERNET", f"{MSG_SIN_INTERNET} (sondas 1.1.1.1/8.8.8.8: {net['tipo']}; fallo original: {tipo_error or '?'})")
        return {"causa": "SIN_INTERNET", "mensaje": MSG_SIN_INTERNET, "internet": False, "web": None, "tipo_error": tipo_error}

    marcar("internet", True, "", ahora)
    web = comprobar_avamet() if objetivo == "avamet" else comprobar_aeropuertos()
    if web["ok"]:
        # El servidor contesta a una petición ligera, así que el fallo fue de esa lectura concreta (tiempo, formato, datos).
        marcar(clave, True, "", ahora)
        msg = f"⚠️ {nombre_web} responde, pero la lectura falló ({tipo_error or 'error desconocido'})"
        registrar(comp, "RESPONDE_PERO_FALLO", f"{msg}. {detalle}")
        return {"causa": "RESPONDE", "mensaje": msg, "internet": True, "web": True, "tipo_error": tipo_error}

    if web["tipo"] == "DNS":
        msg = f"⚠️ Hay Internet, pero no se resuelve el nombre de {nombre_web} (fallo de DNS)"
        causa = "DNS"
    else:
        msg, causa = caida, "WEB_CAIDA"
    marcar(clave, False, msg, ahora)
    registrar(comp, causa, f"{msg} [{web['tipo']}: {web['detalle']}] (fallo original: {tipo_error or '?'})")
    return {"causa": causa, "mensaje": msg, "internet": True, "web": False, "tipo_error": tipo_error}


_ULTIMA_TODO = {"t": 0.0}


def comprobar_todo(ahora: float | None = None) -> dict:
    """Las tres sondas a la vez (botón «Comprobar ahora»). Actualiza el estado de red y lo devuelve. Como mucho una vez cada 5 s."""
    ahora = time.time() if ahora is None else ahora
    with _CERROJO_DIAG:
        if ahora - _ULTIMA_TODO["t"] < 5:
            return estado_red()
        _ULTIMA_TODO["t"] = ahora
        try:
            net = comprobar_internet()
            marcar("internet", net["ok"], "" if net["ok"] else MSG_SIN_INTERNET, ahora)
            if not net["ok"]:
                registrar("RED", "SIN_INTERNET", f"{MSG_SIN_INTERNET} (comprobación manual; {net['tipo']})")
                return estado_red()
            for clave, sonda, comp, caida, web in (("avamet", comprobar_avamet, "AVAMET", MSG_AVAMET_CAIDA, "www.avamet.org"),
                                                   ("aeropuertos", comprobar_aeropuertos, "METAR", MSG_METAR_CAIDA, "aviationweather.gov")):
                r = sonda()
                if r["ok"]:
                    marcar(clave, True, "", ahora)
                else:
                    msg = (f"⚠️ Hay Internet, pero no se resuelve el nombre de {web} (fallo de DNS)"
                           if r["tipo"] == "DNS" else caida)
                    marcar(clave, False, msg, ahora)
                    registrar(comp, "WEB_CAIDA" if r["tipo"] != "DNS" else "DNS", f"{msg} (comprobación manual; {r['tipo']}: {r['detalle']})")
        except Exception as e:               # noqa: BLE001
            registrar("RED", type(e).__name__, f"comprobación manual fallida: {e}")
        return estado_red()
