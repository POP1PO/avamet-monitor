"""Fase 2: todos los ficheros de datos salen de UNA carpeta (multiplataforma). pytest -v  o  python tests/test_datos.py"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

CODIGO = """
import json, sys
sys.path.insert(0, %r)
from backend import config, database, telegram, avamet
print(json.dumps({
 "data": str(config.DATA_DIR), "db": str(database.DB_FILE), "subs": str(telegram.SUSCRIPTORES_FILE),
 "coords_tg": str(telegram.COORDS_FILE), "cache": str(avamet.CACHE_FILE), "lluvia": config.ESTADO_LLUVIA_PATH,
 "viento": config.ESTADO_VIENTO_PATH, "token": config.API_TOKEN_PATH, "bolt": config.BOLTWOOD_PATH,
 "host": config.HOST, "port": config.PORT}))
"""


def _rutas(env_extra=None, cwd=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("AVAMET_")}
    env.update(env_extra or {})
    r = subprocess.run([sys.executable, "-c", CODIGO % str(RAIZ)], capture_output=True, text=True, env=env,
                       cwd=cwd or tempfile.mkdtemp())
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_todo_en_la_carpeta_indicada():
    d = Path(tempfile.mkdtemp()) / "mis datos"
    r = _rutas({"AVAMET_DATA_DIR": str(d)})
    assert Path(r["data"]) == d.resolve() and d.is_dir()               # se crea sola
    for clave in ("db", "subs", "coords_tg", "cache", "lluvia", "viento", "token", "bolt"):
        assert Path(r[clave]).parent == d.resolve(), (clave, r[clave])
    assert {Path(r[c]).name for c in ("db", "subs", "cache", "lluvia", "viento", "token", "bolt")} == {
        "historial.db", "suscriptores.json", "coordenadas.json", "lluvia_estado.json", "viento_estado.json",
        "api_token.txt", "boltwood.dat"}


def test_carpeta_relativa_se_resuelve_contra_el_directorio_actual():
    cwd = Path(tempfile.mkdtemp())
    r = _rutas({"AVAMET_DATA_DIR": "datos_rel"}, cwd=cwd)
    assert Path(r["data"]) == (cwd / "datos_rel").resolve()


def test_por_defecto_en_linux_es_data_del_proyecto():
    if os.name == "nt":
        return                                  # en Windows (código fuente) se mantiene la raíz del proyecto
    r = _rutas()
    assert Path(r["data"]) == RAIZ / "data" and Path(r["db"]).parent == RAIZ / "data"


def test_host_y_puerto_por_entorno():
    r = _rutas({"AVAMET_DATA_DIR": tempfile.mkdtemp()})
    assert r["host"] == "127.0.0.1" and r["port"] == 8000
    r = _rutas({"AVAMET_DATA_DIR": tempfile.mkdtemp(), "AVAMET_HOST": "0.0.0.0", "AVAMET_PORT": "8123"})
    assert r["host"] == "0.0.0.0" and r["port"] == 8123


def test_dotenv_en_el_directorio_actual_puede_fijar_la_carpeta():
    cwd = Path(tempfile.mkdtemp()); d = Path(tempfile.mkdtemp()) / "x"
    (cwd / ".env").write_text(f"AVAMET_DATA_DIR={d}\n", encoding="utf-8")
    r = _rutas(cwd=cwd)
    assert Path(r["data"]) == d.resolve()


def test_sin_rutas_con_appdata_ni_raiz_hardcodeadas():
    for f in ("database.py", "telegram.py", "avamet.py"):
        fuente = (RAIZ / "backend" / f).read_text(encoding="utf-8")
        assert "APPDATA" not in fuente, f


if __name__ == "__main__":
    fallos = 0
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            try: f(); print("OK  ", n)
            except Exception as e: fallos += 1; print("FALLA", n, repr(e))
    sys.exit(1 if fallos else 0)
