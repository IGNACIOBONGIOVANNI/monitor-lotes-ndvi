"""Tests de las manijas para girar y mover lotes (manijas.js + su inserción en folium)."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import folium
import pytest
from folium.plugins import Draw

from monitor_lotes.manijas import ManijasLote

JS = Path(__file__).parent.parent / "src" / "monitor_lotes" / "manijas.js"
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="Node.js no está instalado")
def test_geometria_de_las_manijas_en_node():
    """Centroide, giro y ángulo, probados en JavaScript real (sin Leaflet)."""
    script = f"""
    const M = require({json.dumps(str(JS))});
    const assert = require("assert");
    const cerca = (a, b) => Math.abs(a - b) < 1e-9;

    // Centroide de un rectángulo (con o sin el punto de cierre repetido)
    const r = [{{x: 0, y: 0}}, {{x: 20, y: 0}}, {{x: 20, y: 10}}, {{x: 0, y: 10}}];
    let c = M.centroide(r);
    assert(cerca(c.x, 10) && cerca(c.y, 5));
    c = M.centroide(r.concat([r[0]]));
    assert(cerca(c.x, 10) && cerca(c.y, 5));

    // En pantalla y crece hacia abajo: +90° lleva "derecha" a "abajo" (sentido horario)
    const p = M.girarPunto({{x: 10, y: 0}}, {{x: 0, y: 0}}, 90);
    assert(cerca(p.x, 0) && cerca(p.y, 10));

    // Girar no cambia la superficie (fórmula del zapato)
    const area = (pts) => Math.abs(pts.reduce((s, a, i) => {{
      const b = pts[(i + 1) % pts.length];
      return s + a.x * b.y - b.x * a.y;
    }}, 0)) / 2;
    const girado = r.map((q) => M.girarPunto(q, {{x: 10, y: 5}}, 33));
    assert(cerca(area(girado), area(r)));
    // y el centro queda en su lugar
    c = M.centroide(girado);
    assert(Math.abs(c.x - 10) < 1e-6 && Math.abs(c.y - 5) < 1e-6);

    // Ángulo que giró la manija alrededor del centro, en grados enteros
    const o = {{x: 0, y: 0}};
    assert.strictEqual(M.anguloGrados(o, {{x: 10, y: 0}}, {{x: 0, y: 10}}), 90);
    assert.strictEqual(M.anguloGrados(o, {{x: 10, y: 0}}, {{x: 0, y: -10}}), -90);
    assert.strictEqual(M.anguloGrados(o, {{x: 0, y: -10}}, {{x: -10, y: 0}}), -90);
    assert.strictEqual(M.anguloGrados(o, {{x: 10, y: 0}}, {{x: -10, y: 0.001}}), 180);
    assert.strictEqual(M.anguloGrados(o, {{x: 10, y: 0}}, {{x: 10, y: 0.3}}), 2);
    console.log("ok");
    """
    salida = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    assert salida.returncode == 0, salida.stderr
    assert salida.stdout.strip() == "ok"


def _mapa_con_manijas():
    m = folium.Map(location=(-30.8, -60.6), zoom_start=13)
    grupo = folium.FeatureGroup(name="Dibujos").add_to(m)
    folium.Polygon([(-30.80, -60.66), (-30.80, -60.65), (-30.79, -60.65)]).add_to(grupo)
    Draw(feature_group=grupo).add_to(m)
    ManijasLote(grupo).add_to(m)
    return m, grupo


def test_manijas_se_insertan_en_el_mapa_de_folium():
    m, grupo = _mapa_con_manijas()
    html = m.get_root().render()
    assert f"ManijasLote.instalar({m.get_name()}, {grupo.get_name()});" in html
    assert "var ManijasLote" in html


def test_manijas_apuntan_al_grupo_que_lee_streamlit_folium():
    """streamlit-folium renombra las variables del mapa: la llamada tiene que seguir apuntando
    al mismo grupo que usa como `drawnItems` para devolverle los dibujos a Python."""
    from streamlit_folium import _get_map_string

    m, _ = _mapa_con_manijas()
    js = _get_map_string(m)
    mapa, grupo = re.search(r"ManijasLote\.instalar\((\w+), (\w+)\);", js).groups()
    assert mapa == "map_div"
    assert re.search(rf"var drawnItems\s*=\s*{grupo};", js)
    assert re.search(rf"var {grupo}\s*=", js)
