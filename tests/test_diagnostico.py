"""Pruebas de backend/diagnostico.py (sin red: las sondas se sustituyen)."""
import sys, tempfile, socket, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import requests
from backend import diagnostico as d


def _nuevo(ventana=3600):
    dirt = tempfile.mkdtemp()
    return d.RegistroHora(Path(dirt) / "diagnostico.log", ventana)


def test_clasificar_errores():
    c = d.clasificar_error
    assert c(requests.exceptions.ReadTimeout("x"))[0] == "TIMEOUT"
    assert c(requests.exceptions.ConnectTimeout("x"))[0] == "TIMEOUT"
    assert c(requests.exceptions.SSLError("certificate verify failed"))[0] == "SSL"
    assert c(requests.exceptions.ConnectionError("Failed to resolve: Temporary failure in name resolution"))[0] == "DNS"
    assert c(requests.exceptions.ConnectionError("[WinError 11001] getaddrinfo failed"))[0] == "DNS"
    assert c(requests.exceptions.ConnectionError("[Errno 111] Connection refused"))[0] == "CONEXION_RECHAZADA"
    assert c(requests.exceptions.ConnectionError("[Errno 101] Network is unreachable"))[0] == "RED_INACCESIBLE"
    assert c(requests.exceptions.ConnectionError("Connection reset by peer"))[0] == "CONEXION_CORTADA"
    assert c(requests.exceptions.ConnectionError("otra cosa"))[0] == "SIN_CONEXION"
    assert c(ValueError("no es json"))[0] == "RESPUESTA_INVALIDA"
    assert c(KeyError("k"))[0] == "KeyError"
    resp = requests.Response(); resp.status_code = 503
    assert c(requests.exceptions.HTTPError("503", response=resp))[0] == "HTTP_503"


def test_registro_formato_poda_y_dedup():
    r = _nuevo(3600)
    t0 = 1_800_000_000.0
    assert r.registrar("AVAMET", "TIMEOUT", "uno", ahora=t0)
    assert not r.registrar("AVAMET", "TIMEOUT", "dos", ahora=t0 + 5)           # repetido dentro de 30 s: se omite
    assert not r.registrar("AVAMET", "TIMEOUT", "tres", ahora=t0 + 10)
    assert r.registrar("METAR", "TIMEOUT", "otro componente", ahora=t0 + 11)   # otro componente: se escribe
    assert r.registrar("AVAMET", "TIMEOUT", "cuatro", ahora=t0 + 40)           # pasado el plazo: se escribe, contando los omitidos
    txt = r.leer(t0 + 41)
    lineas = txt.splitlines()
    assert len(lineas) == 3
    partes = lineas[0].split(" | ")
    assert len(partes) == 4 and partes[1] == "AVAMET" and partes[2] == "TIMEOUT" and partes[3] == "uno"
    assert "[+2 igual(es) omitido(s)]" in lineas[2]
    # a las 2 h solo debe quedar lo de la última hora
    r.registrar("TELEGRAM", "DNS", "reciente", ahora=t0 + 7200)
    txt = r.leer(t0 + 7201)
    assert "reciente" in txt and "uno" not in txt and "cuatro" not in txt


def test_registro_limpia_token_pipe_y_saltos():
    r = _nuevo()
    tok = "123456789:AAH_abcdefghijklmnopqrstuvwxyz0123"
    r.registrar("TELEGRAM", "DNS", f"https://api.telegram.org/bot{tok}/sendMessage falló | otra\nlinea", ahora=1_800_000_000.0)
    txt = r.leer(1_800_000_001.0)
    assert tok not in txt and "AAH_" not in txt and "***" in txt
    assert len(txt.splitlines()) == 1 and txt.count(" | ") == 3


def test_registro_no_crece_y_poda_el_fichero():
    r = _nuevo(60)
    t0 = 1_800_000_000.0
    for i in range(50):
        r.registrar("AVAMET", f"E{i}", "x", ahora=t0 + i * 10)       # 500 s de eventos con ventana de 60 s
    assert len(r.ruta.read_text(encoding="utf-8").splitlines()) <= 8


def test_diagnostico_sin_internet(monkeypatch=None):
    _con_sondas(internet=False)
    d._reiniciar_estado_red()
    res = d.diagnosticar("avamet", "SIN_CONEXION", "x", usar_cache=False)
    assert res["causa"] == "SIN_INTERNET" and res["mensaje"] == "❌ Sin conexión a Internet en el observatorio"
    e = d.estado_red()
    assert e["internet"] is False and e["avamet"] is None
    assert "Sin conexión" in d.registro().leer()


def test_diagnostico_web_caida_y_dns_y_responde():
    d._reiniciar_estado_red()
    _con_sondas(internet=True, avamet=False, avamet_tipo="TIMEOUT")
    res = d.diagnosticar("avamet", "TIMEOUT", "x", usar_cache=False)
    assert res["causa"] == "WEB_CAIDA" and res["mensaje"] == "⚠️ La web de AVAMET no responde (posible caída del servidor)"
    assert d.estado_red()["avamet"] is False and d.estado_red()["internet"] is True
    _con_sondas(internet=True, metar=False, metar_tipo="HTTP_503")
    res = d.diagnosticar("metar", "TIMEOUT", "x", usar_cache=False)
    assert res["mensaje"] == "⚠️ La API de meteorología de aeropuertos no responde"
    _con_sondas(internet=True, avamet=False, avamet_tipo="DNS")
    res = d.diagnosticar("avamet", "DNS", "x", usar_cache=False)
    assert res["causa"] == "DNS" and "DNS" in res["mensaje"] and "caída del servidor" not in res["mensaje"]
    _con_sondas(internet=True, avamet=True)
    res = d.diagnosticar("avamet", "TIMEOUT", "x", usar_cache=False)
    assert res["causa"] == "RESPONDE" and "responde, pero la lectura falló" in res["mensaje"] and d.estado_red()["avamet"] is True


