# Compilar y distribuir AVAMET Monitor (.exe para Windows)

## 1. Compilar (en un PC con Windows y Python)

Desde la carpeta del proyecto, en PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt pyinstaller
python scripts\descargar_vendor.py        # Leaflet y Chart.js -> frontend\vendor\ (necesita internet)
python generar_icono.py                  # (opcional, una vez) crea icono.ico; requiere: pip install Pillow
python comprobar_antes_de_compilar.py     # checklist: debe acabar en "TODO LISTO PARA COMPILAR"
python -m PyInstaller --clean AVAMET_Monitor.spec
```

`comprobar_antes_de_compilar.py` verifica entorno virtual, PyInstaller, dependencias de `requirements.txt`, `frontend\vendor\`,
`coordenadas.json`, sintaxis e importación de la aplicación y el contenido del `.spec`; los ❌ bloquean (código de salida 1) y los ⚠️ son avisos.

El resultado es **`dist\AVAMET_Monitor.exe`** (un solo fichero).

El `.spec` se detiene con un mensaje claro si falta `frontend\vendor\` (Leaflet/Chart.js) o `coordenadas.json`.
Incluye `frontend\` completo (con `vendor\`), `coordenadas.json`, los datos de `tzdata` y todos los módulos de `backend\`
(`vigilante`, `apagado`, `seguridad`, `movimiento`, `estaticos`, `instancia`, `registro`...). Si no existe `icono.ico`
compila con el icono por defecto.

> No he podido ejecutar PyInstaller en mi entorno (sin Windows): la compilación real y el arranque del .exe hay que
> probarlos en tu PC. Prueba en un PC "limpio" (sin Python) antes de repartirlo.

## 2. Qué meter en el ZIP para tus compañeros

| Fichero | Para qué |
|---|---|
| `AVAMET_Monitor.exe` | La aplicación |
| `.env.example` | Plantilla de configuración (opcional: el botón ⚙️ de la app rellena el `.env` por ti) |
| `GUIA_NINA.md` | Conectar N.I.N.A. (Alpaca en 127.0.0.1:8000) |
| `MANUAL_TECNICO.md` | Opcional: cómo funciona por dentro |

**No metas**: tu `.env`, `api_token.txt`, `historial.db`, `suscriptores.json` ni nada de `%APPDATA%\AVAMETMonitor`
(contienen tu token de Telegram, tus suscriptores y tu historial). Cada compañero debe crear **su propio bot** y su `.env`.

## 3. Primer arranque de cada compañero
1. Descomprimir y ejecutar `AVAMET_Monitor.exe` (se abre el navegador en `http://127.0.0.1:8000`).
2. Pulsar el botón **⚙️** de la cabecera y rellenar el formulario (token de Telegram, Admin ID y contraseña): se guarda y se aplica sin reiniciar.
3. Solo si editan el `.env` a mano: cerrar y volver a abrir la aplicación.

Windows SmartScreen o el antivirus pueden avisar con un `.exe` sin firmar; no puedo confirmar si ocurrirá con tu build.
