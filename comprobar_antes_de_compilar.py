#!/usr/bin/env python
"""Comprobación PRE-VUELO de AVAMET Monitor: ¿está todo listo para compilar el .exe con PyInstaller?

Uso (desde la carpeta del proyecto, con el entorno virtual activado):
    python comprobar_antes_de_compilar.py

Solo usa la biblioteca estándar, así que funciona aunque falten dependencias (precisamente para decírtelo).
Salida: un checklist con ✅ (correcto), ⚠️ (aviso, no bloquea) y ❌ (error, bloquea).
Código de salida: 0 = listo; 1 = hay al menos un ❌.
"""
import importlib
import importlib.metadata as md
import json
import os
import py_compile
import re
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
SPEC = RAIZ / "AVAMET_Monitor.spec"

try:  # emojis correctos en consolas de Windows
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ERRORES: list[str] = []
AVISOS: list[str] = []


def ok(msg: str) -> None:
    print(f"  ✅ {msg}")


def aviso(msg: str, ayuda: str = "") -> None:
    print(f"  ⚠️  {msg}")
    if ayuda:
        print(f"       → {ayuda}")
    AVISOS.append(msg)


def error(msg: str, ayuda: str = "") -> None:
    print(f"  ❌ {msg}")
    if ayuda:
        print(f"       → {ayuda}")
    ERRORES.append(msg)


def seccion(titulo: str) -> None:
    print(f"\n{titulo}")


# ------------------------------------------------------------------------------------------------------------------
def _version_tupla(v: str) -> tuple:
    partes = []
    for p in re.split(r"[.\-+]", v):
        m = re.match(r"\d+", p)
        if not m:
            break
        partes.append(int(m.group()))
    return tuple(partes)


def _cumple(version: str, espec: str) -> bool:
    """Comprueba una especificación simple (>=, <=, ==, >, <, ~=, !=; separadas por comas). Si no la entiende, True."""
    v = _version_tupla(version)
    for trozo in [t.strip() for t in espec.split(",") if t.strip()]:
        m = re.match(r"(>=|<=|==|!=|~=|>|<)\s*([\w.\-+]+)", trozo)
        if not m:
            continue
        op, req = m.group(1), _version_tupla(m.group(2))
        if not req:
            continue
        if op == ">=" and not v >= req: return False
        if op == "<=" and not v <= req: return False
        if op == "==" and v[:len(req)] != req: return False
        if op == "!=" and v[:len(req)] == req: return False
        if op == ">" and not v > req: return False
        if op == "<" and not v < req: return False
        if op == "~=" and not (v >= req and v[:max(len(req) - 1, 1)] == req[:max(len(req) - 1, 1)]): return False
    return True


def _marcador_aplica(marcador: str) -> bool:
    """Evalúa un marcador de entorno (p. ej. platform_system == "Windows"). Si no se puede, se asume que aplica."""
    try:
        from packaging.markers import Marker  # presente si pip/setuptools están instalados
        return Marker(marcador).evaluate()
    except Exception:
        pass
    m = re.match(r"""\s*platform_system\s*(==|!=)\s*['"](\w+)['"]\s*$""", marcador)
    if m:
        import platform
        igual = platform.system() == m.group(2)
        return igual if m.group(1) == "==" else not igual
    m = re.match(r"""\s*sys_platform\s*(==|!=)\s*['"](\w+)['"]\s*$""", marcador)
    if m:
        igual = sys.platform == m.group(2)
        return igual if m.group(1) == "==" else not igual
    return True


def leer_requirements(ruta: Path):
    """[(nombre, extras, especificación)] de requirements.txt, ya filtrado por marcadores de entorno."""
    res = []
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.split("#", 1)[0].strip()
        if not linea or linea.startswith("-"):
            continue
        req, _, marcador = linea.partition(";")
        if marcador.strip() and not _marcador_aplica(marcador.strip()):
            continue
        m = re.match(r"([A-Za-z0-9_.\-]+)\s*(\[[^\]]*\])?\s*(.*)", req.strip())
        if m:
            extras = [e.strip() for e in (m.group(2) or "").strip("[]").split(",") if e.strip()]
            res.append((m.group(1), extras, m.group(3).strip()))
    return res


