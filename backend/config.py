import os
import sys
from pathlib import Path

RAIZ_PROYECTO = Path(__file__).resolve().parent.parent


def carpeta_datos() -> Path:
    """Carpeta donde viven TODOS los ficheros de datos (historial.db, api_token.txt, lluvia_estado.json,
    viento_estado.json, boltwood.dat, suscriptores.json, coordenadas.json, .env).

    Orden de decisión:
      1. Variable AVAMET_DATA_DIR (o .env del directorio actual): ruta absoluta o relativa al directorio actual.
      2. .exe de PyInstaller (Windows): %APPDATA%\\AVAMETMonitor  (como siempre).
      3. Windows ejecutando desde código fuente: la carpeta del proyecto (como siempre, para no perder tu historial.db).
      4. Linux / macOS (Raspberry Pi): <proyecto>/data/
    """
    env = os.getenv("AVAMET_DATA_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if getattr(sys, "frozen", False):
        # En PyInstaller onefile, __file__ apunta a una carpeta temporal: NO usarla.
        return Path(os.getenv("APPDATA") or Path.home()) / "AVAMETMonitor"
    if os.name == "nt":
        return RAIZ_PROYECTO
    return RAIZ_PROYECTO / "data"


_carpeta_datos = carpeta_datos   # alias por compatibilidad

# Variables de entorno desde un fichero .env (opcional; python-dotenv ya está en requirements).
# Se buscan, por este orden, en: directorio actual, raíz del proyecto y carpeta de datos (el primero que
# define una variable gana; los dos primeros pueden definir AVAMET_DATA_DIR).
# Las variables reales del sistema (p. ej. Environment= de systemd) siempre mandan.
try:
    from dotenv import load_dotenv
    load_dotenv(Path.cwd() / ".env")
    load_dotenv(RAIZ_PROYECTO / ".env")
    load_dotenv(carpeta_datos() / ".env")
except ImportError:
    pass

DATA_DIR = carpeta_datos()
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except OSError as _e:
    print(f"⚠️  No se pudo crear la carpeta de datos {DATA_DIR}: {_e}")

ESTACION_CENTRAL_ID = "c05m009e02"
ESTACION_CENTRAL_NOMBRE = "Almassora"
RADIO_KM = 25.0
INTERVALO_CENTRAL = 60
INTERVALO_RADIO = 180
UMBRAL_VIENTO_KMH = 40
UMBRAL_VIENTO_MAX_KMH = 45       # racha >= esto => VIGILANCIA (sostenido >= UMBRAL_VIENTO_KMH)
UMBRAL_HUMEDAD_PCT = 90
# Viento fuerte = PELIGRO (el techo debe cerrarse / no abrirse). Estrictamente MAYOR que:
UMBRAL_VIENTO_PELIGRO_KMH = 45     # viento sostenido
UMBRAL_RACHA_PELIGRO_KMH = 55      # racha máxima
# Tras detectar lluvia, el estado se mantiene en PELIGRO este tiempo desde el ÚLTIMO incremento.
RETENCION_LLUVIA_S = 30 * 60
# Radar predictivo: una estación solo cuenta como "con lluvia" si su acumulado SUBIÓ en los últimos X segundos
VENTANA_LLUVIA_RADAR_S = 30 * 60
# Igual para el viento: tras el último viento/racha en PELIGRO se mantiene PELIGRO este tiempo.
# Evita que N.I.N.A. abra y cierre el techo con ráfagas oscilando alrededor del umbral.
RETENCION_VIENTO_S = 15 * 60
# Vigilante del bucle principal: si falla (p. ej. sin red al arrancar) se reintenta con espera creciente.
VIGILANTE_ESPERA_INICIAL_S = int(os.getenv("AVAMET_VIGILANTE_ESPERA_S", "5"))
VIGILANTE_ESPERA_MAX_S = int(os.getenv("AVAMET_VIGILANTE_ESPERA_MAX_S", "300"))
# Cada cuánto comprueba el guardián que el bucle sigue vivo (y refresca el fichero Boltwood si no lo está).
GUARDIAN_PERIODO_S = int(os.getenv("AVAMET_GUARDIAN_PERIODO_S", "30"))
# Telegram: aviso de SIN_DATOS solo si dura al menos esto (evita avisar por un único timeout de red).
# 0 = avisar en cuanto se detecte. El token y la contraseña NO van aquí: ver AVAMET_TELEGRAM_TOKEN
# y AVAMET_TELEGRAM_CLAVE en .env (ejemplo en .env.example).
TELEGRAM_SIN_DATOS_DEMORA_S = int(os.getenv("AVAMET_TELEGRAM_DEMORA_SIN_DATOS_S", "180"))
# Rango físicamente plausible de presión a nivel del mar. Fuera de él la lectura de presión
# se IGNORA (no se guarda ni entra en tendencias) pero temperatura y humedad siguen valiendo.
PRESION_MIN_HPA = 850
PRESION_MAX_HPA = 1100
# Dirección de escucha. 127.0.0.1 = solo este equipo. 0.0.0.0 = toda la red local (Raspberry Pi headless;
# run.py --headless la usa por defecto). Se cambia con AVAMET_HOST o run.py --host.
HOST = os.getenv("AVAMET_HOST", "").strip() or "127.0.0.1"
# Modo headless (lo fija run.py a partir de --headless / AVAMET_HEADLESS / Linux sin escritorio). Solo lo usa la interfaz
# para decidir si muestra la pestaña «Configuración» (pensada para el .exe de escritorio).
HEADLESS = os.getenv("AVAMET_HEADLESS", "").strip().lower() in ("1", "true", "yes", "si", "sí", "on")
PORT = int(os.getenv("AVAMET_PORT", "8000"))

# ============================================================
# FRESCURA DE LOS DATOS (seguridad)
# ============================================================
# Si la lectura de la estación central ("Fecha lectura" de AVAMET) es más
# antigua que esto, NO se considera segura (se trata como SIN DATOS).
EDAD_MAX_DATOS_S = int(os.getenv("AVAMET_EDAD_MAX_DATOS_S", "600"))   # 10 min
# Si el bucle principal no actualiza ESTADO en este tiempo, tampoco es seguro
# (el bucle se ha colgado o ha muerto).
EDAD_MAX_CICLO_S = 5 * INTERVALO_CENTRAL
# Estación de respaldo (backend/respaldo.py): si la principal deja de enviar datos, se usa la cercana más próxima que
# sí los envíe (como mucho a esta distancia de la principal) y se vuelve a la principal cuando publica N lecturas nuevas.
# 0 km = desactivado (la app pasa a SIN DATOS => NO SEGURO, como antes).
RESPALDO_MAX_KM = float(os.getenv("AVAMET_RESPALDO_MAX_KM", "10"))
RESPALDO_LECTURAS_VUELTA = int(os.getenv("AVAMET_RESPALDO_LECTURAS_VUELTA", "2"))
# Zona horaria de la "Fecha lectura" que publica AVAMET (hora local de la estación).
ZONA_HORARIA = os.getenv("AVAMET_TZ", "Europe/Madrid")

# Estado persistido de la retención de lluvia (sobrevive a reinicios de la app).
ESTADO_LLUVIA_PATH = os.getenv("AVAMET_ESTADO_LLUVIA_PATH") or str(DATA_DIR / "lluvia_estado.json")
ESTADO_VIENTO_PATH = os.getenv("AVAMET_ESTADO_VIENTO_PATH") or str(DATA_DIR / "viento_estado.json")

# ============================================================
# AUTENTICACIÓN DE LA API (/api/shutdown, /api/ubicacion)
# ============================================================
# Token Bearer. Orden: variable AVAMET_API_TOKEN (o .env) -> fichero API_TOKEN_PATH -> se genera uno
# aleatorio la primera vez y se guarda ahí. Mínimo 16 caracteres.
API_TOKEN_PATH = os.getenv("AVAMET_API_TOKEN_PATH") or str(DATA_DIR / "api_token.txt")
API_MAX_FALLOS = 5            # intentos fallidos por IP ...
API_VENTANA_FALLOS_S = 300    # ... dentro de esta ventana => 429


# ============================================================
# FICHERO BOLTWOOD (Cloud Sensor II, "Single Line Data File", formato nuevo)
# ============================================================
BOLTWOOD_ENABLED = os.getenv("AVAMET_BOLTWOOD", "1").strip().lower() not in ("0", "false", "no", "off")
BOLTWOOD_PATH = os.getenv("AVAMET_BOLTWOOD_PATH") or str(DATA_DIR / "boltwood.dat")
# AVAMET no mide nubes ni luz. Valores fijos que se escriben cuando HAY datos válidos:
#   nubes: 0=desconocido 1=despejado 2=nublado 3=muy nublado
#   luz:   0=desconocido 1=oscuro    2=luz      3=mucha luz
# Déjalos en 0 (desconocido) salvo que tu consumidor trate 0 como inseguro
# y tú controles nubes/luz por otros medios.
BOLTWOOD_COND_NUBES = 0
BOLTWOOD_COND_LUZ = 0

# Cada cuántos segundos manda /stream un latido ("event: ping") si no hay datos nuevos. El watchdog de la página
# reconecta si pasan 60 s sin ningún evento, así que debe ser bastante menor que 60.
SSE_LATIDO_S = int(os.getenv("AVAMET_SSE_LATIDO_S", "15"))

# ============================================================
# DIAGNÓSTICO DE RED (diagnostico.log)
# ============================================================
# Eventos de fallo (AVAMET, METAR, Telegram...) de la última hora; el fichero se poda solo. Se descarga desde ⚙️ Configuración.
DIAG_LOG_PATH = os.getenv("AVAMET_DIAG_LOG_PATH") or str(DATA_DIR / "diagnostico.log")
DIAG_VENTANA_S = int(os.getenv("AVAMET_DIAG_VENTANA_S", "3600"))
# Mejora 7 (3-nov-2026): además, cada línea se copia a un fichero por día (diagnostico_AAAA-MM-DD.log) en esta carpeta, que se
# conserva DIAG_HISTORICO_DIAS días (0 = desactivado). Así se puede revisar qué pasó una noche entera, no solo la última hora.
DIAG_HISTORICO_DIR = os.getenv("AVAMET_DIAG_HISTORICO_DIR") or str(DATA_DIR / "diagnostico_historico")
DIAG_HISTORICO_DIAS = int(os.getenv("AVAMET_DIAG_HISTORICO_DIAS", "30"))

# ============================================================
# ASCOM ALPACA
# ============================================================
# Estricto (por defecto): IsSafe solo responde si el cliente ha enviado PUT connected=true; si no, devuelve el
# error ASCOM NotConnected (0x407 = 1031), como exige el estándar. AVAMET_ALPACA_ESTRICTO=0 vuelve al
# comportamiento antiguo (siempre "conectado"), por si algún cliente gestiona la conexión por su cuenta.
ALPACA_ESTRICTO = os.getenv("AVAMET_ALPACA_ESTRICTO", "1").strip().lower() not in ("0", "false", "no", "off")
