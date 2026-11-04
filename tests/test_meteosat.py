"""Módulo de satélite (Meteosat) y credenciales de EUMETSAT en la configuración. Se ejecuta con:  python tests/test_meteosat.py
Sin red y sin los paquetes eumdac/netCDF4: se prueba la lógica pura y, con un netCDF4 falso, la lectura del fichero."""
import os, sys, tempfile, types
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.environ["AVAMET_DATA_DIR"] = tempfile.mkdtemp()
import numpy as np
from backend import meteosat, entorno


def _rad(T, canal, sat="MSG4"):
    nu, al, be = meteosat.CALIB[sat][canal]
    return nu ** 3 * meteosat.C1 / (np.exp(meteosat.C2 * nu / (al * T + be)) - 1)


def test_planck_ida_y_vuelta_y_unidades():
    T = np.array([[250.0, 270.0], [285.0, 300.0]])
    for canal in ("channel_4", "channel_9"):
        c = meteosat.a_celsius(_rad(T, canal), "mW m-2 sr-1 (cm-1)-1", canal, "MSG4")
        assert np.allclose(c + 273.15, T, atol=1e-6)
    assert np.allclose(meteosat.a_celsius(T, "K", "channel_9", "MSG4"), T - 273.15)
    assert np.allclose(meteosat.a_celsius(T - 273.15, "degC", "channel_9", "MSG4"), T - 273.15)


def test_enmascarados_y_rangos_absurdos():
    m = np.ma.masked_array([[_rad(280.0, "channel_9"), 1e30], [_rad(281.0, "channel_9"), _rad(282.0, "channel_9")]], mask=[[False, True], [False, False]])
    r = meteosat.a_celsius(m, "mW", "channel_9", "MSG4")
    assert np.isfinite(r[0, 0]) and np.isnan(r[0, 1])
    try: meteosat.a_celsius(np.array([[270.0, 280.0]]), "", "channel_9", "MSG4"); assert False   # Kelvin sin unidades -> rechazado
    except ValueError: pass
    try: meteosat.a_celsius(np.array([[5.0]]), "mW", "channel_9", "MSG1"); assert False           # satélite sin tabla
    except ValueError: pass


def test_pixel_1d_2d_y_fuera():
    la, lo = np.linspace(41.2, 37.6, 73), np.linspace(-2.6, 0.8, 69)
    assert meteosat.pixel_mas_cercano(la, lo, 39.9864, -0.0513)[:2] == (24, 51)
    LA, LO = np.meshgrid(la, lo, indexing="ij")
    assert meteosat.pixel_mas_cercano(LA, LO, 39.9864, -0.0513)[:2] == (24, 51)
    try: meteosat.pixel_mas_cercano(la, lo, 36.0, -0.05); assert False
    except ValueError: pass


def test_elevacion_solar_valores_conocidos():
    e = meteosat.elevacion_solar(datetime(2026, 11, 2, 12, tzinfo=timezone.utc), 40.0, 0.0)
    assert 34.5 < e < 36.0, e                       # cálculo a mano: ~35,2-35,4°
    assert meteosat.elevacion_solar(datetime(2026, 11, 2, 21, tzinfo=timezone.utc), 40.0, 0.0) < -40
    v = meteosat.elevacion_solar(datetime(2026, 6, 15, 17, tzinfo=timezone.utc), 39.99, -0.05)
    assert 25 < v < 27, v                           # la versión «simplificada» daba 12,9°


def test_reglas_de_estado():
    assert meteosat.evaluar(-30, -33, True, 10)["estado"] == "CUBIERTO"
    assert meteosat.evaluar(8, 4, True, 9)["estado"] == "POSIBLE NUBE BAJA / NIEBLA"
    assert meteosat.evaluar(8, 4, False, 9)["estado"] != "POSIBLE NUBE BAJA / NIEBLA"             # de día no vale el 3.9 µm
    assert meteosat.evaluar(-2, -2, True, 14)["estado"] == "POSIBLE NUBOSIDAD"
    assert meteosat.evaluar(8, 8, True, 10)["estado"].startswith("DESPEJADO")


