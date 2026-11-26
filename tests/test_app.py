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

