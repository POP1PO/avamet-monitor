"""A6: el vigilante reintenta con espera creciente. Ejecutar: pytest -v   o   python tests/test_vigilante.py"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.vigilante import TerminoInesperado, supervisar   # noqa: E402


class Reloj:
    """Reloj y 'sleep' simulados: el tiempo solo avanza cuando el vigilante duerme o la tarea 'trabaja'."""
    def __init__(self): self.t = 0.0; self.esperas = []
    def __call__(self): return self.t
    async def dormir(self, s): self.esperas.append(s); self.t += s; await asyncio.sleep(0)


def _correr(fabrica, max_fallos, **kw):
    reloj = Reloj(); fallos = []
    class Fin(Exception): pass
    async def al_fallar(exc, intento, espera):
        fallos.append((type(exc).__name__, intento, espera))
        if len(fallos) >= max_fallos: raise asyncio.CancelledError   # corta el bucle infinito en la prueba
    async def main():
        try:
            await supervisar("prueba", lambda: fabrica(reloj), al_fallar, dormir=reloj.dormir, reloj=reloj, **kw)
        except asyncio.CancelledError:
            pass
    asyncio.run(main())
    return fallos, reloj


def test_espera_creciente_con_tope():
    async def siempre_falla(reloj): raise OSError("sin red")
    fallos, reloj = _correr(siempre_falla, 8, espera_inicial_s=5, espera_max_s=60)
    assert [f[2] for f in fallos] == [5, 10, 20, 40, 60, 60, 60, 60]
    assert [f[1] for f in fallos] == list(range(1, 9)) and fallos[0][0] == "OSError"


def test_reintenta_hasta_que_funciona_y_sigue_corriendo():
    intentos = []
    async def a_la_tercera(reloj):
        intentos.append(1)
        if len(intentos) < 3: raise OSError("sin red")
        await asyncio.sleep(3600)             # "bucle sano": no termina; el test lo cancela
    reloj = Reloj(); fallos = []
    async def al_fallar(e, i, w): fallos.append(i)
    async def main():
        t = asyncio.create_task(supervisar("p", lambda: a_la_tercera(reloj), al_fallar, dormir=reloj.dormir, reloj=reloj))
        await asyncio.sleep(0.05); t.cancel()
        try: await t
        except asyncio.CancelledError: return "cancelado"
    assert asyncio.run(main()) == "cancelado"
    assert len(intentos) == 3 and fallos == [1, 2]


def test_terminar_sin_error_cuenta_como_fallo():
    async def termina(reloj): return None
    fallos, _ = _correr(termina, 2)
    assert [f[0] for f in fallos] == ["TerminoInesperado", "TerminoInesperado"]


def test_espera_se_reinicia_tras_ejecucion_estable():
    fases = []
    async def estable_y_luego_falla(reloj):
        fases.append(1)
        if len(fases) in (3, 4):
            reloj.t += 1000                    # esta ejecución duró mucho: era estable
        raise OSError("x")
    fallos, _ = _correr(estable_y_luego_falla, 6, espera_inicial_s=5, espera_max_s=300, estable_s=300)
    # fallos 3 y 4 vienen de ejecuciones estables (>= 300 s): la espera y el intento vuelven a 5 s / 1
    assert [f[2] for f in fallos] == [5, 10, 5, 5, 10, 20]
    assert [f[1] for f in fallos] == [1, 2, 1, 1, 2, 3]


def test_error_en_al_fallar_no_mata_al_vigilante():
    n = []
    async def falla(): raise ValueError("x")
    async def mal_aviso(e, i, w):
        n.append(i)
        if i >= 3: raise asyncio.CancelledError
        raise RuntimeError("telegram roto")
    async def dormir(s): await asyncio.sleep(0)
    async def main():
        try: await supervisar("p", falla, mal_aviso, dormir=dormir)
        except asyncio.CancelledError: pass
    asyncio.run(main())
    assert n == [1, 2, 3]


def test_cancelacion_se_respeta():
    async def eterno(): await asyncio.sleep(3600)
    async def al_fallar(*a): raise AssertionError("no debe avisar al cancelar")
    async def main():
        t = asyncio.create_task(supervisar("p", eterno, al_fallar)); await asyncio.sleep(0.05); t.cancel()
        try: await t
        except asyncio.CancelledError: return True
    assert asyncio.run(main())


if __name__ == "__main__":
    fallos = 0
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            try: f(); print("OK  ", n)
            except Exception as e: fallos += 1; print("FALLA", n, repr(e))
    sys.exit(1 if fallos else 0)
