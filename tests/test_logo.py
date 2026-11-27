"""El logo de assets/ tiene que ser el que genera scripts/generar_logo.py."""

import importlib.util
from pathlib import Path

from PIL import Image

RAIZ = Path(__file__).parent.parent


def _generador():
    ruta = RAIZ / "scripts" / "generar_logo.py"
    spec = importlib.util.spec_from_file_location("generar_logo", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_logo_svg_coincide_con_el_script():
    assert (RAIZ / "assets" / "logo.svg").read_text(encoding="utf-8") == _generador().svg()


def test_favicon():
    icono = Image.open(RAIZ / "assets" / "favicon.png")
    assert icono.size == (64, 64)
    assert icono.mode == "RGBA"
    assert icono.getpixel((0, 0))[3] == 0  # esquinas transparentes (fondo redondeado)
    assert icono.getpixel((32, 32))[3] == 255
