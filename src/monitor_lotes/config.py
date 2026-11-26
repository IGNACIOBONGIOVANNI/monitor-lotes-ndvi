"""Parámetros configurables del monitor.

Todos los umbrales viven acá para poder cambiarlos sin tocar la lógica
(y para poder explicarlos en un solo lugar).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Catálogos STAC
EARTH_SEARCH_URL = "https://earth-search.aws.element84.com/v1"
EARTH_SEARCH_COLECCION = "sentinel-2-c1-l2a"
PLANETARY_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
PLANETARY_COLECCION = "sentinel-2-l2a"

# Nombres de banda que usa el resto del código (independientes del catálogo)
BANDAS = ("red", "nir", "scl")

# Clases de la Scene Classification Layer (SCL) de Sen2Cor
SCL_CLASES = {
    0: "sin datos",
    1: "saturado o defectuoso",
    2: "sombra topográfica / área oscura",
    3: "sombra de nube",
    4: "vegetación",
    5: "suelo desnudo",
    6: "agua",
    7: "sin clasificar",
    8: "nube (probabilidad media)",
    9: "nube (probabilidad alta)",
    10: "cirro",
    11: "nieve o hielo",
}

# Píxeles que se consideran limpios. Se descartan: sin datos (0),
# saturados (1), sombras (2, 3), nubes (8, 9), cirros (10) y nieve (11).
SCL_VALIDAS_DEFAULT = frozenset({4, 5, 6, 7})

RESOLUCION_M = 10  # resolución nativa de B04 y B08
AREA_PIXEL_HA = RESOLUCION_M * RESOLUCION_M / 10_000  # 0,01 ha


@dataclass(frozen=True)
class Parametros:
    """Umbrales del análisis. Los valores por defecto son razonables para la región pampeana."""

    # Fracción mínima del lote con píxeles limpios para usar una fecha
    min_fraccion_limpia: float = 0.80
    # Prefiltro por nubosidad de la escena completa (%); el filtro real es por lote
    max_nubosidad_escena: float = 80.0
    # Clases SCL aceptadas como píxel limpio
    scl_validas: frozenset[int] = field(default_factory=lambda: SCL_VALIDAS_DEFAULT)
    # Alertas: caída mínima de NDVI por píxel y superficie mínima afectada
    caida_ndvi: float = 0.15
    min_ha_alerta: float = 1.0
    # Un píxel en caída cuenta solo si al menos N de sus 8 vecinos también caen.
    # Filtra píxeles sueltos (bordes de nubes que SCL no marcó, pequeños desfasajes
    # entre imágenes). 0 = sin filtro.
    min_vecinos_alerta: int = 2
    # Confirmación: fracción de la zona que debe seguir baja en la fecha limpia siguiente
    fraccion_confirmacion: float = 0.5
