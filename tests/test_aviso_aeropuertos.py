"""Lluvia en un aeropuerto cercano => aviso (VIGILANCIA), venga de donde venga. Ejecutar: pytest -q tests/test_aviso_aeropuertos.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import alertas  # noqa: E402


def _a(icao, nombre, km): return {"icao": icao, "nombre": nombre, "distancia_km": km}


def test_lluvia_cerca_avisa():
    r = alertas.avisos_lluvia_aeropuertos([_a("LEVC", "Valencia", 35.2)], {"LEVC": {"fenomenos": "Lluvia"}})
    assert len(r) == 1 and "Valencia" in r[0] and "35 km" in r[0]


def test_lejos_sin_fenomenos_o_niebla_no_avisan():
    m = {"A": {"fenomenos": "Lluvia"}, "B": {"fenomenos": "Ninguno"}, "C": {"fenomenos": "Niebla"}}
    assert alertas.avisos_lluvia_aeropuertos([_a("A", "Lejos", 90.0), _a("B", "B", 10.0), _a("C", "C", 10.0), _a("D", "Sin metar", 5.0)], m) == []
    assert alertas.avisos_lluvia_aeropuertos(None, None) == []
    assert alertas.avisos_lluvia_aeropuertos([_a("A", "x", None)], {"A": {"fenomenos": "Lluvia"}}) == []


def test_varios_fenomenos():
    r = alertas.avisos_lluvia_aeropuertos([_a("A", "x", 20.0), _a("B", "y", 59.9)], {"A": {"fenomenos": "Tormenta, Chubascos"}, "B": {"fenomenos": "Llovizna"}})
    assert len(r) == 2
