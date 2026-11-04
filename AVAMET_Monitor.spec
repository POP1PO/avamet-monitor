# -*- mode: python ; coding: utf-8 -*-
# Compilar (en Windows o en Linux, desde la carpeta del proyecto, con el entorno virtual activado):
#     python comprobar_antes_de_compilar.py        (checklist previo: debe terminar en "TODO LISTO PARA COMPILAR")
#     python scripts/descargar_vendor.py           (una vez: baja Leaflet y Chart.js a frontend/vendor/)
#     python -m PyInstaller --clean AVAMET_Monitor.spec     (genera dist\AVAMET_Monitor.exe)
import os
from PyInstaller.utils.hooks import collect_all, collect_data_files

# --- Comprobaciones previas: el .exe NO debe compilarse con piezas que faltan ---------------------------------
_RAIZ = os.path.abspath(SPECPATH)                       # SPECPATH: carpeta de este .spec (la inyecta PyInstaller)
_VENDOR = ['leaflet/leaflet.js', 'leaflet/leaflet.css', 'chart/chart.umd.js']      # = backend/estaticos.VENDOR_REQUERIDOS
_faltan = [r for r in _VENDOR if not os.path.isfile(os.path.join(_RAIZ, 'frontend', 'vendor', r))]
if _faltan:
    raise SystemExit("FALTAN librerias del frontend en frontend/vendor/: " + ", ".join(_faltan) +
                     "\nEjecuta antes:  python scripts/descargar_vendor.py")
if not os.path.isfile(os.path.join(_RAIZ, 'coordenadas.json')):
    raise SystemExit("Falta coordenadas.json en la carpeta del proyecto (se incluye dentro del .exe).")
_ICONO = os.path.join(_RAIZ, 'icono.ico')
if not os.path.isfile(_ICONO):
    print("AVISO: no existe icono.ico; el .exe se compila con el icono por defecto.")
    _ICONO = None

# 'frontend' entero (index.html, sw.js, manifest.json y vendor/ con Leaflet y Chart.js) y coordenadas.json
datas = [('frontend', 'frontend'), ('coordenadas.json', '.')]
datas += collect_data_files('tzdata')   # zona horaria Europe/Madrid (Boltwood / Fecha lectura)
binaries = []

# Todos los modulos de backend/ (si anades uno nuevo, entra solo; no hay lista que mantener)
hiddenimports = ['backend.' + f[:-3] for f in sorted(os.listdir(os.path.join(_RAIZ, 'backend')))
                 if f.endswith('.py') and f != '__init__.py']
hiddenimports += ['tzdata', 'dotenv', 'uvicorn.logging', 'uvicorn.loops.auto', 'uvicorn.protocols.http.auto',
                  'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on']


def _incluir(paquete, obligatorio=True):
    """Devuelve (datas, binaries, hiddenimports) del paquete completo (codigo, datos y binarios). Si es obligatorio y no
    esta instalado en este entorno, detiene la compilacion con un mensaje claro (en vez de producir un .exe roto)."""
    try:
        d, b, h = collect_all(paquete)
    except Exception as e:
        if obligatorio:
            raise SystemExit(f"Falta el paquete '{paquete}' en este entorno ({e}). Ejecuta: pip install -r requirements.txt")
        print(f"AVISO: paquete opcional '{paquete}' no disponible; se omite.")
        return [], [], []
    if obligatorio and not (d or b or h):
        raise SystemExit(f"El paquete '{paquete}' no esta instalado en este entorno. Ejecuta: pip install -r requirements.txt")
    return d, b, h


for _p, _obligatorio in (
        [(x, True) for x in ('uvicorn', 'fastapi', 'starlette', 'pydantic', 'pydantic_core', 'anyio', 'h11', 'bs4',
                             'requests', 'urllib3', 'idna', 'charset_normalizer',
                             'certifi')]      # certifi = certificados SSL: sin cacert.pem el .exe no valida HTTPS
        + [(x, False) for x in ('soupsieve', 'lxml', 'httptools', 'websockets',
                              'eumdac', 'netCDF4', 'cftime', 'numpy')]):    # opcionales: uvicorn[standard] y el satelite Meteosat (requirements-satelite.txt)
    _d, _b, _h = _incluir(_p, _obligatorio)
    datas += _d; binaries += _b; hiddenimports += _h


a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='AVAMET_Monitor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=(os.name != 'nt'),      # Windows: sin ventana de consola; Linux: con consola (arranca desde terminal / servicio)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[_ICONO] if _ICONO else None,
)
