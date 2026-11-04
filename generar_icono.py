#!/usr/bin/env python
"""Genera icono.ico de AVAMET Monitor (una nube con una estrella asomando por detrás, sobre fondo oscuro).

Uso (desde la carpeta del proyecto):
    pip install Pillow
    python generar_icono.py            # crea icono.ico junto a este script
    python generar_icono.py --previa   # además crea icono_vista_previa.png (para verlo sin abrir el .ico)

Cada tamaño (16, 24, 32, 48, 64, 128 y 256 px) se dibuja por separado y suavizado (antialiasing), con menos detalle en los
pequeños para que se lea bien; no es un único dibujo reducido. Después PyInstaller lo usa tal cual (icon=icono.ico en el .spec).
Colores de la interfaz: azul #1f6feb, naranja #d29922, fondo #0d1117 (verde y rojo se reservan para el semáforo de la app).
"""
import math
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError:
    sys.exit("Falta Pillow. Instálalo con:  pip install Pillow")

AQUI = Path(__file__).resolve().parent
TAMANOS = (16, 24, 32, 48, 64, 128, 256)

FONDO = "#0d1117"
AZUL = "#1f6feb"
NARANJA = "#d29922"
NUBE = "#e6edf3"
NUBE_SOMBRA = "#b6c2d0"


def _estrella(cx, cy, r_ext, r_int, giro=0.0):
    """Puntos de una estrella de cuatro puntas (destello)."""
    pts = []
    for i in range(8):
        r = r_ext if i % 2 == 0 else r_int
        a = giro + i * math.pi / 4 - math.pi / 2
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def dibujar(tam: int) -> Image.Image:
    """Dibuja el icono a `tam` x `tam` píxeles (sobremuestreado x8 y reducido: bordes suaves)."""
    ss = 8
    n = tam * ss
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    u = lambda v: v * n                      # coordenadas relativas (0..1) -> píxeles
    pequeno = tam <= 32                      # en 16-32 px: menos detalle y trazos más gruesos

    # 1) Fondo: cuadrado redondeado oscuro con aro azul
    radio = u(0.22)
    d.rounded_rectangle([0, 0, n - 1, n - 1], radius=radio, fill=FONDO)
    grosor = u(0.075 if pequeno else 0.04)
    d.rounded_rectangle([0, 0, n - 1, n - 1], radius=radio, outline=AZUL, width=max(1, round(grosor)))

    # 2) Estrella (destello) naranja, parcialmente tapada por la nube
    d.polygon(_estrella(u(0.69), u(0.30), u(0.21), u(0.055)), fill=NARANJA)
    if not pequeno:                          # estrellita pequeña de apoyo
        d.polygon(_estrella(u(0.27), u(0.27), u(0.075), u(0.02)), fill=NARANJA)

    # 3) Nube: tres círculos + base redondeada
    def circulo(cx, cy, r, color):
        d.ellipse([u(cx - r), u(cy - r), u(cx + r), u(cy + r)], fill=color)

    def nube(color, dy=0.0):
        circulo(0.36, 0.60 + dy, 0.145, color)
        circulo(0.53, 0.52 + dy, 0.195, color)
        circulo(0.71, 0.61 + dy, 0.130, color)
        d.rounded_rectangle([u(0.215), u(0.605 + dy), u(0.835), u(0.745 + dy)], radius=u(0.07), fill=color)

    if not pequeno:
        nube(NUBE_SOMBRA, dy=0.018)          # sombra inferior sutil: da volumen
    nube(NUBE)

    # 4) Gotas de lluvia azules (solo en tamaños medianos y grandes)
    if tam >= 48:
        for x in (0.34, 0.50, 0.66):
            d.line([(u(x + 0.025), u(0.80)), (u(x - 0.015), u(0.90))], fill=AZUL, width=max(1, round(u(0.035))))
    elif tam >= 32:
        d.line([(u(0.54), u(0.80)), (u(0.50), u(0.90))], fill=AZUL, width=max(1, round(u(0.06))))

    return img.resize((tam, tam), Image.Resampling.LANCZOS)


def main() -> int:
    marcos = {t: dibujar(t) for t in TAMANOS}
    base = marcos[256]
    destino = AQUI / "icono.ico"
    # La imagen base (256) + un marco propio para cada tamaño: Pillow usa esos marcos en lugar de reducir el de 256.
    base.save(destino, format="ICO", sizes=[(t, t) for t in TAMANOS],
              append_images=[marcos[t] for t in TAMANOS if t != 256])

    # Comprobación: se vuelve a abrir el .ico y se listan los tamaños que contiene
    with Image.open(destino) as ico:
        contenidos = sorted(ico.info.get("sizes", {(ico.width, ico.height)}))
    esperados = sorted((t, t) for t in TAMANOS)
    if contenidos != esperados:
        print(f"⚠️  El .ico contiene {contenidos}; se esperaban {esperados}.")
        print("    Actualiza Pillow (pip install -U Pillow) y vuelve a ejecutar.")
        return 1
    print(f"✅ Creado {destino}  ({destino.stat().st_size // 1024} KB; tamaños: " + ", ".join(f"{w}x{h}" for w, h in contenidos) + ")")

    if "--previa" in sys.argv:
        hoja = Image.new("RGBA", (256 + 40 + 40 + 20 + 128 + 40, 336), "#161b22")
        hoja.paste(base, (40, 40), base)
        y = 40
        for t in (128, 64, 48, 32, 24, 16):
            hoja.paste(marcos[t], (300, y), marcos[t]); y += t + 8
        previa = AQUI / "icono_vista_previa.png"
        hoja.save(previa)
        print(f"🖼️  Vista previa: {previa}")
    print("\nSiguiente paso: python comprobar_antes_de_compilar.py  y  python -m PyInstaller --clean AVAMET_Monitor.spec")
    return 0


if __name__ == "__main__":
    sys.exit(main())
