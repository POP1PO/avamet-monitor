"""Pruebas de las funciones internas de comprobar_antes_de_compilar.py (sin PyInstaller ni red)."""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import comprobar_antes_de_compilar as c


def test_versiones():
    assert c._cumple("0.115.0", ">=0.110") and not c._cumple("0.100", ">=0.110")
    assert c._cumple("2.1", ">=2.0,<3") and not c._cumple("3.0", ">=2.0,<3")
    assert c._cumple("1.2.3", "==1.2.3") and c._cumple("1.2.3", "")
    assert c._cumple("5", "no entiendo esto")            # especificación rara: no bloquea


def test_requirements_con_marcadores():
    with tempfile.TemporaryDirectory() as d:
        r = Path(d) / "requirements.txt"
        r.write_text('# c\nfastapi>=0.110\nuvicorn[standard]>=0.27\ntzdata; platform_system == "Windows"\n'
                     'solo_linux; sys_platform != "win32"\n', encoding="utf-8")
        nombres = {n: (e, s) for n, e, s in c.leer_requirements(r)}
    assert nombres["fastapi"] == ([], ">=0.110") and nombres["uvicorn"] == (["standard"], ">=27".replace("27", "0.27"))
    import platform
    assert ("tzdata" in nombres) == (platform.system() == "Windows")
    assert ("solo_linux" in nombres) == (sys.platform != "win32")


def test_requirements_reales_se_leen():
    nombres = [n for n, _, _ in c.leer_requirements(Path(__file__).resolve().parent.parent / "requirements.txt")]
    assert {"fastapi", "uvicorn", "requests", "beautifulsoup4", "python-dotenv"} <= set(nombres)


def _evaluar(spec_txt):
    """Escribe un .spec de prueba en una copia mínima del proyecto y devuelve evaluar_spec()."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        d = Path(d); (d / "backend").mkdir()
        for m in ("__init__", "main", "entorno", "config"):
            (d / "backend" / f"{m}.py").write_text("", encoding="utf-8")
        sp = d / "x.spec"; sp.write_text(spec_txt, encoding="utf-8")
        antes = c.RAIZ
        c.RAIZ = d
        try:
            return c.evaluar_spec(sp)
        finally:
            c.RAIZ = antes


CAB = "import os, glob\nfrom PyInstaller.utils.hooks import collect_all, collect_data_files\n"
COLA = "a = Analysis(['run.py'], hiddenimports=hiddenimports, datas=[('frontend','frontend')])\npyz = PYZ(a.pure)\nexe = EXE(pyz, a.scripts, name='x')\n"


def test_spec_con_glob_listdir_y_lista_estatica():
    glob_ = "hiddenimports = ['backend.' + os.path.splitext(os.path.basename(f))[0] for f in glob.glob('backend/*.py') if '__init__' not in f]\n"
    listdir = "hiddenimports = ['backend.' + f[:-3] for f in os.listdir('backend') if f.endswith('.py') and f != '__init__.py']\n"
    fija = "hiddenimports = ['backend.main', 'backend.entorno', 'backend.config']\n"
    for cuerpo in (glob_, listdir, fija):
        hi, _d, err = _evaluar(CAB + cuerpo + COLA)
        assert err is None, err
        assert {"backend.main", "backend.entorno", "backend.config"} <= set(hi), hi


def test_spec_roto_o_que_se_detiene_se_explica():
    hi, _d, err = _evaluar("def (\n")
    assert hi is None and "SyntaxError" in err
    hi, _d, err = _evaluar(CAB + "raise SystemExit('faltan cosas')\n")
    assert hi is None and "faltan cosas" in err


if __name__ == "__main__":
    n = 0
    for nombre, f in list(globals().items()):
        if nombre.startswith("test_") and callable(f):
            f(); n += 1; print("OK", nombre)
    print(f"{n} pruebas OK")
