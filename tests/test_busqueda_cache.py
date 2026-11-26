"""Tests de metadata STAC, caché y serie, con objetos inventados (sin internet)."""

from datetime import UTC, date, datetime

import numpy as np
import pystac
import pytest
from shapely.geometry import box

from monitor_lotes.busqueda import EARTH_SEARCH, Escena, _agrupar_por_dia, escala_de
from monitor_lotes.cache import Cache
from monitor_lotes.config import Parametros
from monitor_lotes.lotes import a_crs, crear_lote
from monitor_lotes.ndvi import Escala
from monitor_lotes.serie import exportar, importar, procesar_desde_escenas


def _item(id_, props=None, raster_bands=None, asset="red", dt=None):
    it = pystac.Item(
        id=id_,
        geometry=None,
        bbox=None,
        datetime=dt or datetime(2025, 12, 16, 14, 11, tzinfo=UTC),
        properties=props or {},
    )
    extra = {"raster:bands": raster_bands} if raster_bands else {}
    it.add_asset(asset, pystac.Asset(href="https://x/y.tif", extra_fields=extra))
    return it


def test_escala_desde_raster_bands():
    it = _item("a", raster_bands=[{"nodata": 0, "scale": 0.0001, "offset": -0.1}])
    assert escala_de(it, "red") == Escala(0.0001, -0.1, 0)


@pytest.mark.parametrize(("baseline", "offset"), [("05.11", -0.1), ("04.00", -0.1), ("03.01", 0)])
def test_escala_deducida_por_baseline(baseline, offset):
    """Planetary Computer no publica scale/offset: se deduce de la baseline."""
    it = _item("b", props={"s2:processing_baseline": baseline}, asset="B04")
    assert escala_de(it, "B04") == Escala(0.0001, offset, 0)


def test_agrupa_tiles_del_mismo_dia():
    items = [
        _item("T20JQL", dt=datetime(2025, 12, 16, 14, 11, tzinfo=UTC)),
        _item("T20JQM", dt=datetime(2025, 12, 16, 14, 11, 30, tzinfo=UTC)),
        _item("T20JQL2", dt=datetime(2025, 12, 21, 14, 11, tzinfo=UTC)),
    ]
    pasadas = _agrupar_por_dia(items, EARTH_SEARCH)
    assert [p.fecha for p in pasadas] == [date(2025, 12, 16), date(2025, 12, 21)]
    assert pasadas[0].ids == ["T20JQL", "T20JQM"]


# ---------------------------------------------------------------- escenas sintéticas

LOTE = crear_lote(box(-60.660, -30.805, -60.652, -30.797), "sintetico")
S2 = Escala(0.0001, -0.1, 0)


def _escena(fecha, nir_dn=5500, nubes=0):
    minx, _, _, maxy = a_crs(LOTE.geometria, LOTE.epsg_utm).bounds
    forma = (95, 80)
    scl = np.full(forma, 4, dtype=np.uint8)
    scl.flat[:nubes] = 9
    return Escena(
        fecha=fecha,
        rojo=np.full(forma, 1400, dtype=np.uint16),
        nir=np.full(forma, nir_dn, dtype=np.uint16),
        scl=scl,
        escala_rojo=S2,
        escala_nir=S2,
        epsg=LOTE.epsg_utm,
        transform=(10.0, 0.0, minx, 0.0, -10.0, maxy),
        ids=(f"item-{fecha}",),
    )


def test_cache_ida_y_vuelta(tmp_path):
    cache = Cache(tmp_path)
    e = _escena(date(2025, 12, 16))
    assert cache.leer(LOTE.id, e.fecha, e.ids) is None
    cache.guardar(LOTE.id, e)
    leida = cache.leer(LOTE.id, e.fecha, e.ids)
    np.testing.assert_array_equal(leida.nir, e.nir)
    np.testing.assert_array_equal(leida.scl, e.scl)
    assert leida.escala_rojo == S2
    assert leida.epsg == 32720
    assert leida.transform == pytest.approx(e.transform)


def test_serie_descarta_fecha_nublada_y_exporta(tmp_path):
    escenas = [
        _escena(date(2025, 12, 1)),
        _escena(date(2025, 12, 6), nubes=95 * 80 // 2),  # mitad nublada
        _escena(date(2025, 12, 11), nir_dn=3500),
    ]
    serie = procesar_desde_escenas(LOTE, escenas, Parametros())
    assert serie.tabla["usable"].tolist() == [True, False, True]
    assert len(serie.ndvi) == 2

    exportar(serie, tmp_path)
    otra = importar(tmp_path)
    assert otra.tabla["usable"].tolist() == [True, False, True]
    assert sorted(otra.ndvi) == [date(2025, 12, 1), date(2025, 12, 11)]
    assert np.nanmedian(otra.ndvi[date(2025, 12, 1)]) == pytest.approx(0.837, abs=0.01)
