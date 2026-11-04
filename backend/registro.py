"""Fichero de registro con salida línea a línea y rotación por tamaño (M9).

Sustituye a open(LOG_FILE, "a") sin más: allí el log crecía sin límite y, al ir con buffer, un cierre brusco
(TerminateProcess / os._exit) perdía las últimas líneas.
"""
import os
from pathlib import Path


class FicheroRotativo:
    """Objeto tipo fichero para sys.stdout / sys.stderr. Cada write() se vuelca a disco; al pasar de
    `max_bytes` se rota: registro.txt -> registro.txt.1 -> .2 ... (se conservan `copias`)."""

    def __init__(self, ruta, max_bytes: int = 1_000_000, copias: int = 3):
        self.ruta, self.max_bytes, self.copias = Path(ruta), max_bytes, copias
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        self._f = None
        self._abrir()
        if self._tam() > self.max_bytes:      # un log heredado ya grande se rota al arrancar
            self._rotar()

    def _abrir(self):
        self._f = open(self.ruta, "a", encoding="utf-8", errors="replace", buffering=1)

    def _tam(self) -> int:
        try: return self.ruta.stat().st_size
        except OSError: return 0

    def _rotar(self):
        try: self._f.close()
        except Exception: pass                # noqa: BLE001
        for i in range(self.copias, 0, -1):
            origen = self.ruta if i == 1 else self.ruta.with_name(f"{self.ruta.name}.{i - 1}")
            destino = self.ruta.with_name(f"{self.ruta.name}.{i}")
            try:
                if origen.exists(): os.replace(origen, destino)
            except OSError:
                pass
        self._abrir()

    def write(self, texto):
        try:
            n = self._f.write(texto)
            self._f.flush()
            if self._tam() > self.max_bytes:
                self._rotar()
            return n
        except Exception:                     # noqa: BLE001 - registrar nunca debe tumbar la aplicación
            return 0

    def flush(self):
        try: self._f.flush()
        except Exception: pass                # noqa: BLE001

    def isatty(self): return False
    def fileno(self): return self._f.fileno()
    @property
    def encoding(self): return "utf-8"
