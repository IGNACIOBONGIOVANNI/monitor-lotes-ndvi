"""Carga y geometría de lotes.

Un lote es un polígono en WGS84 (lon/lat). Se puede leer desde un KML de
Google Earth, desde un GeoJSON o desde un dibujo en el mapa (que también es GeoJSON).
"""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from pyproj import CRS, Transformer
from shapely import force_2d, make_valid, set_precision
from shapely import transform as transformar
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

# Límite de tamaño para que la app siga siendo liviana (también en Streamlit Cloud)
MAX_HA = 5_000.0


class LoteInvalidoError(ValueError):
    """El archivo o la geometría no describen un lote usable."""


@dataclass(frozen=True)
class Lote:
    nombre: str
    geometria: Polygon | MultiPolygon  # WGS84 (EPSG:4326), lon/lat

    @property
    def epsg_utm(self) -> int:
        return zona_utm_epsg(self.geometria)

    @property
    def geometria_utm(self) -> Polygon | MultiPolygon:
        return a_crs(self.geometria, self.epsg_utm)

    @property
    def hectareas(self) -> float:
        return self.geometria_utm.area / 10_000

    @property
    def id(self) -> str:
        return id_geometria(self.geometria)

    def a_geojson(self) -> dict:
        return {
            "type": "Feature",
            "properties": {"nombre": self.nombre},
            "geometry": mapping(self.geometria),
        }


# ---------------------------------------------------------------- lectura


def leer_lote(ruta: str | Path, nombre: str | None = None) -> Lote:
    """Lee un lote desde un archivo .kml, .geojson o .json."""
    ruta = Path(ruta)
    texto = ruta.read_text(encoding="utf-8")
    return lote_desde_texto(texto, ruta.suffix, nombre or ruta.stem)


def lote_desde_texto(texto: str, extension: str, nombre: str) -> Lote:
    ext = extension.lower().lstrip(".")
    if ext == "kml":
        geom = geometria_desde_kml(texto)
        nombre = _nombre_kml(texto) or nombre
    elif ext in ("geojson", "json"):
        datos = json.loads(texto)
        geom = geometria_desde_geojson(datos)
        nombre = _nombre_geojson(datos) or nombre
    else:
        raise LoteInvalidoError(f"Formato no soportado: .{ext} (usá .kml o .geojson)")
    return crear_lote(geom, nombre)


def crear_lote(geom: BaseGeometry, nombre: str) -> Lote:
    """Valida la geometría y arma el Lote."""
    geom = _solo_poligonos(make_valid(geom))
    if geom.is_empty:
        raise LoteInvalidoError("El lote no tiene ningún polígono.")
    minx, miny, maxx, maxy = geom.bounds
    if not (-180 <= minx <= maxx <= 180 and -90 <= miny <= maxy <= 90):
        raise LoteInvalidoError("Las coordenadas no parecen lon/lat (WGS84).")
    lote = Lote(nombre=nombre, geometria=geom)
    if lote.hectareas > MAX_HA:
        raise LoteInvalidoError(
            f"El lote tiene {lote.hectareas:,.0f} ha; el máximo es {MAX_HA:,.0f} ha."
        )
    if lote.hectareas < 0.5:
        raise LoteInvalidoError("El lote es muy chico (menos de 0,5 ha).")
    return lote


def geometria_desde_geojson(datos: dict) -> Polygon | MultiPolygon:
    """Acepta Geometry, Feature o FeatureCollection. Une todos los polígonos."""
    tipo = datos.get("type")
    if tipo == "FeatureCollection":
        geoms = [shape(f["geometry"]) for f in datos.get("features", []) if f.get("geometry")]
    elif tipo == "Feature":
        geoms = [shape(datos["geometry"])] if datos.get("geometry") else []
    elif tipo:
        geoms = [shape(datos)]
    else:
        raise LoteInvalidoError("El GeoJSON no tiene 'type'.")
    return _solo_poligonos(unary_union([_a_2d(g) for g in geoms]))


def _nombre_geojson(datos: dict) -> str | None:
    """Usa la propiedad 'nombre' (o 'name') del primer Feature, si existe."""
    if datos.get("type") == "FeatureCollection":
        feats = datos.get("features") or [{}]
        datos = feats[0]
    props = datos.get("properties") or {}
    valor = props.get("nombre") or props.get("name")
    return str(valor) if valor else None


