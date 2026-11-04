"""Cuándo y qué avisar por Telegram. Separado de main.py para poder probarlo sin red.

Avisos:
  - PELIGRO: una vez al entrar (no se repite hasta que el nivel vuelva a SEGURO).
  - SIN_DATOS: una vez si dura al menos `demora_sin_datos_s`; al terminar, un aviso de "datos recuperados".

Entrega: si Telegram no es alcanzable (p. ej. se ha caído internet en el observatorio, que es
justo cuando más probable es el SIN_DATOS), el mensaje NO se pierde: queda en cola y se reintenta en
cada ciclo hasta que llegue (máx. `max_cola` mensajes, caducan a las `caducidad_s`). Los que llegan tarde
llevan una nota con la hora real del suceso.
"""
from __future__ import annotations

import asyncio
import html
import time
from datetime import datetime


def _hora(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


class GestorNotificaciones:
    def __init__(self, enviar, demora_sin_datos_s: float = 180, max_cola: int = 20,
                 caducidad_s: float = 6 * 3600, umbral_retraso_s: float = 120):
        """`enviar(texto) -> (enviados, destinatarios)` es síncrona (se ejecuta en un hilo)."""
        self.enviar = enviar
        self.demora_sin_datos_s = demora_sin_datos_s
        self.max_cola = max_cola
        self.caducidad_s = caducidad_s
        self.umbral_retraso_s = umbral_retraso_s
        self._peligro_notificado = False
        self._sin_datos_desde: float | None = None
        self._sin_datos_notificado = False
        self._cola: list[tuple[float, str]] = []

    @property
    def pendientes(self) -> int:
        return len(self._cola)

    def reiniciar_peligro(self) -> None:
        """Al cambiar de estación: un PELIGRO en la nueva debe avisar de nuevo."""
        self._peligro_notificado = False

    async def procesar(self, nivel: str, alertas: list[dict] | None = None, estacion: str = "",
                       motivo: str = "", ahora: float | None = None) -> None:
        ahora = time.time() if ahora is None else ahora
        est = html.escape(estacion or "la estación")
        nuevos: list[str] = []

        if nivel == "SIN_DATOS":
            if self._sin_datos_desde is None:
                self._sin_datos_desde = ahora
            if not self._sin_datos_notificado and ahora - self._sin_datos_desde >= self.demora_sin_datos_s:
                mins = max(1, round((ahora - self._sin_datos_desde) / 60))
                nuevos.append(
                    f"🔌 <b>SIN DATOS en {est.upper()}</b>\n"
                    f"Llevamos {mins} min sin datos fiables. Se considera <b>NO SEGURO</b> "
                    f"(Alpaca y el fichero Boltwood indican no seguro).\n"
                    f"Motivo: {html.escape(motivo or 'desconocido')}\n"
                    f"<i>{_hora(self._sin_datos_desde)}</i>")
                self._sin_datos_notificado = True
        else:
            if self._sin_datos_desde is not None:
                if self._sin_datos_notificado:
                    mins = max(1, round((ahora - self._sin_datos_desde) / 60))
                    nuevos.append(
                        f"✅ <b>Datos recuperados en {est.upper()}</b>\n"
                        f"Tras {mins} min sin datos. Estado actual: <b>{html.escape(nivel)}</b>.\n"
                        f"<i>{_hora(ahora)}</i>")
                self._sin_datos_desde = None
                self._sin_datos_notificado = False
            if nivel == "SEGURO":
                self._peligro_notificado = False
            elif nivel == "PELIGRO" and not self._peligro_notificado:
                lineas = [f"⚠️ <b>ALERTA EN {est.upper()}</b>"]
                for a in (alertas or []):
                    lineas.append(f"• {html.escape(str(a.get('mensaje', '')))}")
                lineas.append(f"\n<i>{_hora(ahora)}</i>")
                nuevos.append("\n".join(lineas))
                self._peligro_notificado = True

        for m in nuevos:
            self._cola.append((ahora, m))
        self._cola = self._cola[-self.max_cola:]
        await self._vaciar(ahora)

    async def _vaciar(self, ahora: float) -> None:
        self._cola = [(ts, m) for ts, m in self._cola if ahora - ts < self.caducidad_s]
        while self._cola:
            ts, texto = self._cola[0]
            if ahora - ts > self.umbral_retraso_s:
                texto += f"\n<i>⏱ Aviso enviado con retraso: el suceso es de las {_hora(ts)}</i>"
            try:
                # requests es síncrono: fuera del bucle de eventos para no bloquear SSE ni Alpaca
                enviados, total = await asyncio.to_thread(self.enviar, texto)
            except Exception as e:                     # un fallo de Telegram NUNCA debe afectar al estado
                print(f"Telegram: fallo inesperado al enviar ({type(e).__name__}); se reintentará")
                return
            if total == 0 or enviados > 0:             # entregado, o no hay nadie suscrito / bot desactivado
                self._cola.pop(0)
            else:                                      # Telegram no alcanzable: se reintenta en el próximo ciclo
                return
