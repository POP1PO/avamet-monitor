#!/usr/bin/env python3
"""Descarga Leaflet y Chart.js a frontend/vendor/ para que la interfaz funcione SIN internet (Raspberry Pi).

Uso (desde la carpeta del proyecto, en cualquier sistema; solo usa la biblioteca estándar de Python):
    python scripts/descargar_vendor.py
    python scripts/descargar_vendor.py --leaflet 1.9.4 --chart 4.4.1
    python scripts/descargar_vendor.py --solo-comprobar        # no descarga: indica qué falta

Descarga desde cdn.jsdelivr.net (paquetes npm con versión FIJA), comprueba que cada fichero es el esperado
(tamaño y cabecera con el nombre y la versión) y escribe frontend/vendor/VERSIONS.txt con la huella SHA-256.
Después commitea frontend/vendor/ en tu repositorio: la Raspberry Pi ya no necesita internet para el mapa ni las gráficas.
"""
import argparse
import hashlib
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CDN = "https://cdn.jsdelivr.net/npm"
RAIZ = Path(__file__).resolve().parent.parent
DESTINO = RAIZ / "frontend" / "vendor"
UA = {"User-Agent": "AVAMET-Monitor-vendor/1.0"}
# Mapa y marcadores de Leaflet: el CSS los pide en images/ (rutas relativas)
IMAGENES_LEAFLET = ("layers.png", "layers-2x.png", "marker-icon.png", "marker-icon-2x.png", "marker-shadow.png")


def descargar(url: str, intentos: int = 3, timeout: int = 60) -> bytes:
    ultimo = None
    for n in range(1, intentos + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:     # respeta HTTPS_PROXY del entorno
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise                                                   # no tiene sentido reintentar
            ultimo = e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            ultimo = e
        time.sleep(1.5 * n)
    raise RuntimeError(f"no se pudo descargar {url}: {ultimo}")


def verificar_cabecera(nombre: str, datos: bytes, clave: str, version: str) -> None:
    """Comprueba que es la librería y versión pedidas (busca el nombre y la versión en la cabecera)."""
    if len(datos) < 10_000:
        raise ValueError(f"{nombre}: tamaño sospechoso ({len(datos)} bytes)")
    cab = datos[:3000].decode("utf-8", "replace").lower()
    if clave.lower() not in cab or version not in cab:
        raise ValueError(f"{nombre}: la cabecera no menciona '{clave}' y '{version}'; puede no ser el fichero esperado")


def guardar(ruta: Path, datos: bytes) -> str:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_name(ruta.name + ".tmp")
    tmp.write_bytes(datos)
    os.replace(tmp, ruta)
    return hashlib.sha256(datos).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--leaflet", default="1.9.4", help="versión de Leaflet (por defecto 1.9.4)")
    ap.add_argument("--chart", default="4.4.1", help="versión de Chart.js (por defecto 4.4.1)")
    ap.add_argument("--cdn", default=CDN, help=argparse.SUPPRESS)       # para pruebas con un servidor local
    ap.add_argument("--destino", default=str(DESTINO), help=argparse.SUPPRESS)
    ap.add_argument("--solo-comprobar", action="store_true", help="no descarga; muestra qué falta")
    ap.add_argument("--no-verificar", action="store_true", help="no comprobar la cabecera de los ficheros")
    a = ap.parse_args(argv)
    destino = Path(a.destino)

    requeridos = ["leaflet/leaflet.js", "leaflet/leaflet.css", "chart/chart.umd.js"] + [f"leaflet/images/{i}" for i in IMAGENES_LEAFLET]
    if a.solo_comprobar:
        faltan = [r for r in requeridos if not (destino / r).is_file()]
        print("Falta: " + ", ".join(faltan) if faltan else "frontend/vendor/ completo.")
        return 1 if faltan else 0

    L = f"{a.cdn}/leaflet@{a.leaflet}"
    C = f"{a.cdn}/chart.js@{a.chart}"
    # (url, ruta destino, (clave, versión) para verificar o None, obligatorio)
    plan = [(f"{L}/dist/leaflet.js", "leaflet/leaflet.js", ("leaflet", a.leaflet), True),
            (f"{L}/dist/leaflet.css", "leaflet/leaflet.css", None, True)]
    plan += [(f"{L}/dist/images/{i}", f"leaflet/images/{i}", None, True) for i in IMAGENES_LEAFLET]
    plan += [(f"{L}/LICENSE", "leaflet/LICENSE.txt", None, False),
             (f"{C}/dist/chart.umd.js", "chart/chart.umd.js", ("chart.js", a.chart), True),
             (f"{C}/LICENSE.md", "chart/LICENSE.md", None, False)]

    errores, filas = [], []
    for url, rel, verif, obligatorio in plan:
        try:
            try:
                datos = descargar(url)
            except urllib.error.HTTPError as e:
                if rel == "chart/chart.umd.js" and e.code == 404:       # algunas versiones lo llaman .min.js
                    url = url.replace("chart.umd.js", "chart.umd.min.js")
                    datos = descargar(url)
                else:
                    raise
            if verif and not a.no_verificar:
                verificar_cabecera(rel, datos, *verif)
            sha = guardar(destino / rel, datos)
            filas.append((rel, url, len(datos), sha))
            print(f"  OK  {rel}  ({len(datos):,} bytes)")
        except Exception as e:                                          # noqa: BLE001
            if obligatorio:
                errores.append(f"{rel}: {e}")
                print(f"  ERROR {rel}: {e}")
            else:
                print(f"  aviso: {rel} no descargado ({e}); no es imprescindible")

    if errores:
        print(f"\nFaltan {len(errores)} fichero(s) obligatorio(s). Revisa la conexión/proxy y repite.", file=sys.stderr)
        return 1
    lineas = [f"Descargado el {time.strftime('%Y-%m-%d %H:%M:%S')} con scripts/descargar_vendor.py",
              f"Leaflet {a.leaflet} (BSD-2-Clause) · Chart.js {a.chart} (MIT)", ""]
    lineas += [f"{rel}\n  {url}\n  {n} bytes  sha256 {sha}" for rel, url, n, sha in filas]
    guardar(destino / "VERSIONS.txt", ("\n".join(lineas) + "\n").encode("utf-8"))
    print(f"\nListo: {destino}\nAhora commitea frontend/vendor/ y reinicia la aplicación.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
