"""Base de datos, arranque, log, instancia única y Alpaca. pytest -v  o  python tests/test_arranque_bd_alpaca.py"""
import asyncio
import http.server
import json
import os
import sqlite3
import sys
import tempfile
import threading
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from backend import aeropuertos, avamet, database, instancia, registro, telegram   # noqa: E402


# ---------- M5: base de datos ----------
def _bd_nueva():
    d = Path(tempfile.mkdtemp()); database.DB_FILE = d / "sub" / "t.db"; return database.DB_FILE


def test_importar_no_crea_bd_y_se_crea_al_usarla():
    f = _bd_nueva()
    assert not f.exists()                                   # nada se hace al importar / cambiar la ruta
    assert database.guardar_lectura("e", {"Temperatura": "10 °C", "Humedad": "50 %"}) is True
    assert f.exists()                                       # init perezoso (y crea la carpeta que faltaba)


def test_indice_creado():
    f = _bd_nueva(); database.init_db()
    with sqlite3.connect(f) as c:
        idx = [r[1] for r in c.execute("PRAGMA index_list(lecturas)")]
        cols = [r[2] for r in c.execute("PRAGMA index_info(idx_lecturas_estacion_ts)")]
    assert "idx_lecturas_estacion_ts" in idx and cols == ["estacion_id", "timestamp"]


def test_marcas_en_utc_con_zona():
    _bd_nueva(); database.guardar_lectura("e", {"Temperatura": "10 °C", "Humedad": "50 %"})
    ts = database.obtener_historial_24h("e")[0]["timestamp"]
    assert ts.endswith("+00:00") and abs(datetime.fromisoformat(ts) - datetime.now(timezone.utc)) < timedelta(seconds=5)


def test_migracion_de_filas_antiguas_hora_local_sin_zona():
    f = _bd_nueva(); database.init_db()
    local = datetime.now() - timedelta(minutes=10)           # formato antiguo: hora local sin zona
    with sqlite3.connect(f) as c:
        c.execute("INSERT INTO lecturas (estacion_id, timestamp, temperatura, humedad) VALUES ('e', ?, 9, 60)", (local.isoformat(),))
        c.execute("INSERT INTO lecturas (estacion_id, timestamp, temperatura, humedad) VALUES ('e', 'basura', 9, 60)")
    database._INICIALIZADAS.clear(); database.init_db()
    filas = database.obtener_historial_24h("e")
    assert len(filas) == 1 and filas[0]["timestamp"].endswith("+00:00")
    assert abs(datetime.fromisoformat(filas[0]["timestamp"]) - local.astimezone(timezone.utc)) < timedelta(seconds=2)
    database.init_db()                                       # idempotente
    assert len(database.obtener_historial_24h("e")) == 1


def test_conexion_se_cierra():
    _bd_nueva(); database.init_db()
    with database._conexion() as c: pass
    try: c.execute("SELECT 1"); cerrada = False
    except sqlite3.ProgrammingError: cerrada = True
    assert cerrada


def test_borrado_de_48h_funciona_con_utc():
    f = _bd_nueva(); database.init_db()
    viejo = (datetime.now(timezone.utc) - timedelta(hours=49)).isoformat(timespec="seconds")
    with sqlite3.connect(f) as c:
        c.execute("INSERT INTO lecturas (estacion_id, timestamp, temperatura, humedad) VALUES ('e', ?, 9, 60)", (viejo,))
    database.guardar_lectura("e", {"Temperatura": "10 °C", "Humedad": "50 %"})
    with sqlite3.connect(f) as c:
        assert c.execute("SELECT COUNT(*) FROM lecturas").fetchone()[0] == 1


# ---------- M9: instancia, log ----------
class _H(http.server.BaseHTTPRequestHandler):
    nombre = "AVAMET Monitor"
    def do_GET(self):
        self.send_response(200); self.end_headers()
        self.wfile.write(json.dumps({"Value": {"ServerName": type(self).nombre}}).encode())
    def log_message(self, *a): pass


def _serv(nombre):
    h = type("H", (_H,), {"nombre": nombre})
    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=s.serve_forever, daemon=True).start(); return s


def test_instancia_propia_y_ajena():
    a = _serv("AVAMET Monitor"); b = _serv("Otro programa")
    try:
        assert instancia.es_instancia_avamet("0.0.0.0", a.server_address[1], intentos=1)
        assert not instancia.es_instancia_avamet("127.0.0.1", b.server_address[1], intentos=1)
    finally: a.shutdown(); b.shutdown()
    assert not instancia.es_instancia_avamet("127.0.0.1", 1, timeout=0.3, intentos=1)       # nada escuchando


def test_log_rotativo():
    d = Path(tempfile.mkdtemp()); f = registro.FicheroRotativo(d / "log.txt", max_bytes=200, copias=2)
    for i in range(60): f.write(f"línea número {i:03d} con relleno\n")
    nombres = sorted(p.name for p in d.iterdir())
    assert nombres == ["log.txt", "log.txt.1", "log.txt.2"], nombres
    assert all(p.stat().st_size <= 400 for p in d.iterdir())
    assert "059" in (d / "log.txt").read_text(encoding="utf-8") or "059" in (d / "log.txt.1").read_text(encoding="utf-8")