def test_analizar_netcdf_con_fichero_falso():
    class Var:
        def __init__(s, a, units=None): s.a = np.ma.asarray(a); 
        def __getitem__(s, k): return s.a[k]
    la, lo = np.linspace(41.2, 37.6, 73), np.linspace(-2.6, 0.8, 69)
    t108, t039 = np.full((73, 69), 8.0), np.full((73, 69), 3.0)            # °C
    v4, v9 = Var(_rad(t039 + 273.15, "channel_4")), Var(_rad(t108 + 273.15, "channel_9"))
    v4.units = v9.units = "mW m-2 sr-1 (cm-1)-1"
    class DS:
        variables = {"lat": Var(la), "lon": Var(lo), "channel_4": v4, "channel_9": v9}
        def __enter__(s): return s
        def __exit__(s, *a): return False
    fake = types.ModuleType("netCDF4"); fake.Dataset = lambda ruta, modo: DS()
    sys.modules["netCDF4"] = fake
    try:
        r = meteosat.analizar_netcdf("x.nc", datetime(2026, 11, 2, 22, tzinfo=timezone.utc), "MSG4", 39.9864, -0.0513, 10.0)
    finally:
        del sys.modules["netCDF4"]
    assert abs(r["t108_c"] - 8.0) < 0.2 and abs(r["t039_c"] - 3.0) < 0.2 and r["noche"] and r["estado"] == "POSIBLE NUBE BAJA / NIEBLA", r


def test_sin_credenciales_esta_inactivo_y_con_ellas_cachea():
    for k in ("EUMETSAT_CONSUMER_KEY", "EUMETSAT_CONSUMER_SECRET"): os.environ.pop(k, None)
    assert meteosat.obtener(40.0, 0.0) == {"activo": False, "datos": None, "error": None, "traza": []}
    os.environ["EUMETSAT_CONSUMER_KEY"], os.environ["EUMETSAT_CONSUMER_SECRET"] = "k" * 12, "s" * 12
    meteosat.limpiar_cache()
    llamadas = []
    orig_d, orig_p = meteosat._descargar_recorte, meteosat.paquetes_instalados
    try:
        meteosat.paquetes_instalados = lambda: True
        def falla(lat, lon, ya=None): llamadas.append(1); raise OSError("sin red")
        meteosat._descargar_recorte = falla
        a = meteosat.obtener(40.0, 0.0, 10.0, ahora=1000.0)
        assert a["activo"] and a["datos"] is None and "sin red" in a["error"]
        meteosat.obtener(40.0, 0.0, 10.0, ahora=1100.0); assert len(llamadas) == 1              # no reintenta antes de 15 min
        meteosat.obtener(40.0, 0.0, 10.0, ahora=1000.0 + meteosat.REINTENTO_S + 1); assert len(llamadas) == 2
    finally:
        meteosat._descargar_recorte, meteosat.paquetes_instalados = orig_d, orig_p
        for k in ("EUMETSAT_CONSUMER_KEY", "EUMETSAT_CONSUMER_SECRET"): os.environ.pop(k, None)


def test_configuracion_guarda_claves_de_eumetsat():
    r = entorno.validar_campos({"eumetsat_key": " ABCDEFGH1234 ", "eumetsat_secret": "secretSECRET_12-3"})
    assert r == {"EUMETSAT_CONSUMER_KEY": "ABCDEFGH1234", "EUMETSAT_CONSUMER_SECRET": "secretSECRET_12-3"}
    for malo in ({"eumetsat_key": "corta"}, {"eumetsat_secret": "con espacio 12345"}, {"eumetsat_key": 'a"b' * 5}, {"eumetsat_secret": "x$y" * 5}):
        try: entorno.validar_campos(malo); raise AssertionError(f"debía fallar: {malo!r}")
        except ValueError: pass
    ruta = Path(tempfile.mkdtemp()) / ".env"
    ruta.write_text("AVAMET_ADMIN_CHAT_ID=42\n# comentario\n", encoding="utf-8")
    entorno.actualizar_env(ruta, r)
    t = ruta.read_text(encoding="utf-8")
    assert "AVAMET_ADMIN_CHAT_ID=42" in t and "# comentario" in t and "EUMETSAT_CONSUMER_KEY=ABCDEFGH1234" in t
    assert entorno.enmascarar_clave("AbCdEfGh12345678") == "AbCd••••••••" and "SECRET" not in entorno.enmascarar_clave("secretSECRET")[4:]


