"""A3: la BD no debe guardar ceros. Ejecutar: pytest -v   o   python tests/test_database.py
Ahora `import backend.database` ya NO toca ninguna BD (M5); el test usa una BD temporal."""
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import database   # noqa: E402

_TMP = tempfile.mkdtemp()
database.DB_FILE = Path(_TMP) / "test.db"
database.init_db()

OK = {"Temperatura": "15.1 °C", "Humedad": "90 %", "Presión": "1021 hPa", "Viento": "10 km/h ONO", "Lluvia hoy": "0.0 mm"}


def _filas():
    with sqlite3.connect(database.DB_FILE) as c:
        return c.execute("SELECT estacion_id, temperatura, humedad, presion, viento, lluvia FROM lecturas ORDER BY id").fetchall()


def test_no_guarda_errores_ni_lecturas_incompletas():
    n0 = len(_filas())
    assert database.guardar_lectura("x", {"Error": "timeout"}) is False
    assert database.guardar_lectura("x", {}) is False
    assert database.guardar_lectura("x", {"Temperatura": "15.1 °C"}) is False                     # sin humedad
    assert database.guardar_lectura("x", {"Humedad": "90 %", "Presión": "1021 hPa"}) is False     # sin temperatura
    assert len(_filas()) == n0                                                                      # NADA de ceros


def test_guarda_lectura_completa():
    assert database.guardar_lectura("c05m009e02", OK) is True
    assert _filas()[-1] == ("c05m009e02", 15.1, 90.0, 1021.0, 10.0, 0.0)


def test_presion_fuera_de_rango_se_ignora_pero_se_guarda_el_resto():
    d = dict(OK, **{"Presión": "800 hPa"})
    assert database.guardar_lectura("c05m009e02", d) is True
    assert _filas()[-1] == ("c05m009e02", 15.1, 90.0, None, 10.0, 0.0)           # presión NULL, temp/hum guardadas
    d = {k: v for k, v in OK.items() if k != "Presión"}
    assert database.guardar_lectura("c05m009e02", d) is True
    assert _filas()[-1][3] is None


def test_tendencia_ignora_presiones_nulas_y_absurdas():
    est = "tend"
    ahora = datetime.now(timezone.utc)
    filas = [(ahora - timedelta(minutes=25), 15.0, 80.0, 1021.0),
             (ahora - timedelta(minutes=20), 15.0, 80.0, None),            # presión descartada
             (ahora - timedelta(minutes=15), 14.0, 85.0, 1.02),            # fila antigua con el bug 1.021
             (ahora - timedelta(minutes=5), 13.0, 90.0, 1018.0)]
    with sqlite3.connect(database.DB_FILE) as c:
        for ts, t, h, p in filas:
            c.execute("INSERT INTO lecturas (estacion_id, timestamp, temperatura, humedad, presion, viento, lluvia) VALUES (?,?,?,?,?,?,?)",
                      (est, ts.isoformat(timespec="seconds"), t, h, p, 5.0, 0.0))
    tr = database.obtener_tendencia(est, minutos=30)
    assert tr == {"delta_presion": -3.0, "delta_temperatura": -2.0, "delta_humedad": 10.0}, tr
    hist = database.obtener_historial_24h(est)
    assert [f["presion"] for f in hist] == [1021.0, None, None, 1018.0]           # la gráfica deja huecos


def test_viento_y_lluvia_ausentes_son_null_no_cero():
    d = {k: v for k, v in OK.items() if k not in ("Viento", "Lluvia hoy")}
    assert database.guardar_lectura("c05m009e02", d) is True
    assert _filas()[-1][4:] == (None, None)


if __name__ == "__main__":
    fallos = 0
    for nombre, fn in sorted(globals().items()):
        if nombre.startswith("test_") and callable(fn):
            try:
                fn(); print(f"OK    {nombre}")
            except Exception as e:                                          # noqa: BLE001
                fallos += 1; print(f"FALLO {nombre}: {type(e).__name__}: {e}")
    sys.exit(1 if fallos else 0)
