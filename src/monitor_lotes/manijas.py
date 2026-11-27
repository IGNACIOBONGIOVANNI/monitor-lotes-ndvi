"""Manijas para girar y mover lotes dibujados en un mapa de folium.

El comportamiento está en manijas.js; esta clase solo lo inserta en el mapa y le indica qué
FeatureGroup tiene los dibujos (el mismo que se le pasa a `folium.plugins.Draw`).
"""

from __future__ import annotations

from pathlib import Path

import folium
from branca.element import MacroElement
from jinja2 import Template

CODIGO_JS = (Path(__file__).parent / "manijas.js").read_text(encoding="utf-8")


class ManijasLote(MacroElement):
    """Agrega ↻ (girar) y ✥ (mover) a cada polígono del grupo editable de Leaflet.draw."""

    _template = Template(
        """
        {% macro script(this, kwargs) %}
        {{ this.codigo }}
        ManijasLote.instalar({{ this._parent.get_name() }}, {{ this.grupo.get_name() }});
        {% endmacro %}
        """
    )

    def __init__(self, grupo: folium.FeatureGroup):
        super().__init__()
        self._name = "ManijasLote"
        self.grupo = grupo
        self.codigo = CODIGO_JS