# ------------------------------------------------------------------------------------------------------------------
def comprobar_entorno() -> None:
    seccion("1) Entorno de Python")
    v = sys.version_info
    if v >= (3, 10):
        ok(f"Python {v.major}.{v.minor}.{v.micro} ({sys.executable})")
    else:
        error(f"Python {v.major}.{v.minor} es demasiado antiguo", "El proyecto necesita Python 3.10 o superior.")
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        ok(f"Entorno virtual activo: {sys.prefix}")
        try:
            Path(sys.prefix).resolve().relative_to(RAIZ)
        except ValueError:
            aviso("El entorno virtual está FUERA de la carpeta del proyecto",
                  "Si es el que usas para este proyecto, ignóralo; si no, activa el correcto (p. ej. .venv\\Scripts\\Activate.ps1).")
    else:
        error("No estás dentro de un entorno virtual",
              "Actívalo antes (PowerShell: .\\.venv\\Scripts\\Activate.ps1) y vuelve a ejecutar este script.")
    if sys.platform != "win32":
        aviso(f"Sistema '{sys.platform}': el ejecutable resultante NO sería un .exe de Windows",
              "Compila en el PC con Windows donde lo vayas a usar.")
    try:
        r = subprocess.run([sys.executable, "-m", "PyInstaller", "--version"], capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            ok(f"PyInstaller {r.stdout.strip()} instalado en este entorno")
        else:
            raise RuntimeError(r.stderr.strip())
    except Exception:
        error("PyInstaller no está instalado en este entorno", "pip install pyinstaller")


def comprobar_dependencias() -> None:
    seccion("2) Dependencias de requirements.txt (instaladas en este entorno)")
    ruta = RAIZ / "requirements.txt"
    if not ruta.is_file():
        error("Falta requirements.txt")
        return
    faltan = []
    for nombre, extras, espec in leer_requirements(ruta):
        try:
            ver = md.version(nombre)
        except md.PackageNotFoundError:
            faltan.append(nombre)
            error(f"{nombre}{' ' + espec if espec else ''}: NO instalado")
            continue
        if espec and not _cumple(ver, espec):
            error(f"{nombre} {ver} no cumple '{espec}'", f"pip install -U \"{nombre}{espec}\"")
        else:
            ok(f"{nombre} {ver}")
        # extras (p. ej. uvicorn[standard]): httptools, websockets...; son opcionales para el .exe -> un solo aviso
        if extras:
            sin_extra = []
            try:
                for r in md.requires(nombre) or []:
                    dep, _, marc = r.partition(";")
                    m_extra = re.search(r"extra\s*==\s*['\"](\w+)['\"]", marc)
                    if not m_extra or m_extra.group(1) not in extras:
                        continue
                    resto = re.sub(r"\s*and\s*extra\s*==\s*['\"]\w+['\"]|extra\s*==\s*['\"]\w+['\"]\s*(and)?", "", marc).strip()
                    if resto and not _marcador_aplica(resto):
                        continue
                    n = re.match(r"[A-Za-z0-9_.\-]+", dep.strip()).group(0)
                    try:
                        md.version(n)
                    except md.PackageNotFoundError:
                        sin_extra.append(n)
            except Exception:
                pass
            if sin_extra:
                aviso(f"{nombre}[{','.join(extras)}]: faltan extras opcionales: {', '.join(sin_extra)}",
                      "pip install -r requirements.txt  (no bloquea: uvicorn funciona sin ellos)")
    if faltan:
        print("       → Instálalo todo con:  pip install -r requirements.txt")

    # Además de "instalado", que se pueda IMPORTAR (un wheel roto o de otra versión de Python falla aquí)
    for modulo in ("fastapi", "uvicorn", "requests", "bs4", "dotenv", "certifi", "urllib3"):
        try:
            importlib.import_module(modulo)
        except Exception as e:
            error(f"No se puede importar '{modulo}': {type(e).__name__}: {e}")
    try:
        import certifi
        if Path(certifi.where()).is_file():
            ok("Certificados SSL (certifi) encontrados: se incluirán en el .exe")
        else:
            error("certifi no encuentra su cacert.pem", "pip install --force-reinstall certifi")
    except Exception:
        pass  # ya informado arriba
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo("Europe/Madrid")
        ok("Zona horaria Europe/Madrid disponible (tzdata)")
    except Exception as e:
        error(f"Falta la base de zonas horarias: {type(e).__name__}", "pip install tzdata")


def comprobar_archivos() -> None:
    seccion("3) Archivos que se empaquetan dentro del .exe")
    vendor = RAIZ / "frontend" / "vendor"
    # Rutas reales que exige backend/estaticos.VENDOR_REQUERIDOS (y que crea scripts/descargar_vendor.py)
    for rel in ("leaflet/leaflet.js", "leaflet/leaflet.css", "chart/chart.umd.js"):
        f = vendor / rel
        if f.is_file() and f.stat().st_size > 10_000:
            ok(f"frontend/vendor/{rel} ({f.stat().st_size // 1024} KB)")
        elif f.is_file():
            error(f"frontend/vendor/{rel} existe pero es sospechosamente pequeño ({f.stat().st_size} bytes)",
                  "Vuelve a ejecutar: python scripts/descargar_vendor.py")
        else:
            error(f"Falta frontend/vendor/{rel}", "Ejecuta: python scripts/descargar_vendor.py  (necesita internet)")
    imgs = vendor / "leaflet" / "images"
    if imgs.is_dir() and any(imgs.glob("marker-icon*.png")):
        ok("frontend/vendor/leaflet/images/ (iconos de los marcadores del mapa)")
    else:
        aviso("Faltan las imágenes de Leaflet (frontend/vendor/leaflet/images/)",
              "Los marcadores del mapa saldrían rotos. Ejecuta: python scripts/descargar_vendor.py")

    for rel in ("frontend/index.html", "frontend/sw.js", "frontend/manifest.json"):
        if (RAIZ / rel).is_file():
            ok(rel)
        else:
            error(f"Falta {rel}")
    idx = RAIZ / "frontend" / "index.html"
    if idx.is_file() and "__BUILD__" not in idx.read_text(encoding="utf-8", errors="replace"):
        aviso("index.html no contiene el marcador __BUILD__ (cache-busting)", "¿Es la versión correcta del fichero?")

    coords = RAIZ / "coordenadas.json"
    if not coords.is_file():
        error("Falta coordenadas.json en la raíz del proyecto")
    else:
        try:
            d = json.loads(coords.read_text(encoding="utf-8"))
            if isinstance(d, dict) and d:
                ok(f"coordenadas.json válido ({len(d)} estaciones)")
            else:
                error("coordenadas.json está vacío o no es un diccionario")
        except Exception as e:
            error(f"coordenadas.json no es JSON válido: {e}")

    if (RAIZ / "run.py").is_file():
        ok("run.py (punto de entrada)")
    else:
        error("Falta run.py")


def comprobar_codigo() -> None:
    seccion("4) Código del proyecto")
    backend = RAIZ / "backend"
    fuentes = sorted(backend.glob("*.py")) + [RAIZ / "run.py"]
    malos = 0
    with tempfile.TemporaryDirectory() as tmp:
        for f in fuentes:
            try:
                py_compile.compile(str(f), cfile=str(Path(tmp) / (f.name + "c")), doraise=True)
            except py_compile.PyCompileError as e:
                malos += 1
                error(f"Error de sintaxis en {f.relative_to(RAIZ)}: {e.msg.strip().splitlines()[-1]}")
    if not malos:
        ok(f"{len(fuentes)} ficheros .py sin errores de sintaxis")

    # Importar la aplicación de verdad (en un proceso aparte y con una carpeta de datos temporal para no tocar tus datos)
    with tempfile.TemporaryDirectory() as datos:
        env = dict(os.environ, AVAMET_DATA_DIR=datos, AVAMET_HEADLESS="1")
        try:
            r = subprocess.run([sys.executable, "-c", "import backend.main"], cwd=str(RAIZ), env=env,
                               capture_output=True, text=True, timeout=120)
            if r.returncode == 0:
                ok("La aplicación se puede importar (import backend.main)")
            else:
                ultima = (r.stderr.strip().splitlines() or ["(sin detalle)"])[-1]
                error(f"Al importar la aplicación falla: {ultima}", "Suele ser una dependencia que falta (sección 2).")
        except subprocess.TimeoutExpired:
            error("Importar la aplicación tardó más de 2 minutos")


def evaluar_spec(ruta: Path):
    """Ejecuta el .spec de verdad (con sustitutos inertes de PyInstaller) y devuelve (hiddenimports, datas, error).

    Así se acepta cualquier forma de construir la lista: escrita a mano, con os.listdir, glob.glob, bucles, etc.
    No compila nada: Analysis/PYZ/EXE son objetos vacíos que solo guardan sus argumentos, y collect_all/collect_data_files
    devuelven datos ficticios (que los paquetes estén instalados ya se comprueba en el apartado 2).
    Devuelve (None, None, mensaje) si el .spec no se pudo ejecutar.
    """
    import types

    capturado = {}

    class _Falso:
        def __init__(self, *args, **kw):
            capturado.update(kw)
            self.args, self.kw = args, kw

        def __getattr__(self, nombre):                 # a.pure, a.scripts, a.binaries, ... -> listas vacías
            if nombre.startswith("__"):
                raise AttributeError(nombre)
            return []

    def _collect_all(paquete, *a, **k):
        return [], [], [paquete]

    def _collect_data_files(paquete, *a, **k):
        return []

    def _modulo(nombre, **atributos):
        m = types.ModuleType(nombre)
        m.__dict__.update(atributos)
        return m

    falsos = {
        "PyInstaller": _modulo("PyInstaller"),
        "PyInstaller.utils": _modulo("PyInstaller.utils"),
        "PyInstaller.utils.hooks": _modulo("PyInstaller.utils.hooks", collect_all=_collect_all,
                                           collect_data_files=_collect_data_files,
                                           collect_submodules=lambda p, *a, **k: [p],
                                           collect_dynamic_libs=lambda p, *a, **k: [],
                                           copy_metadata=lambda p, *a, **k: []),
    }
    ns = {"__name__": "__spec__", "__file__": str(ruta), "SPECPATH": str(ruta.parent), "SPEC": str(ruta),
          "DISTPATH": str(RAIZ / "dist"), "WORKPATH": str(RAIZ / "build"), "CONF": {},
          "Analysis": _Falso, "PYZ": _Falso, "EXE": _Falso, "COLLECT": _Falso, "BUNDLE": _Falso,
          "MERGE": _Falso, "TOC": list, "Tree": _Falso}
    anteriores = {k: sys.modules.get(k) for k in falsos}
    cwd = os.getcwd()
    try:
        sys.modules.update(falsos)
        os.chdir(RAIZ)                                 # como al compilar: rutas relativas (glob 'backend/*.py') desde la raíz
        codigo = compile(ruta.read_text(encoding="utf-8"), str(ruta), "exec")
        exec(codigo, ns)
    except SystemExit as e:
        return None, None, f"el .spec se detiene a sí mismo con este mensaje: {e}"
    except Exception as e:                             # noqa: BLE001  (cualquier fallo del .spec se explica, no se oculta)
        return None, None, f"no se pudo ejecutar el .spec ({type(e).__name__}: {e})"
    finally:
        os.chdir(cwd)
        for k, v in anteriores.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    hi = capturado.get("hiddenimports")
    if hi is None:
        return None, None, "el .spec no llama a Analysis(..., hiddenimports=...)"
    return [str(x) for x in hi], list(capturado.get("datas") or []), None


_CACHE_SPEC = {}


def evaluar_cache() -> set:
    """hiddenimports reales del .spec (se calcula una sola vez; vacío si no se pudo ejecutar)."""
    if "hi" not in _CACHE_SPEC:
        hi, _d, _f = evaluar_spec(SPEC)
        _CACHE_SPEC["hi"] = set(hi or [])
    return _CACHE_SPEC["hi"]


def comprobar_spec() -> None:
    seccion("5) AVAMET_Monitor.spec")
    if not SPEC.is_file():
        error("Falta AVAMET_Monitor.spec")
        return
    txt = SPEC.read_text(encoding="utf-8")
    ok("AVAMET_Monitor.spec encontrado")

    # 5.1 datas / entrada
    reglas = [
        (r"\(\s*['\"]frontend['\"]\s*,\s*['\"]frontend['\"]\s*\)", "datas incluye la carpeta frontend/ (con vendor/)"),
        (r"\(\s*['\"]coordenadas\.json['\"]\s*,\s*['\"]\.['\"]\s*\)", "datas incluye coordenadas.json"),
        (r"collect_data_files\(\s*['\"]tzdata['\"]\s*\)", "datas incluye tzdata (zona horaria)"),
        (r"\[\s*['\"]run\.py['\"]\s*\]", "el punto de entrada es run.py"),
    ]
    for patron, descr in reglas:
        if re.search(patron, txt):
            ok(descr)
        else:
            error(f"El .spec NO cumple: {descr}", "Revisa AVAMET_Monitor.spec")

    # 5.2 paquetes de terceros empaquetados: collect_all('x') o _incluir('x') / entrada en el bucle de paquetes
    for paquete in ("uvicorn", "fastapi", "starlette", "pydantic", "bs4", "requests", "urllib3", "certifi"):
        if re.search(rf"['\"]{paquete}['\"]", txt) or paquete in evaluar_cache():
            ok(f"se empaqueta el paquete '{paquete}'")
        else:
            error(f"El .spec no incluye el paquete '{paquete}'", f"Añade collect_all('{paquete}') o inclúyelo en la lista de paquetes.")

    # 5.3 lista REAL de hiddenimports: se ejecuta el .spec (vale una lista escrita a mano, os.listdir, glob, bucles...)
    hidden, _datas, fallo = evaluar_spec(SPEC)
    if hidden is None:
        aviso(f"No se pudo leer la lista real de hiddenimports: {fallo}",
              "Se usa el análisis de texto del .spec como alternativa (puede dar falsos avisos si la lista se genera con código).")
    else:
        ok(f"el .spec se ejecuta correctamente y declara {len(hidden)} hiddenimports")
    conjunto = set(hidden or [])

    todos = sorted(f.stem for f in (RAIZ / "backend").glob("*.py") if f.stem != "__init__")
    validos = [m for m in todos if m.isidentifier()]
    raros = [m for m in todos if not m.isidentifier()]
    if raros:
        aviso("En backend/ hay ficheros .py con nombre no válido como módulo (¿copias tipo 'config (1).py'?): " + ", ".join(raros),
              "No se pueden importar y no hacen falta: bórralos (el .spec los ignora o los lista sin efecto).")

    if hidden is not None:
        sin = [m for m in validos if f"backend.{m}" not in conjunto]
    else:
        sin = [m for m in validos if not re.search(rf"['\"]backend\.{m}['\"]", txt)]
        if re.search(r"os\.listdir\([^)]*['\"]backend['\"]", txt) or re.search(r"glob(?:\.glob)?\([^)]*backend", txt):
            sin = []                                    # alternativa solo de texto: se da por buena la generación automática
    if sin:
        mostrar = sorted(x for x in conjunto if "backend" in x)[:4]
        error("Módulos de backend/ que faltan en hiddenimports: " + ", ".join(sin),
              "El .spec debe añadir 'backend.<módulo>' para cada uno." + (f" Ejemplos de lo que genera ahora: {mostrar}" if mostrar else ""))
    else:
        ok(f"hiddenimports incluye los {len(validos)} módulos de backend/")

    # 5.4 hiddenimports de uvicorn (carga dinámica) y otros
    for hi in ("uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto", "uvicorn.lifespan.on", "tzdata", "dotenv"):
        if hi in conjunto or re.search(rf"['\"]{re.escape(hi)}['\"]", txt):
            ok(f"hiddenimport '{hi}'")
        else:
            error(f"Falta el hiddenimport '{hi}' en el .spec")

    # 5.5 el .exe no debe abrir consola, pero tampoco perder los errores (run.py redirige a error_log.txt)
    m = re.search(r"console\s*=\s*(True|False)", txt)
    if m:
        ok(f"console={m.group(1)}" + (" (sin ventana; los errores van a error_log.txt)" if m.group(1) == "False" else ""))

    # 5.6 imports de terceros del código que NO están cubiertos por el .spec ni por la biblioteca estándar
    import ast
    std = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else set()
    propios = {"backend"}
    cubiertos = set(re.findall(r"['\"]([A-Za-z0-9_]+)(?:\.[\w.]+)?['\"]", txt))
    nuevos = set()
    for f in list((RAIZ / "backend").glob("*.py")) + [RAIZ / "run.py"]:
        try:
            arbol = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for n in ast.walk(arbol):
            nombres = []
            if isinstance(n, ast.Import):
                nombres = [a.name.split(".")[0] for a in n.names]
            elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                nombres = [n.module.split(".")[0]]
            for nom in nombres:
                if nom not in std and nom not in propios and nom not in cubiertos:
                    nuevos.add(nom)
    if nuevos:
        aviso("El código importa paquetes que el .spec no menciona: " + ", ".join(sorted(nuevos)),
              "PyInstaller suele detectarlos solo; si el .exe falla con 'No module named ...', añádelos al .spec.")
    else:
        ok("Todos los paquetes de terceros que importa el código están cubiertos por el .spec")


def comprobar_opcionales() -> None:
    seccion("6) Opcionales (solo avisos)")
    if (RAIZ / "icono.ico").is_file():
        ok("icono.ico encontrado: el .exe llevará tu icono")
    else:
        aviso("No hay icono.ico en la raíz", "El .exe se compilará con el icono por defecto. Copia tu icono.ico junto al .spec si lo quieres.")
    candidatos = [RAIZ / ".env"]
    appdata = os.getenv("APPDATA")
    if appdata:
        candidatos.append(Path(appdata) / "AVAMETMonitor" / ".env")
    hallado = next((c for c in candidatos if c.is_file()), None)
    if hallado:
        ok(f".env encontrado: {hallado}")
    else:
        aviso("No hay archivo .env",
              "No impide compilar (el .exe NO lo incluye). Se necesita al EJECUTAR para Telegram: ver el botón ⚙️ Configuración de la app.")
    if (RAIZ / "build").is_dir() or (RAIZ / "dist").is_dir():
        aviso("Existen carpetas build/ o dist/ de una compilación anterior", "Usa --clean (ya está en el comando) para no arrastrar restos.")


def main() -> int:
    print("=" * 66)
    print(" AVAMET Monitor — comprobación antes de compilar")
    print(f" Proyecto: {RAIZ}")
    print("=" * 66)
    comprobar_entorno()
    comprobar_dependencias()
    comprobar_archivos()
    comprobar_codigo()
    comprobar_spec()
    comprobar_opcionales()

    print("\n" + "=" * 66)
    print(f" Resumen: {len(ERRORES)} error(es), {len(AVISOS)} aviso(s)")
    if ERRORES:
        print("\n❌ ERROR: Corrige lo anterior antes de compilar\n")
        for e in ERRORES:
            print(f"   • {e}")
        return 1
    print("\n✅ TODO LISTO PARA COMPILAR\n")
    print("   Ejecuta, desde esta carpeta y con el entorno virtual activo:\n")
    print("       python -m PyInstaller --clean AVAMET_Monitor.spec\n")
    print("   El resultado será:  dist\\AVAMET_Monitor.exe")
    return 0


if __name__ == "__main__":
    sys.exit(main())
