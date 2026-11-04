"""Salida inmediata del proceso. Ejecutar: pytest -v   o   python tests/test_apagado.py"""
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from backend import apagado   # noqa: E402


def _lanzar(codigo_extra: str, timeout=15):
    codigo = (
        "import sys, threading, time\n"
        f"sys.path.insert(0, {str(RAIZ)!r})\n"
        "from backend import apagado\n" + codigo_extra)
    t0 = time.time()
    r = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True, timeout=timeout)
    return r, time.time() - t0


def test_sale_ya_con_hilos_bloqueados_y_no_demonio():
    r, dt = _lanzar(
        "threading.Thread(target=time.sleep, args=(30,)).start()\n"      # hilo NO demonio bloqueado
        "threading.Thread(target=time.sleep, args=(30,), daemon=True).start()\n"
        "def trabajo():\n"
        "    time.sleep(0.2); apagado.terminar_ya(0)\n"
        "threading.Thread(target=trabajo).start()\n"                      # como el background task de /api/shutdown
        "time.sleep(30)\n")
    assert r.returncode == 0 and dt < 5, (r.returncode, dt, r.stderr)


def test_respeta_el_codigo_de_salida():
    r, dt = _lanzar("apagado.terminar_ya(3)\n")
    assert r.returncode == 3


def test_vuelca_la_salida_pendiente_antes_de_morir():
    r, _ = _lanzar("print('ultimo mensaje'); apagado.terminar_ya(0)\n")   # stdout a pipe: con buffer
    assert "ultimo mensaje" in r.stdout


def test_ruta_windows_llama_a_terminateprocess():
    llamadas = []

    class Fn:
        def __init__(self, nombre, valor=None): self.nombre, self.valor = nombre, valor
        def __call__(self, *a): llamadas.append((self.nombre, a)); return self.valor

    class Kernel32:
        GetCurrentProcess = Fn("GetCurrentProcess", -1)
        TerminateProcess = Fn("TerminateProcess", 1)

    apagado._terminar_windows(Kernel32, 7)
    assert llamadas == [("GetCurrentProcess", ()), ("TerminateProcess", (-1, 7))]
    assert Kernel32.TerminateProcess.argtypes is not None      # tipos explícitos (HANDLE de 64 bits)


if __name__ == "__main__":
    fallos = 0
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            try: f(); print("OK  ", n)
            except Exception as e: fallos += 1; print("FALLA", n, repr(e))
    sys.exit(1 if fallos else 0)
