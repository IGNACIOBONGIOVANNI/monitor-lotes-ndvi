"""Prueba de humo de la interfaz: la demo con el lote de ejemplo tiene que cargar sin
errores y sin internet (usa los resultados precalculados de data/ejemplo)."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).parent.parent / "app.py")


def test_demo_carga_con_lote_de_ejemplo():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert at.title[0].value == "Monitor de lotes con NDVI"
    etiquetas = [m.label for m in at.metric]
    assert "Superficie" in etiquetas and "NDVI mediano" in etiquetas


def test_demo_muestra_alertas():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert any(s.value.startswith("Alertas por caída") for s in at.subheader)
    assert at.selectbox[0].options  # hay al menos una alerta en el ejemplo
    descargas = [b.proto.label for b in at.get("download_button")]
    assert "Descargar zona (KML)" in descargas


def test_subir_el_umbral_reduce_las_alertas():
    at = AppTest.from_file(APP, default_timeout=60).run()
    antes = len(at.selectbox[0].options)
    at.slider[2].set_value(0.4).run()  # caída mínima 0,40
    assert not at.exception
    despues = len(at.selectbox[0].options) if at.selectbox else 0
    assert despues < antes