def test_traza_paso_a_paso_sin_secretos():
    os.environ["EUMETSAT_CONSUMER_KEY"], os.environ["EUMETSAT_CONSUMER_SECRET"] = "CLAVEPRUEBA12", "SECRETOPRUEBA12"
    meteosat.limpiar_cache(); meteosat._TRAZA.clear()
    orig_d, orig_p = meteosat._descargar_recorte, meteosat.paquetes_instalados
    try:
        meteosat.paquetes_instalados = lambda: True
        def falla(lat, lon, ya=None): meteosat._paso("2/7 prueba"); raise RuntimeError("Data Tailor falló (FAILED)")
        meteosat._descargar_recorte = falla
        r = meteosat.obtener(40.0, 0.0, 10.0, ahora=5000.0)
        texto = "\n".join(r["traza"])
        assert "Nueva consulta" in texto and "2/7 prueba" in texto and "FALLO en esta consulta" in texto, texto
        assert "CLAVEPRUEBA12" not in texto and "SECRETOPRUEBA12" not in texto
    finally:
        meteosat._descargar_recorte, meteosat.paquetes_instalados = orig_d, orig_p
        for k in ("EUMETSAT_CONSUMER_KEY", "EUMETSAT_CONSUMER_SECRET"): os.environ.pop(k, None)


def test_valores_en_punto_interpola_y_resuelve_el_empate_de_medio_pixel():
    # Rejilla como la real del 3-nov-2026: 38.475..41.475 (78 filas) x -1.750..1.650 (89 columnas); estación en 39.975, -0.050
    la, lo = np.linspace(38.475, 41.475, 78), np.linspace(-1.750, 1.650, 89)
    assert meteosat.pixel_mas_cercano(la, lo, 39.975, -0.050)[:2] == (38, 44)     # el antiguo: siempre la fila del sur
    c = np.zeros((78, 89)); c[38, :] = 10.0; c[39, :] = 20.0
    (v,), desc = meteosat.valores_en_punto([c], la, lo, 39.975, -0.050)
    assert abs(v - 15.0) < 1e-9 and "filas 38-39 (pesos 0.50/0.50)" in desc and "columna 44" in desc, desc
    (v,), _ = meteosat.valores_en_punto([c[::-1]], la[::-1], lo, 39.975, -0.050)      # rejilla decreciente: igual
    assert abs(v - 15.0) < 1e-9
    (v,), desc = meteosat.valores_en_punto([c], la, lo, float(la[38]), float(lo[44]))  # justo sobre un píxel: ese píxel
    assert v == 10.0 and desc.endswith("fila 38, columna 44"), desc
    c2 = c.copy(); c2[39, 44] = np.nan                                                  # vecino sin dato: el más cercano
    (v,), desc = meteosat.valores_en_punto([c2], la, lo, 39.975, -0.050)
    assert v == 10.0 and "más cercano (38,44)" in desc
    LA, LO = np.meshgrid(la, lo, indexing="ij")                                        # rejilla 2-D: el más cercano
    (v,), desc = meteosat.valores_en_punto([c], LA, LO, 39.975, -0.050)
    assert v == 10.0 and "más cercano" in desc


def _eumdac_falso(productos, creados):
    """Módulo eumdac falso: la búsqueda devuelve `productos`; new_customisation anota en `creados` (no debe llamarse)."""
    import types
    class Prod:
        def __init__(s, fin): s.sensing_end = fin
        def __str__(s): return f"MSG3-SEVI-MSG15-0100-NA-{s.sensing_end:%Y%m%d%H%M%S}"
    m = types.ModuleType("eumdac"); tm = types.ModuleType("eumdac.tailor_models")
    m.AccessToken = lambda cred: "token"
    class DS:
        def __init__(s, tok): pass
        def get_collection(s, c):
            class Col:
                def search(s2, **k): return [Prod(f) for f in productos]
            return Col()
    class DT:
        def __init__(s, tok): pass
        def new_customisation(s, prod, chain): creados.append(prod); raise RuntimeError("no se debía crear trabajo")
    m.DataStore, m.DataTailor = DS, DT
    tm.Chain = lambda **k: k
    m.tailor_models = tm
    return m, tm


