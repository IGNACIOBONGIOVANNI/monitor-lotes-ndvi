import numpy as np
import pytest

from monitor_lotes.config import Parametros
from monitor_lotes.ndvi import (
    Escala,
    a_reflectancia,
    calcular_ndvi,
    mascara_limpia,
    procesar_escena,
)

# Escala real de Sentinel-2 L2A Collection 1 (metadata STAC "raster:bands")
S2 = Escala(scale=0.0001, offset=-0.1, nodata=0)


def dn(reflectancia):
    """Reflectancia -> número digital, como lo guarda Sentinel-2."""
    return np.round((np.asarray(reflectancia) + 0.1) / 0.0001).astype(np.uint16)


def test_reflectancia_aplica_scale_y_offset():
    refl = a_reflectancia(np.array([1000, 1500, 5000], dtype=np.uint16), S2)
    np.testing.assert_allclose(refl, [0.0, 0.05, 0.4], atol=1e-6)


def test_reflectancia_nodata_es_nan():
    refl = a_reflectancia(np.array([0, 2000], dtype=np.uint16), S2)
    assert np.isnan(refl[0]) and refl[1] == pytest.approx(0.1)


def test_reflectancia_negativa_se_recorta():
    assert a_reflectancia(np.array([500], dtype=np.uint16), S2)[0] == 0


@pytest.mark.parametrize(
    ("rojo", "nir", "esperado"),
    [
        (0.04, 0.45, 0.837),  # soja/maíz en pleno crecimiento
        (0.15, 0.25, 0.25),  # suelo desnudo / rastrojo
        (0.05, 0.02, -0.429),  # agua
    ],
)
def test_ndvi_valores_logicos(rojo, nir, esperado):
    """El pipeline completo desde DN tiene que dar NDVI típicos de cada cobertura."""
    r = a_reflectancia(dn([rojo]), S2)
    n = a_reflectancia(dn([nir]), S2)
    assert calcular_ndvi(r, n)[0] == pytest.approx(esperado, abs=0.01)


def test_sin_offset_el_ndvi_da_mal():
    """Documenta por qué importa el offset: ignorarlo subestima el NDVI de la vegetación."""
    sin_offset = Escala(scale=0.0001, offset=0.0)
    r = a_reflectancia(dn([0.04]), sin_offset)
    n = a_reflectancia(dn([0.45]), sin_offset)
    assert calcular_ndvi(r, n)[0] < 0.65  # en vez de ~0.84


def test_ndvi_siempre_entre_menos_uno_y_uno():
    rng = np.random.default_rng(42)
    rojo = a_reflectancia(rng.integers(0, 20000, 10_000, dtype=np.uint16), S2)
    nir = a_reflectancia(rng.integers(0, 20000, 10_000, dtype=np.uint16), S2)
    ndvi = calcular_ndvi(rojo, nir)
    finitos = ndvi[np.isfinite(ndvi)]
    assert finitos.size > 9_000
    assert finitos.min() >= -1 and finitos.max() <= 1


def test_ndvi_con_suma_cero_es_nan():
    assert np.isnan(calcular_ndvi(np.array([0.0]), np.array([0.0]))[0])


def test_mascara_scl():
    scl = np.array([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11])
    limpio = mascara_limpia(scl, Parametros().scl_validas)
    assert limpio.tolist() == [False] * 4 + [True] * 4 + [False] * 4


def _escena(scl_valor_nube, n_nubes):
    """Lote de 10x10 con vegetación; las primeras n_nubes celdas con nubes."""
    rojo = np.full((10, 10), dn(0.04))
    nir = np.full((10, 10), dn(0.45))
    scl = np.full((10, 10), 4, dtype=np.uint8)
    scl.flat[:n_nubes] = scl_valor_nube
    dentro = np.ones((10, 10), dtype=bool)
    return rojo, nir, scl, dentro


def test_fecha_limpia_se_usa():
    r = procesar_escena(*_escena(9, 10), S2, S2)
    assert r.pct_limpio == pytest.approx(90)
    assert r.usable
    assert r.mediana == pytest.approx(0.837, abs=0.01)
    assert np.isnan(r.ndvi.flat[0])


def test_fecha_nublada_se_descarta():
    r = procesar_escena(*_escena(8, 25), S2, S2)
    assert r.pct_limpio == pytest.approx(75)
    assert not r.usable


def test_umbral_configurable():
    r = procesar_escena(*_escena(8, 25), S2, S2, Parametros(min_fraccion_limpia=0.7))
    assert r.usable


def test_porcentaje_solo_cuenta_pixeles_del_lote():
    rojo, nir, scl, dentro = _escena(9, 0)
    dentro[:, 5:] = False  # el lote ocupa la mitad del rectángulo
    scl[:, 5:] = 9  # y las nubes caen afuera del lote
    r = procesar_escena(rojo, nir, scl, dentro, S2, S2)
    assert r.n_pixeles_lote == 50
    assert r.pct_limpio == pytest.approx(100)


def test_estadisticas_percentiles():
    rojo, nir, scl, dentro = _escena(4, 0)
    nir = dn(np.linspace(0.1, 0.5, 100).reshape(10, 10))
    r = procesar_escena(rojo, nir, scl, dentro, S2, S2)
    assert r.p10 < r.mediana < r.p90
    assert r.p10 >= -1 and r.p90 <= 1
