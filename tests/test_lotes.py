import json

import pytest
from shapely.geometry import box

from monitor_lotes.lotes import (
    LoteInvalidoError,
    crear_lote,
    geometria_desde_kml,
    lote_desde_texto,
    zona_utm_epsg,
)

KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document><Placemark><name>Lote 1</name>
    <Polygon><outerBoundaryIs><LinearRing><coordinates>
      -60.60,-30.80,0 -60.59,-30.80,0 -60.59,-30.79,0 -60.60,-30.79,0 -60.60,-30.80,0
    </coordinates></LinearRing></outerBoundaryIs></Polygon>
  </Placemark></Document>
</kml>"""


def test_kml_de_google_earth():
    geom = geometria_desde_kml(KML)
    assert geom.geom_type == "Polygon"
    assert geom.bounds == pytest.approx((-60.60, -30.80, -60.59, -30.79))


def test_kml_toma_el_nombre_del_placemark():
    assert lote_desde_texto(KML, ".kml", "archivo").nombre == "Lote 1"


def test_geojson_toma_el_nombre_de_las_propiedades():
    feat = {
        "type": "Feature",
        "properties": {"nombre": "La Esquina"},
        "geometry": box(-60.60, -30.80, -60.59, -30.79).__geo_interface__,
    }
    assert lote_desde_texto(json.dumps(feat), ".geojson", "archivo").nombre == "La Esquina"


def test_kml_con_hueco():
    kml = KML.replace(
        "</outerBoundaryIs>",
        "</outerBoundaryIs><innerBoundaryIs><LinearRing><coordinates>"
        "-60.597,-30.797 -60.593,-30.797 -60.593,-30.793 -60.597,-30.793 -60.597,-30.797"
        "</coordinates></LinearRing></innerBoundaryIs>",
    )
    geom = geometria_desde_kml(kml)
    assert len(geom.interiors) == 1


def test_kml_sin_poligono():
    with pytest.raises(LoteInvalidoError):
        geometria_desde_kml("<kml><Document/></kml>")


def test_geojson_feature_collection():
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": box(-60.60, -30.80, -60.59, -30.79).__geo_interface__,
            }
        ],
    }
    lote = lote_desde_texto(json.dumps(fc), ".geojson", "prueba")
    assert lote.nombre == "prueba"


@pytest.mark.parametrize(
    ("lon", "lat", "epsg"),
    [
        (-60.6, -30.8, 32720),  # San Justo, Santa Fe
        (-64.2, -31.4, 32720),  # Córdoba
        (-58.4, -34.6, 32721),  # Buenos Aires
        (2.35, 48.85, 32631),  # París (hemisferio norte)
    ],
)
def test_zona_utm(lon, lat, epsg):
    assert zona_utm_epsg(box(lon - 0.01, lat - 0.01, lon + 0.01, lat + 0.01)) == epsg


def test_hectareas_en_utm():
    # Cuadrado de 0,01° x 0,01° en San Justo: ~957 m (lon) x ~1109 m (lat) -> ~106 ha
    lote = crear_lote(box(-60.60, -30.80, -60.59, -30.79), "cuadrado")
    assert lote.hectareas == pytest.approx(106.1, rel=0.01)


def test_lote_demasiado_grande():
    with pytest.raises(LoteInvalidoError, match="máximo"):
        crear_lote(box(-61, -31, -60, -30), "enorme")


def test_id_estable():
    a = crear_lote(box(-60.60, -30.80, -60.59, -30.79), "a")
    b = crear_lote(box(-60.60, -30.80, -60.59, -30.79), "b")
    assert a.id == b.id


# ---------------------------------------------------------------- dibujo

RECTANGULO = box(-60.660, -30.805, -60.652, -30.797).__geo_interface__


def test_lote_desde_dibujo():
    from monitor_lotes.lotes import hectareas_wgs84, lote_desde_dibujo

    lote = lote_desde_dibujo(RECTANGULO)
    assert lote.nombre == "Lote dibujado"
    assert lote.hectareas == pytest.approx(67.9, rel=0.01)
    assert hectareas_wgs84(lote.geometria) == pytest.approx(lote.hectareas)