def test_recuperado_se_registra_y_acierto_implica_internet():
    d._reiniciar_estado_red()
    d.marcar("internet", False, "sin red", ahora=1_800_000_000.0)
    d.marcar("avamet", True, ahora=1_800_000_100.0)
    e = d.estado_red()
    assert e["internet"] is True and e["avamet"] is True and e["motivos"]["internet"] == ""
    assert "RECUPERADO" in d.registro().leer()


def test_cache_del_diagnostico():
    d._reiniciar_estado_red()
    n = {"c": 0}
    _con_sondas(internet=True, avamet=False)
    original = d.comprobar_avamet
    def contando():
        n["c"] += 1
        return original()
    d.comprobar_avamet = contando
    d.diagnosticar("avamet", "X", "", usar_cache=True, ahora=1_800_000_000.0)
    d.diagnosticar("avamet", "X", "", usar_cache=True, ahora=1_800_000_005.0)    # dentro de 20 s: reutiliza
    d.diagnosticar("avamet", "X", "", usar_cache=True, ahora=1_800_000_030.0)    # fuera: vuelve a sondear
    assert n["c"] == 2


def test_diagnosticar_nunca_lanza():
    d._reiniciar_estado_red()
    def malo(*a, **k): raise RuntimeError("fallo inesperado")
    d.comprobar_internet = malo
    res = d.diagnosticar("avamet", "X", "", usar_cache=False)
    assert res["causa"] == "DESCONOCIDA"


def test_comprobar_internet_real_contra_servidor_local():
    # La sonda real hace socket.create_connection: se comprueba con un servidor local y con un puerto cerrado.
    import importlib
    importlib.reload(d)
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1); puerto = srv.getsockname()[1]
    d.SONDAS_INTERNET = (("127.0.0.1", puerto),)
    ok = d.comprobar_internet(timeout=1)
    srv.close()
    d.SONDAS_INTERNET = (("127.0.0.1", puerto),)
    mal = d.comprobar_internet(timeout=1)
    assert ok["ok"] is True and mal["ok"] is False and mal["tipo"] in ("CONEXION_RECHAZADA", "SIN_CONEXION", "TIMEOUT")


def test_historico_por_dias_y_limpieza():
    from datetime import datetime as _dt
    dirt = Path(tempfile.mkdtemp())
    r = d.RegistroHora(dirt / "diagnostico.log", 3600, dirt / "hist", 2)
    t0 = _dt(2026, 11, 3, 4, 39).timestamp()                         # hora local del equipo
    assert r.registrar("AVAMET", "RESPALDO", "Castelló sin datos", ahora=t0)
    assert r.registrar("AVAMET", "RESPALDO_FIN", "vuelve", ahora=t0 + 600)
    f = dirt / "hist" / "diagnostico_2026-11-03.log"
    assert f.read_text(encoding="utf-8").count("\n") == 2
    # al día siguiente: otro fichero; la poda de la última hora NO toca el histórico
    assert r.registrar("METAR", "TIMEOUT", "x", ahora=t0 + 86400)
    assert (dirt / "hist" / "diagnostico_2026-11-04.log").is_file() and f.read_text(encoding="utf-8").count("\n") == 2
    assert "Castelló" not in r.leer(t0 + 86401)
    (dirt / "hist" / "otro.txt").write_text("no tocar", encoding="utf-8")
    # 3 días después (límite 2 días): se borra el del 3-nov, se queda el del 4 y lo que no es suyo
    assert r.registrar("METAR", "TIMEOUT", "y", ahora=t0 + 3 * 86400 + 4000)
    assert not f.is_file() and (dirt / "hist" / "otro.txt").is_file()
    assert r.ficheros_historico() == ["diagnostico_2026-11-06.log", "diagnostico_2026-11-04.log"]
    # desactivado (0 días): no crea carpeta
    r0 = d.RegistroHora(dirt / "b.log", 3600, dirt / "hist0", 0)
    assert r0.registrar("AVAMET", "X", "y", ahora=t0) and not (dirt / "hist0").exists() and r0.ficheros_historico() == []


def test_texto_descarga_tiene_cabecera_y_estado():
    d._reiniciar_estado_red()
    d.marcar("avamet", True)
    t = d.texto_descarga("9.9")
    assert t.startswith("# AVAMET Monitor 9.9") and "Estado de red ahora:" in t and "AVAMET=OK" in t


def _con_sondas(internet=True, avamet=True, metar=True, avamet_tipo="TIMEOUT", metar_tipo="TIMEOUT"):
    d.comprobar_internet = lambda timeout=2.5: {"ok": internet, "ms": 1, "tipo": "" if internet else "RED_INACCESIBLE", "detalle": "t"}
    d.comprobar_avamet = lambda: {"ok": avamet, "ms": 1, "http": 200, "tipo": "" if avamet else avamet_tipo, "detalle": "t"}
    d.comprobar_aeropuertos = lambda: {"ok": metar, "ms": 1, "http": 200, "tipo": "" if metar else metar_tipo, "detalle": "t"}


if __name__ == "__main__":
    import tempfile as _t
    from backend import config
    # el registro global escribe en una carpeta temporal, nunca en la real
    d._REGISTRO = d.RegistroHora(Path(_t.mkdtemp()) / "diagnostico.log", 3600)
    n = 0
    for nombre, f in list(globals().items()):
        if nombre.startswith("test_") and callable(f):
            f(); n += 1; print("OK", nombre)
    print(f"{n} pruebas OK")
