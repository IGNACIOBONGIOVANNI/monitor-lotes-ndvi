from datetime import date

import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box

from monitor_lotes.lotes import a_crs, crear_lote
from monitor_lotes.visual import PALETA_NDVI, a_mercator, colorear, figura_serie


def _hex(pixel):
    r, g, b = (int(v) for v in pixel[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def test_colorear_extremos_y_transparencia():
    img = colorear(np.array([[0.0, 1.0, np.nan, 2.0]]))
    assert img.shape == (1, 4, 4)
    assert _hex(img[0, 0]) == PALETA_NDVI[0]
    assert _hex(img[0, 1]) == PALETA_NDVI[-1]
    assert img[0, 2, 3] == 0  # NaN transparente
    assert (img[0, 3] == img[0, 1]).all()  # fuera de rango se satura


def test_colorear_mas_ndvi_es_mas_oscuro():
    img = colorear(np.linspace(0, 1, 20)[None, :]).astype(int)
    luminosidad = img[0, :, :3].sum(axis=1)
    assert (np.diff(luminosidad) <= 0).all()


def test_a_mercator_cubre_el_lote():
    lote = crear_lote(box(-60.660, -30.805, -60.652, -30.797), "x")
    minx, _, _, maxy = a_crs(lote.geometria, lote.epsg_utm).bounds
    datos = np.full((89, 77), 0.5, dtype=np.float32)
    salida, ((sur, oeste), (norte, este)) = a_mercator(
        datos, lote.epsg_utm, (10.0, 0.0, minx, 0.0, -10.0, maxy)
    )
    assert np.isfinite(salida).mean() > 0.9
    assert oeste == pytest.approx(-60.660, abs=1e-3)
    assert norte == pytest.approx(-30.797, abs=1e-3)
    assert sur < norte and oeste < este


def test_figura_serie():
    tabla = pd.DataFrame(
        {
            "fecha": [date(2025, 12, 1), date(2025, 12, 6), date(2025, 12, 11)],
            "mediana": [0.8, np.nan, 0.85],
            "media": [0.78, np.nan, 0.83],
            "p10": [0.6, np.nan, 0.7],
            "p90": [0.9, np.nan, 0.92],
            "pct_limpio": [100, 10, 95],
            "usable": [True, False, True],
        }
    )
    fig = figura_serie(tabla, date(2025, 12, 11))
    nombres = [t.name for t in fig.data]
    assert "Mediana del lote" in nombres and "Descartada (nubes)" in nombres
