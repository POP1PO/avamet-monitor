"""Registro de satélite en la BD e informe de comparación (modo sombra). Ejecutar: python tests/test_satelite_informe.py"""
import sys, tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import database, satelite_informe as inf   # noqa: E402

database.DB_FILE = Path(tempfile.mkdtemp()) / "t.db"
database.init_db()
T0 = datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc)


def fila(min_, lluvia, estado="DESPEJADO (estimado)", minreg=5.0):
    return {"timestamp": (T0 + timedelta(minutes=min_)).isoformat(timespec="seconds"), "lluvia_hoy": lluvia, "estado": estado,
            "min_regional": minreg, "t108": 8.0}


def test_acierto_con_antelacion_y_falsa_alarma():
    filas = [fila(0, 0.0), fila(30, 0.0, "CUBIERTO", -35), fila(60, 0.0, "CUBIERTO", -30), fila(90, 0.4),      # lluvia a los 90 min, avisada desde el 30
             fila(120, 0.4), fila(150, 0.4), fila(180, 0.4, "POSIBLE NUBOSIDAD", -5),                          # aviso aislado
             fila(210, 0.4), fila(240, 0.4), fila(270, 0.4), fila(300, 0.4)]                                  # sin lluvia detrás: falsa alarma
    r = inf.informe(filas)
    assert r["episodios_lluvia"] == 1 and r["episodios_avisados"] == 1 and r["antelacion_media_min"] == 60, r
    assert r["avisos_totales"] == 3 and r["falsas_alarmas"] == 1, r


def test_lluvia_sin_aviso_y_reinicio_de_medianoche():
    filas = [fila(0, 1.0), fila(30, 0.0), fila(60, 0.0), fila(90, 0.6)]      # 1.0 -> 0.0 (medianoche) no cuenta; 0.0 -> 0.6 sí
    r = inf.informe(filas)
    assert r["episodios_lluvia"] == 1 and r["episodios_avisados"] == 0 and r["antelacion_media_min"] is None, r


def test_huecos_largos_no_cuentan_y_pocos_datos():
    assert inf.episodios_lluvia([fila(0, 0.0), fila(300, 2.0)]) == []         # 5 h sin registros: no se sabe cuándo empezó
    assert "Todavía no hay datos" in inf.informe([fila(0, 0.0)])["mensaje"]
    assert "Solo 1 episodios" in inf.informe([fila(0, 0.0), fila(30, 1.0)])["mensaje"]


def test_nube_fria_solo_cuenta_dentro_del_radio():
    # Como en la vigilancia del 2-3 nov: la mínima de toda la zona siempre < -20 °C pero sin lluvia -> antes, todo falsas alarmas
    viejas = [fila(m, 0.0, minreg=-35.0) for m in range(0, 300, 30)]
    r = inf.informe(viejas, radio_km=40)
    assert r["avisos_totales"] == 0 and r["registros_sin_radio"] == len(viejas) and r["definiciones"]["radio_km"] == 40, r
    nuevas = [dict(fila(m, 0.0, minreg=-35.0), min_radio=5.0) for m in range(0, 300, 30)]
    nuevas[3]["min_radio"] = -25.0                                   # nube fría a menos de 40 km en un registro
    r = inf.informe(nuevas, radio_km=40)
    assert r["avisos_totales"] == 1 and r["falsas_alarmas"] == 1 and r["registros_sin_radio"] == 0, r


def test_guardar_y_leer_en_la_bd():
    sat = {"t108_c": 7.5, "t039_c": 3.0, "min_regional_c": -2.0, "noche": True, "estado": "DESPEJADO (estimado)",
           "barrido_utc": "2026-11-02 00:00", "satelite": "MSG4"}
    assert database.guardar_satelite("est1", sat, {"Temperatura": "10.5 °C", "Humedad": "80 %", "Viento": "5 km/h", "Lluvia hoy": "0.2 mm"})
    assert not database.guardar_satelite("est1", {"t108_c": None}, {})          # sin datos no se guarda
    f = database.leer_satelite("est1", 1)
    assert len(f) == 1 and f[0]["t108"] == 7.5 and f[0]["aire"] == 10.5 and f[0]["lluvia_hoy"] == 0.2 and f[0]["noche"] == 1
    assert database.leer_satelite("otra", 1) == []


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"): v(); print("ok", k)
