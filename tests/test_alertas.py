"""Tests de alertas con arrays inventados (sin internet).

Grilla de 50 x 50 píxeles de 10 m = 25 ha. Cada píxel = 0,01 ha.
"""

from datetime import date

import numpy as np
import pytest
from shapely.geometry import box

from monitor_lotes.alertas import (
    CONFIRMADA,
    PENDIENTE,
    SIN_CONFIRMAR,
    Alerta,
    contar_vecinos,
    detectar_alertas,
    zona_afectada,
)
from monitor_lotes.config import Parametros
from monitor_lotes.lotes import a_crs, a_kml, crear_lote, geometria_desde_kml

D1, D2, D3 = date(2025, 12, 3), date(2025, 12, 16), date(2026, 1, 20)


def campo(valor=0.85):
    return np.full((50, 50), valor, dtype=np.float32)


def test_sin_cambios_no_hay_alertas():
    assert detectar_alertas({D1: campo(), D2: campo()}) == []


def test_caida_en_bloque_genera_alerta_con_hectareas():
    despues = campo()
    despues[10:30, 10:30] = 0.30  # 20 x 20 píxeles = 4 ha, caída de 0,55
    (alerta,) = detectar_alertas({D1: campo(), D2: despues})
    assert alerta.fecha == D2 and alerta.fecha_anterior == D1
    assert alerta.hectareas == pytest.approx(4.0)
    assert alerta.pct_lote == pytest.approx(16.0)
    assert alerta.caida_media == pytest.approx(0.55, abs=1e-5)
    assert alerta.dias == 13


def test_caida_chica_no_alcanza_el_umbral():
    despues = campo(0.75)  # baja 0,10 en todo el lote
    assert detectar_alertas({D1: campo(), D2: despues}) == []


def test_umbral_de_caida_configurable():
    despues = campo(0.75)
    alertas = detectar_alertas({D1: campo(), D2: despues}, Parametros(caida_ndvi=0.08))
    assert len(alertas) == 1 and alertas[0].hectareas == pytest.approx(25.0)


def test_superficie_minima():
    despues = campo()
    despues[0:5, 0:5] = 0.2  # 0,25 ha
    assert detectar_alertas({D1: campo(), D2: despues}) == []
    assert len(detectar_alertas({D1: campo(), D2: despues}, Parametros(min_ha_alerta=0.2))) == 1


def test_pixeles_sueltos_se_filtran():
    despues = campo()
    rng = np.random.default_rng(1)
    filas, cols = rng.integers(0, 50, 150), rng.integers(0, 50, 150)
    despues[filas, cols] = 0.2  # ~1,5 ha de píxeles dispersos (ruido)
    assert detectar_alertas({D1: campo(), D2: despues}, Parametros(min_ha_alerta=0.5)) == []
    sin_filtro = Parametros(min_ha_alerta=0.5, min_vecinos_alerta=0)
    assert len(detectar_alertas({D1: campo(), D2: despues}, sin_filtro)) == 1


def test_solo_compara_pixeles_limpios_en_ambas_fechas():
    antes, despues = campo(), campo()
    despues[:, :25] = np.nan  # media grilla con nubes en la fecha nueva
    despues[:, 25:] = 0.3  # la otra mitad cae
    (alerta,) = detectar_alertas({D1: antes, D2: despues})
    assert alerta.hectareas == pytest.approx(12.5)
    assert alerta.pct_lote == pytest.approx(100.0)
    assert not alerta.mascara[:, :25].any()


def test_compara_con_la_fecha_limpia_anterior_no_con_la_primera():
    d2 = campo(0.5)  # cae en D2
    d3 = campo(0.5)  # sin cambio respecto de D2
    alertas = detectar_alertas({D3: d3, D1: campo(), D2: d2})  # desordenado a propósito
    assert [a.fecha for a in alertas] == [D2]


def _con_bloque(valor):
    c = campo()
    c[10:30, 10:30] = valor
    return c


def test_caida_que_persiste_queda_confirmada():
    (alerta,) = detectar_alertas({D1: campo(), D2: _con_bloque(0.3), D3: _con_bloque(0.35)})
    assert alerta.estado == CONFIRMADA
    assert alerta.fecha_control == D3
    assert "seguía baja" in alerta.texto(0.15)


def test_caida_que_se_recupera_queda_sin_confirmar():
    """Típico de bruma o nube fina no detectada: baja y a los 5 días vuelve."""
    (alerta,) = detectar_alertas({D1: campo(), D2: _con_bloque(0.3), D3: campo()})
    assert alerta.estado == SIN_CONFIRMAR
    assert "bruma" in alerta.texto(0.15)


def test_ultima_fecha_queda_pendiente():
    (alerta,) = detectar_alertas({D1: campo(), D2: _con_bloque(0.3)})
    assert alerta.estado == PENDIENTE
    assert alerta.fecha_control is None


def test_texto_aclara_que_no_diagnostica():
    despues = campo()
    despues[:20, :20] = 0.3
    (alerta,) = detectar_alertas({D1: campo(), D3: despues})
    texto = alerta.texto(0.15)
    for palabra in ("cosecha", "granizo", "plagas", "falta de agua", "no diagnostica"):
        assert palabra in texto
    assert "días sin imágenes limpias" in texto  # 48 días entre fechas


def test_contar_vecinos():
    m = np.zeros((3, 3), dtype=bool)
    m[1, 1] = True
    assert contar_vecinos(m)[1, 1] == 0
    assert contar_vecinos(m)[0, 0] == 1
    assert contar_vecinos(np.ones((3, 3), dtype=bool))[1, 1] == 8


def test_zona_afectada_en_wgs84_y_kml():
    lote = crear_lote(box(-60.660, -30.805, -60.652, -30.797), "x")
    minx, _, _, maxy = a_crs(lote.geometria, lote.epsg_utm).bounds
    transform = (10.0, 0.0, minx, 0.0, -10.0, maxy)
    mascara = np.zeros((50, 50), dtype=bool)
    mascara[10:30, 10:30] = True
    alerta = Alerta(D2, D1, 4.0, 16.0, 0.5, mascara, np.zeros((50, 50)))

    zona = zona_afectada(alerta, lote.epsg_utm, transform)
    assert lote.geometria.buffer(0.01).contains(zona)
    assert a_crs(zona, lote.epsg_utm).area / 10_000 == pytest.approx(4.0, rel=1e-3)

    leida = geometria_desde_kml(a_kml(zona, "Zona afectada", "caída & <prueba>"))
    # el KML guarda 7 decimales (~1 cm): la diferencia tiene que ser despreciable
    assert leida.symmetric_difference(zona).area / zona.area < 1e-4


def test_numeros_en_formato_argentino():
    from monitor_lotes.formato import num

    assert num(1234.56) == "1.234,6"
    assert num(0.15, 2) == "0,15"
