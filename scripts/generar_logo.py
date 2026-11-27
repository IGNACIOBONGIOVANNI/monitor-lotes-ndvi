"""Genera el logo de la app: assets/logo.svg, assets/logo.png y assets/favicon.png.

Idea: un potrero visto desde el satélite, levemente girado (como un lote real dibujado en el
mapa), coloreado por zonas con la misma escala de NDVI de la app, con los surcos marcados y
una mancha naranja: una zona con alerta, donde el NDVI cayó. El SVG y los PNG salen de las
mismas formas.

Uso:  python scripts/generar_logo.py
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

RAIZ = Path(__file__).resolve().parent.parent
SALIDA = RAIZ / "assets"

LADO = 512  # el diseño se define en un cuadrado de 512 x 512
FONDO = "#0f3b2c"  # verde muy oscuro
RADIO_FONDO = 112
BLANCO = "#ffffff"

# Lote: rectángulo girado (negativo = antihorario en pantalla)
CENTRO = (256, 262)
ANCHO, ALTO = 312, 262
GIRO_GRADOS = -12
BORDE = 16  # borde blanco alrededor del lote

# Zonas del lote en coordenadas del lote (x en [-156, 156], y en [-131, 131]), con colores de
# la escala de NDVI de la app (ColorBrewer YlGn) y el naranja de las alertas.
ZONAS = [
    ([(-156, -131), (156, -131), (156, 131), (-156, 131)], "#41ab5d"),  # base: cultivo
    ([(-156, -131), (156, -131), (156, -52), (-156, 4)], "#238443"),  # más vigoroso
    ([(-156, 62), (-30, 131), (-156, 131)], "#78c679"),  # menos vigoroso
    (
        [
            (40, 18),
            (62, -10),
            (98, -26),
            (132, -22),
            (156, -2),
            (156, 131),
            (92, 131),
            (58, 112),
            (34, 80),
            (28, 46),
        ],
        "#ec835a",
    ),  # alerta: zona donde cayó el NDVI
]
# Surcos: líneas claras y semitransparentes a lo largo del lote
SURCO_SEPARACION = 26
SURCO_ANCHO = 5
SURCO_COLOR = (255, 255, 255)
SURCO_OPACIDAD = 0.22


def _girar(x: float, y: float) -> tuple[float, float]:
    a = math.radians(GIRO_GRADOS)
    cx, cy = CENTRO
    return (cx + x * math.cos(a) - y * math.sin(a), cy + x * math.sin(a) + y * math.cos(a))


def formas() -> list[tuple[list[tuple[float, float]], str]]:
    """Polígonos del logo en orden de dibujo (sin el fondo ni los surcos)."""
    mw, mh = ANCHO / 2 + BORDE, ALTO / 2 + BORDE
    borde = [_girar(-mw, -mh), _girar(mw, -mh), _girar(mw, mh), _girar(-mw, mh)]
    return [(borde, BLANCO)] + [([_girar(x, y) for x, y in pts], c) for pts, c in ZONAS]


def surcos() -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Segmentos de los surcos, de borde a borde del lote."""
    mw, mh = ANCHO / 2, ALTO / 2
    salida = []
    y = -mh + SURCO_SEPARACION / 2
    while y < mh:
        salida.append((_girar(-mw, y), _girar(mw, y)))
        y += SURCO_SEPARACION
    return salida


def svg() -> str:
    partes = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {LADO} {LADO}" '
        f'width="{LADO}" height="{LADO}">',
        "<title>Monitor de lotes</title>",
        f'<rect width="{LADO}" height="{LADO}" rx="{RADIO_FONDO}" fill="{FONDO}"/>',
    ]
    for puntos, color in formas():
        coords = " ".join(f"{x:.2f},{y:.2f}" for x, y in puntos)
        partes.append(f'<polygon points="{coords}" fill="{color}"/>')
    r, g, b = SURCO_COLOR
    partes.append(
        f'<g stroke="rgb({r},{g},{b})" stroke-opacity="{SURCO_OPACIDAD}" '
        f'stroke-width="{SURCO_ANCHO}">'
    )
    for (x0, y0), (x1, y1) in surcos():
        partes.append(f'<line x1="{x0:.2f}" y1="{y0:.2f}" x2="{x1:.2f}" y2="{y1:.2f}"/>')
    partes.append("</g>")
    partes.append("</svg>")
    return "\n".join(partes) + "\n"


def png(tamano: int, sobremuestreo: int = 4) -> Image.Image:
    """Dibuja en grande y achica: así los bordes quedan suaves (antialiasing)."""
    escala = tamano * sobremuestreo / LADO
    lado = tamano * sobremuestreo
    lienzo = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    dibujo = ImageDraw.Draw(lienzo)
    dibujo.rounded_rectangle([0, 0, lado - 1, lado - 1], radius=RADIO_FONDO * escala, fill=FONDO)
    for puntos, color in formas():
        dibujo.polygon([(x * escala, y * escala) for x, y in puntos], fill=color)

    # Los surcos van en una capa aparte para poder hacerlos semitransparentes
    capa = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    trazo = ImageDraw.Draw(capa)
    alfa = round(255 * SURCO_OPACIDAD)
    for (x0, y0), (x1, y1) in surcos():
        trazo.line(
            [(x0 * escala, y0 * escala), (x1 * escala, y1 * escala)],
            fill=(*SURCO_COLOR, alfa),
            width=max(1, round(SURCO_ANCHO * escala)),
        )
    lienzo = Image.alpha_composite(lienzo, capa)
    return lienzo.resize((tamano, tamano), Image.Resampling.LANCZOS)


def main() -> None:
    SALIDA.mkdir(exist_ok=True)
    (SALIDA / "logo.svg").write_text(svg(), encoding="utf-8", newline="\n")
    png(512).save(SALIDA / "logo.png", optimize=True)
    png(64, sobremuestreo=8).save(SALIDA / "favicon.png", optimize=True)
    print(f"Logo generado en {SALIDA}")


if __name__ == "__main__":
    main()