def test_log_grande_heredado_se_rota_al_abrir():
    d = Path(tempfile.mkdtemp()); (d / "log.txt").write_text("x" * 500, encoding="utf-8")
    registro.FicheroRotativo(d / "log.txt", max_bytes=100, copias=1)
    assert (d / "log.txt.1").exists() and (d / "log.txt").stat().st_size == 0


def test_run_py_sin_appdata_no_usa_path_none():
    src = (RAIZ / "run.py").read_text(encoding="utf-8")
    assert "Path(os.getenv('APPDATA'))" not in src and "FicheroRotativo" in src and "es_instancia_avamet" in src


# ---------- B1: ClientTransactionID ----------
def _main_con_fastapi_falso():
    if "backend.main" in sys.modules: return sys.modules["backend.main"]
    class HTTPException(Exception):
        def __init__(self, status_code, detail=None, headers=None): self.status_code = status_code
    fa = types.ModuleType("fastapi")
    class _App:
        def __init__(self, *a, **k): pass
        def _d(self, *a, **k): return lambda f: f
        get = put = post = mount = _d
        def add_middleware(self, *a, **k): pass
    fa.FastAPI = _App; fa.Request = object; fa.Body = lambda *a, **k: None; fa.HTTPException = HTTPException; fa.BackgroundTasks = object
    fr = types.ModuleType("fastapi.responses"); fr.FileResponse = fr.StreamingResponse = fr.Response = object
    sys.modules.setdefault("fastapi", fa); sys.modules.setdefault("fastapi.responses", fr)
    import backend.main as m
    return m


def test_client_transaction_id_tolerante():
    m = _main_con_fastapi_falso()
    r = lambda **q: types.SimpleNamespace(query_params=q)
    assert m._client_tx(r(ClientTransactionID="42")) == 42
    assert m._client_tx(r(clienttransactionid="7")) == 7              # Alpaca no distingue mayúsculas
    assert m._client_tx(r(ClientTransactionID="abc")) == 0            # antes: 500
    assert m._client_tx(r(ClientTransactionID="")) == 0 and m._client_tx(r(ClientTransactionID="-5")) == 0
    assert m._client_tx(r(ClientTransactionID="99999999999")) == 0 and m._client_tx(r()) == 0
    out = m._alpaca_respuesta(True, r(ClientTransactionID="xx"))
    assert out["Value"] is True and out["ErrorNumber"] == 0 and out["ClientTransactionID"] == 0


# ---------- B3 ----------
def test_user_agent_sin_correo_inventado():
    os.environ.pop("AVAMET_CONTACTO", None)
    ua = aeropuertos._user_agent(); assert "tudominio" not in ua and "@" not in ua
    os.environ["AVAMET_CONTACTO"] = "obs@ejemplo.org"
    try: assert "obs@ejemplo.org" in aeropuertos._user_agent()
    finally: os.environ.pop("AVAMET_CONTACTO", None)
    assert "tudominio" not in (RAIZ / "backend" / "aeropuertos.py").read_text(encoding="utf-8")


# ---------- B4 ----------
def test_municipios_se_rellenan_y_se_conservan():
    c = {"a": {"nombre": "Almassora (Nord)"}, "b": {"nombre": "Vila-real", "municipio": "Ya puesto"}, "c": {"nombre": ""}}
    avamet.completar_municipios(c)
    assert c["a"]["municipio"] == "Almassora" and c["b"]["municipio"] == "Ya puesto" and c["c"]["municipio"] == ""


def test_coordenadas_json_del_repo_queda_completo():
    d = avamet.completar_municipios(json.loads((RAIZ / "coordenadas.json").read_text(encoding="utf-8")))
    assert len(d) > 900 and all(v["municipio"] for v in d.values())


def test_pueblo_sin_tildes_ni_mayusculas_y_exacto_primero():
    coords = {"1": {"nombre": "Castelló de la Plana (Ermita)", "municipio": "Castelló de la Plana"},
              "2": {"nombre": "Castelló", "municipio": "Castelló"}, "3": {"nombre": "Vila-real"}}
    assert [e for e, _ in telegram.buscar_estaciones(coords, "CASTELLO")] == ["2", "1"]     # exacto primero
    assert [e for e, _ in telegram.buscar_estaciones(coords, "vila")] == ["3"]              # sin 'municipio' usa 'nombre'
    assert telegram.buscar_estaciones(coords, "  ") == [] and telegram.buscar_estaciones(coords, "zzz") == []


if __name__ == "__main__":
    fallos = 0
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            try: f(); print("OK  ", n)
            except Exception as e: fallos += 1; print("FALLA", n, repr(e))
    sys.exit(1 if fallos else 0)
