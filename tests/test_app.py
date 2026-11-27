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


def test_modo_dibujo_con_giro():
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.radio(key="origen").set_value("Dibujar en el mapa").run()
    assert not at.exception
    rect = {
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
    at.session_state["dibujos"] = [rect]
    at.run()
    assert not at.exception
    ha = at.session_state["lote"].hectareas
    at.slider(key="giro").set_value(35).run()
    assert not at.exception
    girado = at.session_state["lote"]
    assert girado.hectareas == pytest.approx(ha, rel=1e-3)
    assert girado.geometria.bounds != pytest.approx(
        rect_bounds := (-60.66, -30.805, -60.652, -30.797)
    )
    del rect_bounds


def test_editar_el_dibujo_conserva_el_giro():
    from monitor_lotes.serie import importar

    at = AppTest.from_file(APP, default_timeout=60).run()
    at.radio(key="origen").set_value("Dibujar en el mapa").run()
    at.session_state["dibujos"] = [
        {
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
    ]
    at.run()
    at.slider(key="giro").set_value(25).run()
    # Simula que ya se calculó: se muestran resultados y el control de giro desaparece
    at.session_state["serie"] = importar(Path(APP).parent / "data" / "ejemplo" / "resultados")
    at.run()
    assert not at.exception
    editar = next(b for b in at.button if b.label == "Editar el dibujo")
    editar.click().run()
    assert not at.exception
    assert at.slider(key="giro").value == 25
