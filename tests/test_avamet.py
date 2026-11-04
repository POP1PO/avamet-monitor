"""Parser de avamet.py con el texto REAL de la ficha de Almassora (1-nov-2026) y presión anómala.
Ejecutar: pytest -v   o   python tests/test_avamet.py"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import avamet   # noqa: E402

TEXTO = ("AVAMET - MXO el temps / el tiempo Almassora Web AVAMET MXO Meteoxarxaonline Més informació: Fitxa tècnica "
         "Últimes dades Almassora 39° 56' 40.55\" N, 00° 03' 24.35\" W (28 m) 01-11-2026 05:25 15,1° mín 15,1° màx 17,7° "
         "Humitat 90 % Pressió al nivell de la mar {pres} hPa Vent 10 km/h ONO Vent màx 18 km/h Pluja hui 0,0 mm "
         "Pluja mensual 0,0 mm Pluja anual 366,6 mm Temperatura Velocitat i direcció del vent")


class _R:
    def __init__(self, t): self.text = "<html><body><p>" + t + "</p></body></html>"
    def raise_for_status(self): pass


def _leer(pres):
    with mock.patch.object(avamet.requests, "get", return_value=_R(TEXTO.format(pres=pres))):
        return avamet.obtener_datos_tiempo_real("c05m009e02")


def test_texto_real_completo():
    d = _leer("1.021")
    assert d["Presión"] == "1021 hPa" and d["Fecha lectura"] == "01-11-2026 05:25"
    assert d["Temperatura"] == "15.1 °C" and d["Humedad"] == "90 %" and d["Lluvia anual"] == "366.6 mm"
    assert avamet.datos_validos(d)


def test_presion_800_se_ignora_pero_la_estacion_sigue_valida():
    d = _leer("800")
    assert "Presión" not in d and d["Presión descartada"] == "800 hPa"
    assert avamet.datos_validos(d)                                  # temperatura y humedad siguen evaluándose
    assert d["Temperatura"] == "15.1 °C" and d["Humedad"] == "90 %"


def test_presion_1200_tambien_se_ignora():
    d = _leer("1.200")
    assert "Presión" not in d and avamet.datos_validos(d)


if __name__ == "__main__":
    fallos = 0
    for nombre, fn in sorted(globals().items()):
        if nombre.startswith("test_") and callable(fn):
            try:
                fn(); print(f"OK    {nombre}")
            except Exception as e:                                          # noqa: BLE001
                fallos += 1; print(f"FALLO {nombre}: {type(e).__name__}: {e}")
    sys.exit(1 if fallos else 0)
