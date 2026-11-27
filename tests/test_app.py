"""Prueba de humo de la interfaz: la demo con el lote de ejemplo tiene que cargar sin
errores y sin internet (usa los resultados precalculados de data/ejemplo)."""

from pathlib import Path

import pytest
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


RECT = {
    "type": "Polygon",
    "coordinates": [
        [
            [-60.66, -30.805],
            [-60.652, -30.805],
            [-60.652, -30.797],
            [-60.66, -30.797],
            [-60.66, -30.805],
        ]
    ],
}
# El mismo lote girado ~30° (como llega desde el navegador después de usar la manija ↻)
GIRADO = {
    "type": "Polygon",
    "coordinates": [
        [
            [-60.6609, -30.8016],
            [-60.6553, -30.8073],
            [-60.6511, -30.8004],
            [-60.6567, -30.7947],
            [-60.6609, -30.8016],
        ]
    ],
}


def test_modo_dibujo_arma_el_lote_y_deja_elegir():
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.radio(key="origen").set_value("Dibujar en el mapa").run()
    assert not at.exception
    assert at.session_state["lote"] is None  # todavía no se dibujó nada

    at.session_state["dibujos"] = [RECT]
    at.run()
    assert not at.exception
    assert at.session_state["lote"].hectareas == pytest.approx(67.9, rel=0.01)

    at.session_state["dibujos"] = [RECT, GIRADO]
    at.run()
    assert not at.exception
    elegir = at.selectbox[0]
    assert elegir.label == "¿Cuál dibujo es el lote?"
    elegir.set_value(0).run()
    assert at.session_state["lote"].geometria.bounds == pytest.approx(
        (-60.66, -30.805, -60.652, -30.797)
    )


def test_editar_el_dibujo_vuelve_con_los_dibujos():
    from monitor_lotes.serie import importar

    at = AppTest.from_file(APP, default_timeout=60).run()
    at.radio(key="origen").set_value("Dibujar en el mapa").run()
    at.session_state["dibujos"] = [GIRADO]
    at.run()
    lote = at.session_state["lote"]
    # Simula que ya se calculó: se muestran resultados y el mapa de dibujo desaparece
    at.session_state["serie"] = importar(Path(APP).parent / "data" / "ejemplo" / "resultados")
    at.run()
    assert not at.exception
    editar = next(b for b in at.button if b.label == "Editar el dibujo")
    editar.click().run()
    assert not at.exception
    assert at.session_state["dibujos_iniciales"] == [GIRADO]  # el mapa arranca con lo girado
    assert at.session_state["lote"].id == lote.id
