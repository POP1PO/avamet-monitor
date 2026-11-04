"""Detección de "ya hay una instancia de AVAMET Monitor en ese puerto" (M9).

Antes bastaba con que el puerto estuviera ocupado para abrir el navegador, aunque lo ocupara OTRA aplicación
(otro servicio en el 8000). Ahora se le pregunta por su identidad: AVAMET Monitor responde a
/management/v1/description (endpoint ASCOM Alpaca de gestión) con ServerName = "AVAMET Monitor".
"""
import time

import requests

NOMBRE_SERVIDOR = "AVAMET Monitor"


def host_de_prueba(host: str) -> str:
    return "127.0.0.1" if host in ("0.0.0.0", "", "::") else host


def es_instancia_avamet(host: str, port: int, timeout: float = 2.0, intentos: int = 3, pausa_s: float = 0.7) -> bool:
    """True solo si en host:port responde AVAMET Monitor. Cualquier error o respuesta distinta -> False.
    Reintenta unas veces: una instancia recién lanzada ya tiene el puerto abierto pero aún no atiende peticiones."""
    for i in range(max(1, intentos)):
        try:
            r = requests.get(f"http://{host_de_prueba(host)}:{port}/management/v1/description", timeout=timeout)
            if r.ok and (r.json().get("Value") or {}).get("ServerName") == NOMBRE_SERVIDOR:
                return True
        except Exception:                    # noqa: BLE001 - no responde / no es JSON / no es nuestra
            pass
        if i < intentos - 1: time.sleep(pausa_s)
    return False
