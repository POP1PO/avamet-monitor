import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config

DB_FILE = config.DATA_DIR / "historial.db"

# Marca de las marcas de tiempo guardadas en UTC con zona explícita (ver ahora_utc_iso).
_SUFIJO_UTC = "+00:00"
_INICIALIZADAS: set = set()       # rutas de BD ya preparadas en este proceso


def ahora_utc_iso(desfase: timedelta = timedelta(0)) -> str:
    """Instante actual (menos `desfase`) en UTC, ISO-8601 con zona: '2026-11-01T13:00:00+00:00'.

    M5: antes se guardaba la hora local SIN zona, ambigua en el cambio de hora de octubre (la 02:30 ocurre
    dos veces) y el borrado de 48 h / las tendencias podían equivocarse. En UTC el orden de las cadenas
    coincide siempre con el orden cronológico. El navegador la convierte a su hora local al pintar.
    """
    return (datetime.now(timezone.utc) - desfase).isoformat(timespec="seconds")


def init_db() -> None:
    """Crea la tabla y el índice, y convierte a UTC las filas antiguas (hora local sin zona). Idempotente.

    Ya NO se ejecuta al importar el módulo (M5): se llama sola, una vez por proceso y BD, la primera vez que
    se usa; también puedes llamarla explícitamente (los tests cambian DB_FILE y la llaman).
    """
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(DB_FILE)) as conn, conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lecturas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                estacion_id TEXT,
                timestamp TEXT,
                temperatura REAL,
                humedad REAL,
                presion REAL,
                viento REAL,
                lluvia REAL
            )
        """)
        try:
            conn.execute("ALTER TABLE lecturas ADD COLUMN estacion_id TEXT DEFAULT 'unknown'")
        except sqlite3.OperationalError:
            pass
        # Registro de las consultas de satélite (Meteosat) junto con lo que medía la estación en ese momento (para calibrar umbrales)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS satelite_registro (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                estacion_id TEXT, timestamp TEXT, barrido_utc TEXT, satelite TEXT,
                t108 REAL, t039 REAL, min_regional REAL, noche INTEGER, estado TEXT,
                aire REAL, humedad REAL, viento REAL, lluvia_hoy REAL
            )
        """)
        try:      # mejora 3: mínima de 10,8 µm dentro del radio de aviso (las filas anteriores la tienen a NULL)
            conn.execute("ALTER TABLE satelite_registro ADD COLUMN min_radio REAL")
        except sqlite3.OperationalError:
            pass
        conn.execute("CREATE INDEX IF NOT EXISTS idx_satelite_estacion_ts ON satelite_registro (estacion_id, timestamp)")
        # Todas las consultas filtran por estación y ordenan/filtran por tiempo
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lecturas_estacion_ts ON lecturas (estacion_id, timestamp)")
        # Migración única: filas antiguas con hora local sin zona -> UTC con zona
        antiguas = conn.execute("SELECT id, timestamp FROM lecturas WHERE timestamp NOT LIKE ?", ("%" + _SUFIJO_UTC,)).fetchall()
        for fila_id, ts in antiguas:
            try:
                utc = datetime.fromisoformat(ts).astimezone(timezone.utc).isoformat(timespec="seconds")   # naive = hora local de este equipo
            except (TypeError, ValueError):
                conn.execute("DELETE FROM lecturas WHERE id = ?", (fila_id,))          # marca ilegible: no sirve para nada
                continue
            conn.execute("UPDATE lecturas SET timestamp = ? WHERE id = ?", (utc, fila_id))
    _INICIALIZADAS.add(str(DB_FILE))


@contextmanager
def _conexion():
    """Conexión que se CIERRA siempre (sqlite3 usado con `with` solo hace commit, no cierra) y confirma al salir."""
    if str(DB_FILE) not in _INICIALIZADAS:
        init_db()
    with closing(sqlite3.connect(DB_FILE)) as conn, conn:
        yield conn


CLAVES_OBLIGATORIAS = ("Temperatura", "Humedad")   # sin ellas no se guarda nada


def _a_float(texto):
    try:
        return float(str(texto).split()[0].replace(",", "."))
    except (ValueError, IndexError):
        return None


