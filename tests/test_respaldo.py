"""Estación de respaldo (backend/respaldo.py). Se ejecuta con:  python tests/test_respaldo.py   (o con pytest)
Lógica pura: sin red. Reproduce el episodio real del 3-nov-2026 (Castelló sin publicar de ~04:39 a 04:49)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import respaldo

P = "c12m040e04"          # id de la principal (cualquiera)


def _paso(estado, activa, fecha, ahora=1000.0, n=2, principal=P):
    return respaldo.decidir(estado, principal, activa, fecha, ahora, n)


def test_episodio_real_cambia_y_vuelve_tras_dos_lecturas_nuevas():
    e, a = _paso(None, True, "03-11-2026 04:34");            assert a == respaldo.PRINCIPAL and not e["en_respaldo"]
    e, a = _paso(e, False, "03-11-2026 04:39", ahora=2000);  assert a == respaldo.CAMBIO and e["en_respaldo"] and e["desde_ts"] == 2000
    e, a = _paso(e, False, "03-11-2026 04:39");              assert a == respaldo.RESPALDO
    e, a = _paso(e, True, "03-11-2026 04:49");               assert a == respaldo.RESPALDO and e["fechas_nuevas"] == ["03-11-2026 04:49"]
    e, a = _paso(e, True, "03-11-2026 04:49");               assert a == respaldo.RESPALDO and len(e["fechas_nuevas"]) == 1   # misma lectura: no cuenta 2 veces
    e, a = _paso(e, True, "03-11-2026 04:54");               assert a == respaldo.VUELTA and not e["en_respaldo"] and e["fechas_nuevas"] == []
    e, a = _paso(e, True, "03-11-2026 04:59");               assert a == respaldo.PRINCIPAL


def test_si_se_vuelve_a_caer_a_medio_recuperar_se_empieza_a_contar_de_nuevo():
    e, _ = _paso(None, False, "x")
    e, a = _paso(e, True, "03-11-2026 05:00");  assert a == respaldo.RESPALDO and len(e["fechas_nuevas"]) == 1
    e, a = _paso(e, False, "03-11-2026 05:00"); assert a == respaldo.RESPALDO and e["fechas_nuevas"] == []
    e, a = _paso(e, True, "03-11-2026 05:20");  assert a == respaldo.RESPALDO
    e, a = _paso(e, True, "03-11-2026 05:25");  assert a == respaldo.VUELTA


def test_la_lectura_vieja_de_antes_de_caer_no_cuenta_y_sin_lectura_tambien_vale():
    e, a = _paso(None, False, "03-11-2026 04:39")
    e, a = _paso(e, True, "03-11-2026 04:39");  assert a == respaldo.RESPALDO and e["fechas_nuevas"] == []
    e, a = _paso(None, False, None)             # AVAMET no devolvió nada
    assert a == respaldo.CAMBIO and e["fecha_al_caer"] is None
    e, a = _paso(e, True, "03-11-2026 06:00", n=1); assert a == respaldo.VUELTA   # con 1 lectura configurada basta una


def test_cambio_de_ubicacion_reinicia_y_no_modifica_la_entrada():
    e, _ = _paso(None, False, "x")
    copia = dict(e)
    e2, a = _paso(e, True, "y", principal="otra")
    assert a == respaldo.PRINCIPAL and e2["principal_id"] == "otra" and not e2["en_respaldo"]
    assert e == copia                           # decidir() no toca el estado que recibe


def test_candidatos_distancia_orden_preferida_y_maximo():
    cerca = [{"id": "a", "distancia_km": 6.0}, {"id": "b", "distancia_km": 1.2}, {"id": "c", "distancia_km": 12.0},
             {"id": "d", "distancia_km": 3.5}, {"id": "e", "distancia_km": None}]
    assert [c["id"] for c in respaldo.candidatos(cerca, 10)] == ["b", "d", "a"]
    assert [c["id"] for c in respaldo.candidatos(cerca, 10, preferida="a")] == ["a", "b", "d"]
    assert [c["id"] for c in respaldo.candidatos(cerca, 10, maximo=1)] == ["b"]
    assert [c["id"] for c in respaldo.candidatos(cerca, 20, preferida="zz", maximo=5)] == ["b", "d", "a", "c"]
    assert respaldo.candidatos(cerca, 0) == [] and respaldo.candidatos([], 10) == []


def test_mensaje():
    t = respaldo.mensaje("Castelló de la Plana", {"nombre": "Almassora", "distancia_km": 3.46}, "última lectura hace 12 min")
    assert t.startswith("Usando la estación de respaldo Almassora (a 3.5 km) porque Castelló de la Plana no envía datos: última lectura hace 12 min.")
    assert "volverá" in t


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"): v(); print("ok", k)
