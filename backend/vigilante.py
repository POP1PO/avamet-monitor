"""Vigilante del bucle principal (A6): que un equipo desatendido se recupere solo.

`supervisar()` ejecuta una corrutina "para siempre". Si termina o lanza CUALQUIER excepción (p. ej. sin red al
arrancar, un fallo inesperado fuera del try del bucle), avisa con `al_fallar`, espera con espera creciente
(5 s, 10 s, 20 s ... máx. 5 min) y la vuelve a lanzar. Si la ejecución anterior duró lo bastante (`estable_s`),
la espera vuelve a empezar desde el principio. La cancelación (apagado de la app) siempre se respeta.

Separado de main.py para poder probarlo sin red ni FastAPI.
"""
from __future__ import annotations

import asyncio
import time


class TerminoInesperado(RuntimeError):
    """La corrutina supervisada terminó sin error (debería ser infinita)."""


async def supervisar(nombre: str, fabrica, al_fallar, *, espera_inicial_s: float = 5, espera_max_s: float = 300,
                     estable_s: float = 300, dormir=asyncio.sleep, reloj=time.monotonic) -> None:
    """`fabrica()` crea la corrutina; `al_fallar(exc, intento, espera_s)` es async y NO debe lanzar
    (si lo hace, se registra y se sigue)."""
    espera = espera_inicial_s
    intento = 0
    while True:
        t0 = reloj()
        try:
            await fabrica()
            error: BaseException = TerminoInesperado(f"{nombre} terminó inesperadamente")
        except asyncio.CancelledError:
            raise
        except Exception as e:                       # noqa: BLE001 - cualquier fallo se reintenta
            error = e
        if reloj() - t0 >= estable_s:
            espera, intento = espera_inicial_s, 0
        intento += 1
        try:
            await al_fallar(error, intento, espera)
        except asyncio.CancelledError:
            raise
        except Exception as e2:                      # noqa: BLE001 - el aviso del fallo no puede matar al vigilante
            print(f"Vigilante: error al registrar el fallo de {nombre} ({type(e2).__name__}: {e2})")
        await dormir(espera)
        espera = min(espera * 2, espera_max_s)
