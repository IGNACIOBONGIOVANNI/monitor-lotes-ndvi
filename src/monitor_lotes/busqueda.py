"""Búsqueda de imágenes Sentinel-2 en catálogos STAC y lectura recortada al lote.

Fuente principal: Earth Search (Element 84), colección sentinel-2-c1-l2a.
Respaldo: Microsoft Planetary Computer, colección sentinel-2-l2a (requiere firmar las URLs).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

import numpy as np
import pystac
from pystac_client import Client

from . import config
from .lotes import Lote
from .ndvi import Escala

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Fuente:
    nombre: str
    url: str
    coleccion: str
    # nombre interno -> clave del asset en ese catálogo
    assets: dict[str, str]
    firmar: bool = False


EARTH_SEARCH = Fuente(
    "earth-search",
    config.EARTH_SEARCH_URL,
    config.EARTH_SEARCH_COLECCION,
    {"red": "red", "nir": "nir", "scl": "scl"},
)
PLANETARY = Fuente(
    "planetary-computer",
    config.PLANETARY_URL,
    config.PLANETARY_COLECCION,
    {"red": "B04", "nir": "B08", "scl": "SCL"},
    firmar=True,
)
FUENTES = {f.nombre: f for f in (EARTH_SEARCH, PLANETARY)}


@dataclass(frozen=True)
class Pasada:
    """Todas las imágenes de un mismo día (un lote puede caer entre dos tiles)."""

    fecha: date
    items: tuple[pystac.Item, ...]
    fuente: Fuente

    @property
    def ids(self) -> list[str]:
        return sorted(i.id for i in self.items)


# ---------------------------------------------------------------- búsqueda


def buscar_pasadas(
    lote: Lote,
    desde: date,
    hasta: date,
    max_nubosidad: float = 80.0,
    fuente: str | None = None,
) -> list[Pasada]:
    """Busca imágenes que tocan el lote. Si no se elige fuente, prueba Earth Search y,
    si falla, Planetary Computer."""
    candidatas = [FUENTES[fuente]] if fuente else [EARTH_SEARCH, PLANETARY]
    ultimo_error: Exception | None = None
    for f in candidatas:
        try:
            items = _buscar(f, lote, desde, hasta, max_nubosidad)
            log.info("%s: %d imágenes encontradas", f.nombre, len(items))
            return _agrupar_por_dia(items, f)
        except Exception as e:  # red, catálogo caído, etc.
            log.warning("Falló la búsqueda en %s: %s", f.nombre, e)
            ultimo_error = e
    raise RuntimeError(f"No se pudo consultar ningún catálogo STAC: {ultimo_error}")


def _buscar(f: Fuente, lote: Lote, desde: date, hasta: date, max_nubosidad: float):
    modificador = None
    if f.firmar:
        import planetary_computer

        modificador = planetary_computer.sign_inplace
    cliente = Client.open(f.url, modifier=modificador)
    busqueda = cliente.search(
        collections=[f.coleccion],
        intersects=lote.geometria.__geo_interface__,
        datetime=f"{desde.isoformat()}/{hasta.isoformat()}",
        # Prefiltro grueso con la nubosidad de la escena entera; el filtro real es por lote
        query={"eo:cloud_cover": {"lt": max_nubosidad}},
    )
    return list(busqueda.items())


def _agrupar_por_dia(items, fuente: Fuente) -> list[Pasada]:
    # Sentinel-2 pasa por Argentina ~14 UTC, así que la fecha UTC coincide con la local.
    por_dia: dict[date, list] = defaultdict(list)
    for it in items:
        por_dia[it.datetime.date()].append(it)
    return [Pasada(d, tuple(por_dia[d]), fuente) for d in sorted(por_dia)]


# ---------------------------------------------------------------- escala y offset


def escala_de(item: pystac.Item, asset: str) -> Escala:
    """Lee scale/offset/nodata de la metadata STAC (extensión raster, 'raster:bands').

    Si el catálogo no lo publica (Planetary Computer), se deduce de la baseline de
    procesamiento: desde la 04.00 (enero 2022) ESA suma 1000 a los DN (offset -0.1).
    """
    bandas = item.assets[asset].extra_fields.get("raster:bands") or [{}]
    b = bandas[0]
    if "scale" in b or "offset" in b:
        return Escala(b.get("scale", 1.0), b.get("offset", 0.0), b.get("nodata", 0))
    if asset.upper() == "SCL":
        return Escala(1.0, 0.0, 0)
    baseline = str(item.properties.get("s2:processing_baseline", "00.00"))
    offset = -0.1 if baseline >= "04.00" else 0.0
    return Escala(0.0001, offset, 0)


# ---------------------------------------------------------------- lectura


@dataclass(frozen=True)
class Escena:
    """Bandas crudas de una pasada, recortadas al lote y en su grilla UTM de 10 m."""

    fecha: date
    rojo: np.ndarray  # uint16, DN
    nir: np.ndarray  # uint16, DN
    scl: np.ndarray  # uint8, clases
    escala_rojo: Escala
    escala_nir: Escala
    epsg: int
    transform: tuple  # affine de 6 valores (a, b, c, d, e, f)
    ids: tuple[str, ...]


def leer_pasada(pasada: Pasada, lote: Lote) -> Escena:
    """Lee SOLO la ventana del lote de cada COG (lecturas parciales por HTTP).

    odc-stac arma una grilla en la UTM del lote, a 10 m, limitada al rectángulo del
    polígono; GDAL pide únicamente los bloques del COG que caen ahí.
    """
    import odc.stac

    odc.stac.configure_rio(cloud_defaults=True)
    f = pasada.fuente
    patch = None
    if f.firmar:
        import planetary_computer

        patch = planetary_computer.sign

    ds = odc.stac.load(
        list(pasada.items),
        bands=[f.assets["red"], f.assets["nir"], f.assets["scl"]],
        geopolygon=lote.geometria,
        crs=f"EPSG:{lote.epsg_utm}",
        resolution=config.RESOLUCION_M,
        groupby="solar_day",  # une los tiles del mismo día
        resampling="nearest",  # no inventa valores (clave para SCL, que son clases)
        patch_url=patch,
    )
    ds = ds.isel(time=0)
    item = pasada.items[0]
    gb = ds.odc.geobox
    return Escena(
        fecha=pasada.fecha,
        rojo=ds[f.assets["red"]].values,
        nir=ds[f.assets["nir"]].values,
        scl=ds[f.assets["scl"]].values.astype(np.uint8),
        escala_rojo=escala_de(item, f.assets["red"]),
        escala_nir=escala_de(item, f.assets["nir"]),
        epsg=lote.epsg_utm,
        transform=tuple(gb.affine)[:6],
        ids=tuple(pasada.ids),
    )