def test_no_pide_a_data_tailor_una_imagen_ya_analizada():
    os.environ["EUMETSAT_CONSUMER_KEY"], os.environ["EUMETSAT_CONSUMER_SECRET"] = "k" * 12, "s" * 12
    fin = datetime(2026, 11, 3, 8, 57, 42)                 # eumdac lo da sin zona (UTC)
    creados = []
    m, tm = _eumdac_falso([datetime(2026, 11, 3, 8, 42, 42), fin], creados)
    sys.modules["eumdac"], sys.modules["eumdac.tailor_models"] = m, tm
    try:
        assert meteosat._descargar_recorte(40.0, 0.0, fin) is None and creados == []
        assert meteosat._descargar_recorte(40.0, 0.0, fin.replace(tzinfo=timezone.utc)) is None and creados == []
        try: meteosat._descargar_recorte(40.0, 0.0, datetime(2026, 11, 3, 8, 42, 42)); assert False   # hay una más nueva: sí la pide
        except RuntimeError as e: assert "no se debía" in str(e) and len(creados) == 1
    finally:
        sys.modules.pop("eumdac", None); sys.modules.pop("eumdac.tailor_models", None)
        for k in ("EUMETSAT_CONSUMER_KEY", "EUMETSAT_CONSUMER_SECRET"): os.environ.pop(k, None)


def test_sin_imagen_nueva_conserva_el_ts_y_reintenta_a_los_15_min():
    os.environ["EUMETSAT_CONSUMER_KEY"], os.environ["EUMETSAT_CONSUMER_SECRET"] = "k" * 12, "s" * 12
    meteosat.limpiar_cache()
    fins = [datetime(2026, 11, 3, 8, 57), None, datetime(2026, 11, 3, 9, 57)]      # None = EUMETSAT sin imagen nueva
    vistos, analizados = [], []
    orig = meteosat._descargar_recorte, meteosat.analizar_netcdf, meteosat.paquetes_instalados
    try:
        meteosat.paquetes_instalados = lambda: True
        def descargar(lat, lon, ya=None):
            vistos.append(ya)
            f = fins.pop(0)
            if f is None: return None
            return os.path.join(tempfile.mkdtemp(), "x.nc"), f, "MSG3"
        def analizar(ruta, instante, sat, lat, lon, aire):
            analizados.append(instante); return {"t108_c": 9.0, "barrido_utc": instante.strftime("%Y-%m-%d %H:%M")}
        meteosat._descargar_recorte, meteosat.analizar_netcdf = descargar, analizar
        a = meteosat.obtener(40.0, 0.0, 10.0, ahora=10000.0)
        assert a["ts"] == 10000.0 and not a["sin_novedad"] and vistos == [None]
        b = meteosat.obtener(40.0, 0.0, 10.0, ahora=10000.0 + meteosat.REFRESCO_S + 1)
        assert vistos[-1] == datetime(2026, 11, 3, 8, 57)                    # le pasa la imagen ya analizada
        assert b["ts"] == 10000.0 and b["sin_novedad"] and b["datos"] == a["datos"] and len(analizados) == 1
        c = meteosat.obtener(40.0, 0.0, 10.0, ahora=10000.0 + meteosat.REFRESCO_S + 60)
        assert len(vistos) == 2 and c["ts"] == 10000.0                       # no vuelve a buscar enseguida
        t = 10000.0 + meteosat.REFRESCO_S + meteosat.REINTENTO_S + 2
        d = meteosat.obtener(40.0, 0.0, 10.0, ahora=t)
        assert d["ts"] == t and not d["sin_novedad"] and len(analizados) == 2
    finally:
        meteosat._descargar_recorte, meteosat.analizar_netcdf, meteosat.paquetes_instalados = orig
        for k in ("EUMETSAT_CONSUMER_KEY", "EUMETSAT_CONSUMER_SECRET"): os.environ.pop(k, None)


