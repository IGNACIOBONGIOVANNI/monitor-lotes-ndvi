"""Caché en disco de las bandas crudas ya descargadas.

Se guarda lo crudo (DN de rojo, NIR y SCL) y no el NDVI: así, si cambiás las clases
SCL o el umbral de nubes, se recalcula todo sin volver a bajar nada.

Estructura:
    cache/<id_lote>/<AAAA-MM-DD>_<hash_items>.tif   (3 bandas: rojo, nir, scl)
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine

from .busqueda import Escena
from .ndvi import Escala

DIR_DEFAULT = Path(os.environ.get("MONITOR_CACHE_DIR", "cache"))


class Cache:
    def __init__(self, directorio: str | Path = DIR_DEFAULT):
        self.dir = Path(directorio)

    def ruta(self, id_lote: str, fecha: date, ids: list[str] | tuple[str, ...]) -> Path:
        h = hashlib.sha1("|".join(sorted(ids)).encode()).hexdigest()[:8]
        return self.dir / id_lote / f"{fecha.isoformat()}_{h}.tif"

    def leer(self, id_lote: str, fecha: date, ids) -> Escena | None:
        ruta = self.ruta(id_lote, fecha, ids)
        if not ruta.exists():
            return None
        with rasterio.open(ruta) as src:
            datos = src.read()
            meta = json.loads(src.tags()["monitor_lotes"])
            return Escena(
                fecha=fecha,
                rojo=datos[0],
                nir=datos[1],
                scl=datos[2].astype(np.uint8),
                escala_rojo=Escala(**meta["escala_rojo"]),
                escala_nir=Escala(**meta["escala_nir"]),
                epsg=src.crs.to_epsg(),
                transform=tuple(src.transform)[:6],
                ids=tuple(meta["ids"]),
            )

    def guardar(self, id_lote: str, escena: Escena) -> Path:
        ruta = self.ruta(id_lote, escena.fecha, escena.ids)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        alto, ancho = escena.rojo.shape
        meta = {
            "ids": list(escena.ids),
            "escala_rojo": vars(escena.escala_rojo),
            "escala_nir": vars(escena.escala_nir),
        }
        tmp = ruta.with_suffix(".tmp")
        with rasterio.open(
            tmp,
            "w",
            driver="GTiff",
            width=ancho,
            height=alto,
            count=3,
            dtype="uint16",
            crs=f"EPSG:{escena.epsg}",
            transform=Affine(*escena.transform),
            nodata=0,
            compress="deflate",
        ) as dst:
            dst.write(np.stack([escena.rojo, escena.nir, escena.scl.astype(np.uint16)]))
            dst.update_tags(monitor_lotes=json.dumps(meta))
            dst.set_band_description(1, "rojo B04 (DN)")
            dst.set_band_description(2, "nir B08 (DN)")
            dst.set_band_description(3, "scl")
        tmp.replace(ruta)  # escritura atómica: nunca queda un .tif a medias
        return ruta
