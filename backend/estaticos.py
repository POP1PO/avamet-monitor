"""Ficheros del frontend: identificador de versión (cache-busting) y acceso seguro a frontend/vendor/.

Módulo puro (sin FastAPI) para poder probarlo.

Cache-busting: BUILD_ID es un hash del contenido de index.html, sw.js, manifest.json y de los ficheros de vendor/
(más la versión de la app). Se sustituye el marcador __BUILD__ en index.html y sw.js al servirlos:
  - sw.js cambia de contenido cuando cambia cualquier fichero del frontend -> el navegador instala el nuevo
    Service Worker, éste borra las cachés antiguas y la página se recarga sola;
  - las URL de vendor/ llevan ?v=BUILD_ID -> el navegador no reutiliza una librería antigua.
No hay que acordarse de subir ningún número a mano.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

MARCADOR = "__BUILD__"
_EXT_VENDOR = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".png": "image/png",
               ".txt": "text/plain; charset=utf-8", ".md": "text/plain; charset=utf-8"}

# Librerías que debe haber en frontend/vendor/ (las descarga scripts/descargar_vendor.py)
VENDOR_REQUERIDOS = ("leaflet/leaflet.js", "leaflet/leaflet.css", "chart/chart.umd.js")


class Frontend:
    def __init__(self, carpeta: Path, version_app: str = ""):
        self.carpeta = Path(carpeta)
        self.vendor = self.carpeta / "vendor"
        self.version_app = version_app
        self._firma = None
        self._build = ""

    def _ficheros(self) -> list[Path]:
        base = [self.carpeta / n for n in ("index.html", "sw.js", "manifest.json")]
        extra = sorted(p for p in self.vendor.rglob("*") if p.is_file()) if self.vendor.is_dir() else []
        return [p for p in base + extra if p.is_file()]

    def build_id(self) -> str:
        """Se recalcula solo si cambia algún fichero (tamaño o fecha): barato en cada petición."""
        ficheros = self._ficheros()
        firma = tuple((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in ficheros)
        if firma != self._firma:
            h = hashlib.sha1(self.version_app.encode("utf-8"))
            for p in ficheros:
                h.update(str(p.relative_to(self.carpeta)).encode("utf-8"))
                h.update(p.read_bytes())
            self._build, self._firma = h.hexdigest()[:10], firma
        return self._build

    def render(self, nombre: str) -> str:
        """Devuelve index.html / sw.js con __BUILD__ sustituido por el BUILD_ID actual."""
        texto = (self.carpeta / nombre).read_text(encoding="utf-8")
        return texto.replace(MARCADOR, self.build_id())

    def resolver_vendor(self, ruta: str) -> tuple[Path, str] | None:
        """Ruta pedida (/vendor/<ruta>) -> (fichero, content-type), o None si no existe o no está permitida.
        Impide salir de frontend/vendor/ (../, rutas absolutas, enlaces simbólicos) y limita las extensiones."""
        if not ruta or "\x00" in ruta or "\\" in ruta:
            return None
        tipo = _EXT_VENDOR.get(Path(ruta).suffix.lower())
        if tipo is None or not self.vendor.is_dir():
            return None
        try:
            base = self.vendor.resolve()
            destino = (base / ruta).resolve()
            destino.relative_to(base)
        except (ValueError, OSError):
            return None
        return (destino, tipo) if destino.is_file() else None

    def vendor_faltante(self) -> list[str]:
        return [r for r in VENDOR_REQUERIDOS if not (self.vendor / r).is_file()]