def test_un_fallo_no_hace_que_la_misma_imagen_se_registre_dos_veces():
    os.environ["EUMETSAT_CONSUMER_KEY"], os.environ["EUMETSAT_CONSUMER_SECRET"] = "k" * 12, "s" * 12
    meteosat.limpiar_cache()
    pasos = ["ok", "falla", "misma"]
    orig = meteosat._descargar_recorte, meteosat.analizar_netcdf, meteosat.paquetes_instalados
    try:
        meteosat.paquetes_instalados = lambda: True
        def descargar(lat, lon, ya=None):
            p = pasos.pop(0)
            if p == "falla": raise RuntimeError("Data Tailor falló (FAILED)")
            if p == "misma": assert ya == datetime(2026, 11, 3, 11, 57); return None
            return os.path.join(tempfile.mkdtemp(), "x.nc"), datetime(2026, 11, 3, 11, 57), "MSG3"
        meteosat._descargar_recorte = descargar
        meteosat.analizar_netcdf = lambda *a: {"t108_c": 9.0, "barrido_utc": "2026-11-03 11:57"}
        a = meteosat.obtener(40.0, 0.0, 10.0, ahora=1000.0)
        b = meteosat.obtener(40.0, 0.0, 10.0, ahora=1000.0 + meteosat.REFRESCO_S + 1)
        assert b["error"] and b["ts"] == a["ts"]                              # mismos datos => mismo ts
        c = meteosat.obtener(40.0, 0.0, 10.0, ahora=1000.0 + meteosat.REFRESCO_S + meteosat.REINTENTO_S + 2)
        assert c["error"] is None and c["ts"] == a["ts"] and c["sin_novedad"]   # main.py no la guarda otra vez
    finally:
        meteosat._descargar_recorte, meteosat.analizar_netcdf, meteosat.paquetes_instalados = orig
        for k in ("EUMETSAT_CONSUMER_KEY", "EUMETSAT_CONSUMER_SECRET"): os.environ.pop(k, None)


def test_aviso_de_imagen_antigua():
    import time as _t
    hace = lambda m: datetime.fromtimestamp(_t.time() - m * 60, timezone.utc).strftime("%Y-%m-%d %H:%M")
    r = meteosat.antiguedad({"barrido_utc": hace(20)})
    assert 19 <= r["edad_imagen_min"] <= 21 and r["aviso_imagen"] is None
    r = meteosat.antiguedad({"barrido_utc": hace(70)})
    assert r["aviso_imagen"] and "no ha publicado" in r["aviso_imagen"]
    assert meteosat.antiguedad(None) == {"edad_imagen_min": None, "aviso_imagen": None}
    assert meteosat.antiguedad({"barrido_utc": "basura"})["aviso_imagen"] is None


def test_base_de_datos_no_duplica_la_misma_imagen():
    from backend import database
    sat = {"t108_c": 9.0, "t039_c": 4.0, "min_regional_c": -30.0, "noche": True, "estado": "X", "barrido_utc": "2026-11-03 11:57", "satelite": "MSG3"}
    est = {"Temperatura": "15 °C", "Humedad": "70 %", "Lluvia hoy": "0.0 mm"}
    assert database.guardar_satelite("est_dup", sat, est) is True
    assert database.guardar_satelite("est_dup", sat, est) is False
    assert database.guardar_satelite("otra_est", sat, est) is True                     # otra estación: sí
    assert database.guardar_satelite("est_dup", {**sat, "barrido_utc": "2026-11-03 12:12"}, est) is True
    assert len(database.leer_satelite("est_dup", 1)) == 2


def test_minimo_en_radio_1d_2d_y_vacio():
    la, lo = np.linspace(41.475, 38.475, 78), np.linspace(-1.75, 1.65, 89)          # rejilla real del recorte (~0.039° x 0.039°)
    t = np.full((78, 89), 10.0)
    t[0, 0] = -40.0                                                                 # nube muy fría en la esquina NO (~200 km)
    iy, ix = int(np.abs(la - 39.75).argmin()), int(np.abs(lo - 0.2).argmin())     # nube fría a ~30 km de Castelló
    t[iy, ix] = -25.0
    m, n = meteosat.minimo_en_radio(t, la, lo, 39.9864, -0.0513, 40)
    assert m == -25.0 and n > 100, (m, n)
    m, n = meteosat.minimo_en_radio(t, la, lo, 39.9864, -0.0513, 15)               # a 15 km ya no la alcanza
    assert m == 10.0, m
    LA, LO = np.meshgrid(la, lo, indexing="ij")
    assert meteosat.minimo_en_radio(t, LA, LO, 39.9864, -0.0513, 40)[0] == -25.0   # lat/lon 2-D: mismo resultado
    assert meteosat.minimo_en_radio(t, la, lo, 39.9864, -0.0513, 0.5) == (None, 0)  # radio menor que un píxel
    tm = np.ma.masked_array(t, mask=(t < 0))                                        # enmascarados: no cuentan
    assert meteosat.minimo_en_radio(tm, la, lo, 39.9864, -0.0513, 40)[0] == 10.0