def guardar_lectura(estacion_id: str, datos: dict) -> bool:
    """Guarda una lectura. NO inserta nada si es un error o faltan temperatura/humedad
    (antes se guardaban ceros y las tendencias daban falsas "caídas bruscas").
    Presión ausente o fuera de rango (config.PRESION_MIN_HPA..MAX) -> NULL: se ignora, pero la
    temperatura y la humedad sí se guardan. Viento y lluvia ausentes -> NULL, no 0.
    Devuelve True si guardó."""
    if not isinstance(datos, dict) or "Error" in datos:
        print(f"BD: lectura de {estacion_id} con error, no se guarda")
        return False
    faltan = [k for k in CLAVES_OBLIGATORIAS if _a_float(datos.get(k)) is None]
    if faltan:
        print(f"BD: lectura de {estacion_id} incompleta (faltan {faltan}), no se guarda")
        return False
    try:
        temp, hum = (_a_float(datos[k]) for k in CLAVES_OBLIGATORIAS)
        pres = _a_float(datos.get("Presión"))
        if pres is not None and not (config.PRESION_MIN_HPA <= pres <= config.PRESION_MAX_HPA):
            print(f"BD: presión {pres} hPa fuera de rango, se ignora")
            pres = None
        viento = _a_float(datos.get("Viento"))
        lluvia = _a_float(datos.get("Lluvia hoy"))
        with _conexion() as conn:
            conn.execute("""
                INSERT INTO lecturas (estacion_id, timestamp, temperatura, humedad, presion, viento, lluvia)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (estacion_id, ahora_utc_iso(), temp, hum, pres, viento, lluvia))
            limite = ahora_utc_iso(timedelta(hours=48))
            conn.execute("DELETE FROM lecturas WHERE timestamp < ?", (limite,))
        return True
    except Exception as e:
        print(f"Error guardando en BD: {e}")
        return False

SATELITE_RETENCION_DIAS = 400


def guardar_satelite(estacion_id: str, sat: dict, datos_estacion: dict) -> bool:
    """Guarda una consulta de satélite (valores ya calculados) con la lectura de la estación de ese momento. Nunca lanza."""
    try:
        if not isinstance(sat, dict) or sat.get("t108_c") is None:
            return False
        d = datos_estacion if isinstance(datos_estacion, dict) else {}
        with _conexion() as conn:
            # Una fila por imagen y estación: si la misma imagen llega dos veces (p. ej. EUMETSAT sin publicar otra nueva,
            # o un reinicio de la app), no se duplica en el informe de comparación.
            if sat.get("barrido_utc") and conn.execute(
                    "SELECT 1 FROM satelite_registro WHERE estacion_id = ? AND barrido_utc = ? LIMIT 1",
                    (estacion_id, sat.get("barrido_utc"))).fetchone():
                return False
            conn.execute("""
                INSERT INTO satelite_registro (estacion_id, timestamp, barrido_utc, satelite, t108, t039, min_regional, noche, estado,
                                               aire, humedad, viento, lluvia_hoy, min_radio)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (estacion_id, ahora_utc_iso(), sat.get("barrido_utc"), sat.get("satelite"), sat.get("t108_c"), sat.get("t039_c"),
                  sat.get("min_regional_c"), 1 if sat.get("noche") else 0, sat.get("estado"),
                  _a_float(d.get("Temperatura")), _a_float(d.get("Humedad")), _a_float(d.get("Viento")), _a_float(d.get("Lluvia hoy")),
                  sat.get("min_radio_c")))
            conn.execute("DELETE FROM satelite_registro WHERE timestamp < ?", (ahora_utc_iso(timedelta(days=SATELITE_RETENCION_DIAS)),))
        return True
    except Exception as e:
        print(f"Error guardando registro de satélite: {e}")
        return False


def leer_satelite(estacion_id: str, dias: int = 30) -> list:
    """Registros de satélite de los últimos `dias` días, del más antiguo al más reciente (dicts)."""
    with _conexion() as conn:
        conn.row_factory = sqlite3.Row
        filas = conn.execute("SELECT * FROM satelite_registro WHERE estacion_id = ? AND timestamp >= ? ORDER BY timestamp",
                             (estacion_id, ahora_utc_iso(timedelta(days=max(1, int(dias)))))).fetchall()
        return [dict(f) for f in filas]


def obtener_historial_24h(estacion_id: str):
    with _conexion() as conn:
        conn.row_factory = sqlite3.Row
        limite = ahora_utc_iso(timedelta(hours=24))
        cursor = conn.execute(
            "SELECT * FROM lecturas WHERE estacion_id = ? AND timestamp >= ? ORDER BY timestamp ASC", 
            (estacion_id, limite)
        )
        filas = [dict(row) for row in cursor.fetchall()]
        for f in filas:
            if f["presion"] is None or not (config.PRESION_MIN_HPA <= f["presion"] <= config.PRESION_MAX_HPA):
                f["presion"] = None            # la gráfica deja un hueco en vez de un pico absurdo
        return filas

def obtener_tendencia(estacion_id: str, minutos=30):
    """Deltas en los últimos `minutos`. La presión usa SOLO lecturas con presión válida
    (ignora NULL y valores fuera de rango, p. ej. filas antiguas con el bug 1.021)."""
    limite = ahora_utc_iso(timedelta(minutes=minutos))
    res = {"delta_presion": 0.0, "delta_temperatura": 0.0, "delta_humedad": 0.0}
    pmin, pmax = config.PRESION_MIN_HPA, config.PRESION_MAX_HPA
    with _conexion() as conn:
        conn.row_factory = sqlite3.Row
        primera = conn.execute(
            "SELECT * FROM lecturas WHERE estacion_id = ? AND timestamp >= ? ORDER BY timestamp ASC LIMIT 1",
            (estacion_id, limite)).fetchone()
        ultima = conn.execute(
            "SELECT * FROM lecturas WHERE estacion_id = ? ORDER BY timestamp DESC LIMIT 1",
            (estacion_id,)).fetchone()
        if primera and ultima and primera['timestamp'] != ultima['timestamp']:
            try:
                res["delta_temperatura"] = round(float(ultima['temperatura']) - float(primera['temperatura']), 1)
                res["delta_humedad"] = round(float(ultima['humedad']) - float(primera['humedad']), 1)
            except (TypeError, ValueError):
                pass
        q = ("SELECT presion, timestamp FROM lecturas WHERE estacion_id = ? AND presion IS NOT NULL "
             "AND presion BETWEEN ? AND ? ")
        p1 = conn.execute(q + "AND timestamp >= ? ORDER BY timestamp ASC LIMIT 1", (estacion_id, pmin, pmax, limite)).fetchone()
        p2 = conn.execute(q + "ORDER BY timestamp DESC LIMIT 1", (estacion_id, pmin, pmax)).fetchone()
        if p1 and p2 and p1['timestamp'] != p2['timestamp']:
            res["delta_presion"] = round(float(p2['presion']) - float(p1['presion']), 1)
    return res
