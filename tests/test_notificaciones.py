"""A4: avisos PELIGRO / SIN_DATOS / recuperado y cola de reintentos. Ejecutar: pytest -v   o   python tests/test_notificaciones.py"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.notificaciones import GestorNotificaciones   # noqa: E402


class Fake:
    def __init__(self, res=(1, 1)): self.res = res; self.textos = []
    def __call__(self, t): self.textos.append(t); return self.res


def run(g, nivel, ahora, alertas=None, motivo=""):
    asyncio.run(g.procesar(nivel, alertas or [], "Almassora", motivo, ahora))


def test_sin_datos_espera_la_demora():
    f = Fake(); g = GestorNotificaciones(f, demora_sin_datos_s=180)
    run(g, "SIN_DATOS", 1000, motivo="DATO_CADUCADO")
    run(g, "SIN_DATOS", 1100, motivo="DATO_CADUCADO")
    assert f.textos == []
    run(g, "SIN_DATOS", 1200, motivo="DATO_CADUCADO")
    assert len(f.textos) == 1 and "SIN DATOS" in f.textos[0] and "DATO_CADUCADO" in f.textos[0]


def test_sin_datos_una_sola_vez_por_episodio():
    f = Fake(); g = GestorNotificaciones(f, demora_sin_datos_s=0)
    for t in (0, 60, 120, 180): run(g, "SIN_DATOS", t)
    assert len(f.textos) == 1


def test_parpadeo_corto_no_avisa_ni_recupera():
    f = Fake(); g = GestorNotificaciones(f, demora_sin_datos_s=180)
    run(g, "SIN_DATOS", 0); run(g, "SEGURO", 60)
    assert f.textos == []


def test_recuperacion_avisa_tras_aviso_de_corte():
    f = Fake(); g = GestorNotificaciones(f, demora_sin_datos_s=0)
    run(g, "SIN_DATOS", 0); run(g, "SEGURO", 300)
    assert len(f.textos) == 2 and "recuperados" in f.textos[1]
    run(g, "SIN_DATOS", 400)            # nuevo episodio => nuevo aviso
    assert len(f.textos) == 3


def test_peligro_una_vez_hasta_volver_a_seguro():
    f = Fake(); g = GestorNotificaciones(f)
    al = [{"mensaje": "Lluvia <detectada>"}]
    run(g, "PELIGRO", 0, al); run(g, "PELIGRO", 60, al)
    assert len(f.textos) == 1 and "&lt;detectada&gt;" in f.textos[0]
    run(g, "SEGURO", 120); run(g, "PELIGRO", 180, al)
    assert len(f.textos) == 2


def test_peligro_tras_sin_datos_no_repite_si_no_hubo_seguro():
    f = Fake(); g = GestorNotificaciones(f, demora_sin_datos_s=0)
    run(g, "PELIGRO", 0, [{"mensaje": "x"}]); run(g, "SIN_DATOS", 60); run(g, "PELIGRO", 120, [{"mensaje": "x"}])
    assert sum("ALERTA" in t for t in f.textos) == 1


def test_reiniciar_peligro():
    f = Fake(); g = GestorNotificaciones(f)
    run(g, "PELIGRO", 0, [{"mensaje": "x"}]); g.reiniciar_peligro(); run(g, "PELIGRO", 10, [{"mensaje": "x"}])
    assert len(f.textos) == 2


def test_cola_reintenta_sin_internet_y_marca_retraso():
    f = Fake(res=(0, 2)); g = GestorNotificaciones(f, demora_sin_datos_s=0)
    run(g, "SIN_DATOS", 0, motivo="x")
    assert g.pendientes == 1
    run(g, "SIN_DATOS", 60); assert g.pendientes == 1
    f.res = (2, 2)                                  # vuelve internet
    run(g, "SIN_DATOS", 600)
    assert g.pendientes == 0 and "con retraso" in f.textos[-1]


def test_sin_suscriptores_no_se_acumula():
    f = Fake(res=(0, 0)); g = GestorNotificaciones(f, demora_sin_datos_s=0)
    run(g, "SIN_DATOS", 0)
    assert g.pendientes == 0


def test_caducidad_y_tope_de_cola():
    f = Fake(res=(0, 1)); g = GestorNotificaciones(f, demora_sin_datos_s=0, caducidad_s=100, max_cola=2)
    run(g, "PELIGRO", 0, [{"mensaje": "a"}]); assert g.pendientes == 1
    run(g, "SIN_DATOS", 500); assert g.pendientes == 1      # el primero caducó
    for i in range(5):
        run(g, "SEGURO", 501 + i * 2); run(g, "PELIGRO", 502 + i * 2, [{"mensaje": "a"}])
    assert g.pendientes <= 2


def test_excepcion_del_envio_no_se_propaga():
    def mal(t): raise RuntimeError("red")
    g = GestorNotificaciones(mal, demora_sin_datos_s=0)
    run(g, "SIN_DATOS", 0)
    assert g.pendientes == 1


if __name__ == "__main__":
    fallos = 0
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            try: f(); print("OK  ", n)
            except Exception as e: fallos += 1; print("FALLA", n, repr(e))
    sys.exit(1 if fallos else 0)
