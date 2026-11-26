"""Serie temporal de NDVI de un lote: búsqueda -> lectura (o caché) -> NDVI -> estadísticas.

También exporta/importa los resultados (CSV + GeoTIFF multibanda), que es lo que usa
la demo para cargar rápido sin ir a internet.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from affine import Affine
from rasterio.features import geometry_mask

from .busqueda import Escena, buscar_pasadas, leer_pasada
from .cache import Cache
from .config import Parametros
from .lotes import Lote, leer_lote
from .ndvi import procesar_escena

log = logging.getLogger(__name__)

COLUMNAS = [
    "fecha",
    "media",
    "mediana",
    "p10",
    "p90",
    "pct_limpio",
    "usable",
    "n_pixeles_lote",
    "fuente",
    "imagenes",
]


@dataclass
class Serie:
    lote: Lote
    tabla: pd.DataFrame  # una fila por pasada (usable o no)
    ndvi: dict[date, np.ndarray] = field(default_factory=dict)  # solo fechas usables
    epsg: int | None = None
    transform: tuple | None = None

    @property
    def usables(self) -> pd.DataFrame:
        return self.tabla[self.tabla["usable"]].reset_index(drop=True)


def mascara_lote(lote: Lote, transform: tuple, forma: tuple[int, int]) -> np.ndarray:
    """True en los píxeles cuyo centro cae dentro del polígono del lote."""
    return geometry_mask(
        [lote.geometria_utm], out_shape=forma, transform=Affine(*transform), invert=True
    )


def calcular_serie(
    lote: Lote,
    desde: date,
    hasta: date,
    params: Parametros | None = None,
    cache: Cache | None = None,
    fuente: str | None = None,
    progreso: Callable[[int, int, str], None] | None = None,
) -> Serie:
    params = params or Parametros()
    cache = cache or Cache()
    pasadas = buscar_pasadas(lote, desde, hasta, params.max_nubosidad_escena, fuente)

    filas, ndvis = [], {}
    epsg = transform = forma = None
    dentro = None
    for i, pasada in enumerate(pasadas, 1):
        if progreso:
            progreso(i, len(pasadas), pasada.fecha.isoformat())
        escena = cache.leer(lote.id, pasada.fecha, pasada.ids)
        if escena is None:
            try:
                escena = leer_pasada(pasada, lote)
            except Exception as e:
                log.warning("No se pudo leer %s: %s", pasada.fecha, e)
                continue
            cache.guardar(lote.id, escena)

        if forma is None:
            epsg, transform, forma = escena.epsg, escena.transform, escena.rojo.shape
            dentro = mascara_lote(lote, transform, forma)
        elif escena.rojo.shape != forma or escena.transform != transform:
            log.warning("La grilla de %s no coincide con la del lote; se omite", pasada.fecha)
            continue

        r = procesar_escena(
            escena.rojo,
            escena.nir,
            escena.scl,
            dentro,
            escena.escala_rojo,
            escena.escala_nir,
            params,
        )
        filas.append(
            {
                "fecha": pasada.fecha,
                **r.estadisticas(),
                "fuente": pasada.fuente.nombre,
                "imagenes": ";".join(pasada.ids),
            }
        )
        if r.usable:
            ndvis[pasada.fecha] = r.ndvi

    tabla = pd.DataFrame(filas, columns=COLUMNAS)
    return Serie(lote=lote, tabla=tabla, ndvi=ndvis, epsg=epsg, transform=transform)


def procesar_desde_escenas(lote: Lote, escenas: list[Escena], params: Parametros) -> Serie:
    """Igual que calcular_serie pero con escenas ya en memoria (útil para tests)."""
    filas, ndvis = [], {}
    e0 = escenas[0]
    dentro = mascara_lote(lote, e0.transform, e0.rojo.shape)
    for e in escenas:
        r = procesar_escena(e.rojo, e.nir, e.scl, dentro, e.escala_rojo, e.escala_nir, params)
        filas.append({"fecha": e.fecha, **r.estadisticas(), "fuente": "", "imagenes": ""})
        if r.usable:
            ndvis[e.fecha] = r.ndvi
    return Serie(lote, pd.DataFrame(filas, columns=COLUMNAS), ndvis, e0.epsg, e0.transform)


# ---------------------------------------------------------------- exportar / importar


def exportar(serie: Serie, directorio: str | Path) -> None:
    """Guarda lote.geojson, serie.csv y ndvi.tif (una banda por fecha usable)."""
    d = Path(directorio)
    d.mkdir(parents=True, exist_ok=True)
    (d / "lote.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": [serie.lote.a_geojson()]}, indent=1),
        encoding="utf-8",
    )
    tabla = serie.tabla.copy()
    tabla["fecha"] = pd.to_datetime(tabla["fecha"]).dt.strftime("%Y-%m-%d")
    tabla.round(4).to_csv(d / "serie.csv", index=False)

    if not serie.ndvi:
        return
    fechas = sorted(serie.ndvi)
    alto, ancho = serie.ndvi[fechas[0]].shape
    with rasterio.open(
        d / "ndvi.tif",
        "w",
        driver="GTiff",
        width=ancho,
        height=alto,
        count=len(fechas),
        dtype="float32",
        crs=f"EPSG:{serie.epsg}",
        transform=Affine(*serie.transform),
        nodata=np.nan,
        compress="deflate",
        predictor=3,
    ) as dst:
        for i, f in enumerate(fechas, 1):
            dst.write(serie.ndvi[f], i)
            dst.set_band_description(i, f.isoformat())


def importar(directorio: str | Path) -> Serie:
    d = Path(directorio)
    lote = leer_lote(d / "lote.geojson")
    tabla = pd.read_csv(d / "serie.csv", dtype={"fuente": object, "imagenes": object})
    tabla["fecha"] = pd.to_datetime(tabla["fecha"]).dt.date
    tabla["usable"] = tabla["usable"].astype(bool)
    tabla["fuente"] = tabla["fuente"].fillna("")
    tabla["imagenes"] = tabla["imagenes"].fillna("")
    ndvis, epsg, transform = {}, None, None
    if (d / "ndvi.tif").exists():
        with rasterio.open(d / "ndvi.tif") as src:
            epsg, transform = src.crs.to_epsg(), tuple(src.transform)[:6]
            for i, desc in enumerate(src.descriptions, 1):
                ndvis[date.fromisoformat(desc)] = src.read(i)
    return Serie(lote, tabla, ndvis, epsg, transform)
