"""Estado de la ESTACIÓN (≠ estado de la red): alertas.estado_estacion / aviso_sin_datos_estacion / estaciones_alternativas."""
import sys, time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import alertas, config

AHORA = time.time() // 60 * 60 + 30     # a mitad de minuto: la fecha de AVAMET solo trae minutos y, si no, el test falla según el segundo en que se ejecute


def lectura(edad_s, ahora=AHORA):
    t = datetime.fromtimestamp(ahora - edad_s, ZoneInfo("Europe/Madrid")).strftime("%d-%m-%Y %H:%M")
    return {"Fecha lectura": t, "Temperatura": "15.1 °C", "Humedad": "70 %", "Presión": "1021 hPa", "Viento": "5 km/h N",
            "Viento máximo": "8 km/h", "Lluvia hoy": "0.0 mm"}


def test_activa_y_congelada():
    e = alertas.estado_estacion(lectura(120), "Aras de los Olmos", AHORA)
    assert e["estado"] == "ACTIVA" and e["edad_min"] == 2 and e["mensaje"] == "" and e["max_min"] == config.EDAD_MAX_DATOS_S // 60
    e = alertas.estado_estacion(lectura(11 * 60 + 5), "Aras de los Olmos", AHORA)
    assert e["estado"] == "CONGELADA" and e["edad_min"] == 11
    assert e["mensaje"] == "El sensor de esta estación lleva 11 minutos sin enviar datos a AVAMET (aunque tu red funciona bien)."


def test_coincide_con_evaluar_seguridad():
    # La clasificación y la seguridad usan el mismo límite: nunca "ACTIVA" siendo no segura por antigüedad, ni al revés.
    # (La lectura se redondea al minuto al formatearla, por eso se evitan los valores justo en el límite.)
    estado = {"estado": "SEGURO", "timestamp": datetime.fromtimestamp(AHORA).isoformat(timespec="seconds")}
    for edad in (0, 300, 540, 700, 900, 3600):
        d = lectura(edad)
        activa = alertas.estado_estacion(d, "x", AHORA)["estado"] == "ACTIVA"
        seguro, motivo = alertas.evaluar_seguridad(estado, d, AHORA)
        assert activa == seguro, (edad, motivo)


def test_sin_fecha_y_sin_lectura():
    e = alertas.estado_estacion({"Error": "timeout"}, "X", AHORA)
    assert e["estado"] == "SIN_LECTURA" and e["edad_min"] is None
    d = lectura(60); d["Fecha lectura"] = "basura"
    assert alertas.estado_estacion(d, "X", AHORA)["estado"] == "SIN_FECHA"
    futura = lectura(-3600)                                   # fecha una hora en el futuro (zona horaria mal)
    assert alertas.estado_estacion(futura, "X", AHORA)["estado"] == "SIN_FECHA"


def test_texto_del_aviso():
    e = alertas.estado_estacion(lectura(11 * 60), "Aras de los Olmos", AHORA)
    assert alertas.aviso_sin_datos_estacion(e) == (
        "⚠️ SIN DATOS FÍABLES: El sensor de Aras de los Olmos no se actualiza desde hace 11 min. "
        "(Tu conexión a Internet y la web de AVAMET funcionan correctamente, pero este sensor está inactivo).")
    assert alertas.aviso_sin_datos_estacion({"estado": "ACTIVA"}) == ""
    assert alertas.aviso_sin_datos_estacion({"estado": "SIN_LECTURA"}) == ""
    assert "no indica una fecha válida" in alertas.aviso_sin_datos_estacion({"estado": "SIN_FECHA", "nombre": "X"})


def test_alternativas_solo_activas_y_ordenadas():
    radio = [
        {"id": "a", "nombre": "Lejana", "distancia_km": 20.0, "datos": lectura(60)},
        {"id": "b", "nombre": "Congelada", "distancia_km": 3.0, "datos": lectura(30 * 60)},
        {"id": "c", "nombre": "Con error", "distancia_km": 4.0, "datos": {"Error": "x"}},
        {"id": "d", "nombre": "Cercana", "distancia_km": 5.5, "datos": lectura(180)},
    ]
    alt = alertas.estaciones_alternativas(radio, AHORA)
    assert [a["id"] for a in alt] == ["d", "a"] and alt[0]["edad_min"] == 3 and alt[0]["distancia_km"] == 5.5
    assert len(alertas.estaciones_alternativas(radio * 5, AHORA, maximo=1)) == 1
    assert alertas.estaciones_alternativas([], AHORA) == [] and alertas.estaciones_alternativas(None, AHORA) == []


if __name__ == "__main__":
    n = 0
    for nombre, f in list(globals().items()):
        if nombre.startswith("test_") and callable(f):
            f(); n += 1; print("OK", nombre)
    print(f"{n} pruebas OK")