def test_franja_de_crepusculo():
    assert meteosat.periodo_solar(1.7) == "día" and meteosat.periodo_solar(-3.0) == "día"
    assert meteosat.periodo_solar(-4.0) == "crepúsculo" and meteosat.periodo_solar(-6.3) == "crepúsculo"   # los dos casos del 3-nov
    assert meteosat.periodo_solar(-9.0) == "noche" and meteosat.periodo_solar(-40) == "noche"
    assert meteosat.periodo_solar(-5, dia_desde=-6, noche_hasta=-6) == "día"
    assert meteosat.periodo_solar(-7, dia_desde=-6, noche_hasta=-6) == "noche"        # límites iguales: sin franja
    # Mismo cielo (diferencia +3 K, que de noche es NIEBLA): en el crepúsculo no se da por despejado ni por niebla
    r = meteosat.evaluar(8, 5, False, 10, crepusculo=True)
    assert r["estado"] == "SIN DETERMINAR (crepúsculo)" and "+3.0 K" in r["motivo"], r
    assert meteosat.evaluar(-30, -33, False, 10, crepusculo=True)["estado"] == "CUBIERTO"      # la cima fría vale a cualquier hora
    assert meteosat.evaluar(-2, -2, False, 14, crepusculo=True)["estado"] == "POSIBLE NUBOSIDAD"
    assert meteosat.evaluar(8, 5, False, 10)["estado"].startswith("DESPEJADO")                  # de día, como antes


def test_analizar_netcdf_guarda_minima_del_radio_y_periodo():
    class Var:
        def __init__(s, a, units=None): s.a = np.ma.asarray(a)
        def __getitem__(s, k): return s.a[k]
    la, lo = np.linspace(41.2, 37.6, 73), np.linspace(-2.6, 0.8, 69)
    t108, t039 = np.full((73, 69), 8.0), np.full((73, 69), 3.0)
    t108[0, 0] = -45.0                                               # nube fría lejos (> 150 km): no debe contar en el radio
    v4, v9 = Var(_rad(t039 + 273.15, "channel_4")), Var(_rad(t108 + 273.15, "channel_9"))
    v4.units = v9.units = "mW m-2 sr-1 (cm-1)-1"
    class DS:
        variables = {"lat": Var(la), "lon": Var(lo), "channel_4": v4, "channel_9": v9}
        def __enter__(s): return s
        def __exit__(s, *a): return False
    fake = types.ModuleType("netCDF4"); fake.Dataset = lambda ruta, modo: DS()
    sys.modules["netCDF4"] = fake
    try:
        # 17:20 UTC del 3-nov en Castelló: sol a -5.1° -> crepúsculo
        r = meteosat.analizar_netcdf("x.nc", datetime(2026, 11, 3, 17, 20, tzinfo=timezone.utc), "MSG4", 39.9864, -0.0513, 10.0)
    finally:
        del sys.modules["netCDF4"]
    assert r["min_regional_c"] < -44 and abs(r["min_radio_c"] - 8.0) < 0.2 and r["radio_km"] == meteosat.RADIO_AVISO_KM, r
    assert r["periodo"] == "crepúsculo" and r["noche"] is False and r["estado"] == "SIN DETERMINAR (crepúsculo)", r


def test_base_de_datos_guarda_minima_del_radio():
    from backend import database
    sat = {"t108_c": 9.0, "t039_c": 4.0, "min_regional_c": -30.0, "min_radio_c": -3.5, "noche": False, "estado": "X",
           "barrido_utc": "2026-11-03 16:42", "satelite": "MSG3"}
    assert database.guardar_satelite("est_radio", sat, {"Temperatura": "15 °C", "Humedad": "70 %"}) is True
    f = database.leer_satelite("est_radio", 1)
    assert len(f) == 1 and f[0]["min_radio"] == -3.5 and f[0]["min_regional"] == -30.0


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"): v(); print("ok", k)