def geometria_desde_kml(texto: str) -> Polygon | MultiPolygon:
    """Parser mínimo de KML: toma todos los <Polygon>, con sus huecos.

    Los KML de Google Earth son simples y este parser evita depender del
    driver KML de GDAL, que no siempre viene compilado igual en cada sistema.
    """
    try:
        raiz = ET.fromstring(texto.encode("utf-8"))
    except ET.ParseError as e:
        raise LoteInvalidoError(f"El KML no es XML válido: {e}") from e

    poligonos = []
    for pol in raiz.iter():
        if _local(pol.tag) != "Polygon":
            continue
        exterior, huecos = None, []
        for borde in pol:
            nombre = _local(borde.tag)
            if nombre not in ("outerBoundaryIs", "innerBoundaryIs"):
                continue
            coords = _coords_de(borde)
            if len(coords) < 3:
                continue
            if nombre == "outerBoundaryIs":
                exterior = coords
            else:
                huecos.append(coords)
        if exterior:
            poligonos.append(Polygon(exterior, huecos))

    if not poligonos:
        raise LoteInvalidoError("No encontré ningún polígono en el KML.")
    return _solo_poligonos(unary_union(poligonos))


def _nombre_kml(texto: str) -> str | None:
    """Nombre del primer <Placemark> (lo que se ve en Google Earth)."""
    try:
        raiz = ET.fromstring(texto.encode("utf-8"))
    except ET.ParseError:
        return None
    for e in raiz.iter():
        if _local(e.tag) == "Placemark":
            for hijo in e:
                if _local(hijo.tag) == "name" and hijo.text and hijo.text.strip():
                    return hijo.text.strip()
    return None


def _local(tag: str) -> str:
    """Saca el namespace: '{http://www.opengis.net/kml/2.2}Polygon' -> 'Polygon'."""
    return tag.rsplit("}", 1)[-1]


def _coords_de(elemento: ET.Element) -> list[tuple[float, float]]:
    for e in elemento.iter():
        if _local(e.tag) == "coordinates" and e.text:
            puntos = []
            for tupla in e.text.split():
                partes = tupla.split(",")
                puntos.append((float(partes[0]), float(partes[1])))  # se ignora la altura
            return puntos
    return []


def _a_2d(geom: BaseGeometry) -> BaseGeometry:
    return force_2d(geom)


def _solo_poligonos(geom: BaseGeometry) -> Polygon | MultiPolygon:
    """Se queda solo con las partes de área (descarta líneas y puntos sueltos)."""
    if isinstance(geom, (Polygon, MultiPolygon)):
        return geom
    partes = [g for g in getattr(geom, "geoms", []) if isinstance(g, (Polygon, MultiPolygon))]
    if not partes:
        return Polygon()
    u = unary_union(partes)
    return u if isinstance(u, (Polygon, MultiPolygon)) else Polygon()


# ---------------------------------------------------------------- proyección


def zona_utm_epsg(geom: BaseGeometry) -> int:
    """EPSG de la zona UTM que contiene el centroide del lote.

    Zona = floor((lon + 180) / 6) + 1. Hemisferio norte: 326xx, sur: 327xx.
    Ejemplo: San Justo, Santa Fe (lon -60.6, lat -30.8) -> zona 20 sur -> EPSG:32720.
    """
    c = geom.centroid
    zona = int((c.x + 180) // 6) + 1
    zona = min(max(zona, 1), 60)
    return (32600 if c.y >= 0 else 32700) + zona


def a_crs(geom: BaseGeometry, epsg: int, desde_epsg: int = 4326) -> BaseGeometry:
    t = Transformer.from_crs(CRS.from_epsg(desde_epsg), CRS.from_epsg(epsg), always_xy=True)
    return transformar(geom, lambda x, y: t.transform(x, y), interleaved=False)


def id_geometria(geom: BaseGeometry) -> str:
    """Identificador estable del lote (para la caché): hash de la geometría redondeada a ~1 cm."""
    norm = set_precision(geom, 1e-7).normalize()
    return hashlib.sha1(norm.wkb).hexdigest()[:12]
