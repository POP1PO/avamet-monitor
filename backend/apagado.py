"""Salida inmediata del proceso (sin esperar a hilos, sin cierre ordenado).

Se usa SOLO después de haber dejado todo en orden (p. ej. el fichero Boltwood en APAGADO).

- Linux/macOS: os._exit(codigo). No espera a hilos ni ejecuta limpieza; el código de salida se respeta
  (systemd con Restart=on-failure NO reinicia con código 0, y por eso aquí no se usa SIGKILL).
- Windows: TerminateProcess sobre el propio proceso. os._exit en Windows acaba llamando a ExitProcess, que
  notifica a las DLL de cada hilo (DLL_PROCESS_DETACH) y puede quedarse esperando si algún hilo está bloqueado
  dentro de una llamada de red; TerminateProcess no espera a nadie. Si algo falla, se usa os._exit.
"""
import os
import sys


def _terminar_windows(kernel32, codigo: int) -> None:
    """Separada para poder probarla con un kernel32 simulado. Si funciona, no vuelve."""
    from ctypes import wintypes
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.TerminateProcess(kernel32.GetCurrentProcess(), codigo)


def terminar_ya(codigo: int = 0) -> None:
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.flush()
        except Exception:
            pass
    if sys.platform == "win32":
        try:
            import ctypes
            _terminar_windows(ctypes.windll.kernel32, codigo)
        except Exception:
            pass                       # si no se pudo, se cae al os._exit de abajo
    os._exit(codigo)
