"""Cálculo de NDVI a partir de las bandas crudas de Sentinel-2.

Todas las funciones trabajan con arrays de numpy, sin red ni archivos,
para que se puedan testear con datos inventados.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import Parametros


@dataclass(frozen=True)
class Escala:
    """Conversión de número digital (DN) a reflectancia: refl = DN * scale + offset."""

    scale: float = 1.0
    offset: float = 0.0
    nodata: float | None = 0


def a_reflectancia(dn: np.ndarray, escala: Escala) -> np.ndarray:
    """DN (uint16) -> reflectancia (float32). Nodata queda como NaN.

    En Sentinel-2 L2A Collection 1: scale=0.0001 y offset=-0.1, o sea DN 1000 = reflectancia 0.
    Si no se aplica el offset, el NDVI sale sesgado (sobre todo en suelos y cultivos ralos).
    """
    refl = dn.astype(np.float32) * np.float32(escala.scale) + np.float32(escala.offset)
    if escala.nodata is not None:
        refl[dn == escala.nodata] = np.nan
    # Por el offset, píxeles muy oscuros pueden dar reflectancia levemente negativa:
    # físicamente no tiene sentido, se recorta a 0.
    return np.clip(refl, 0, None)


def calcular_ndvi(rojo: np.ndarray, nir: np.ndarray) -> np.ndarray:
    """NDVI = (NIR - Rojo) / (NIR + Rojo). Con reflectancias >= 0 queda siempre en [-1, 1]."""
    with np.errstate(divide="ignore", invalid="ignore"):
        suma = nir + rojo
        ndvi = (nir - rojo) / suma
    ndvi[~(suma > 0)] = np.nan
    return ndvi.astype(np.float32)


def mascara_limpia(scl: np.ndarray, validas: frozenset[int]) -> np.ndarray:
    """True donde el píxel SCL es una clase aceptada (vegetación, suelo, agua, ...)."""
    return np.isin(scl, list(validas))


@dataclass(frozen=True)
class ResultadoFecha:
    ndvi: np.ndarray  # NaN fuera del lote o donde no está limpio
    media: float
    mediana: float
    p10: float
    p90: float
    pct_limpio: float  # % de píxeles del lote que quedaron limpios
    n_pixeles_lote: int
    usable: bool  # pct_limpio >= umbral

    def estadisticas(self) -> dict:
        return {
            "media": self.media,
            "mediana": self.mediana,
            "p10": self.p10,
            "p90": self.p90,
            "pct_limpio": self.pct_limpio,
            "n_pixeles_lote": self.n_pixeles_lote,
            "usable": self.usable,
        }


def procesar_escena(
    rojo_dn: np.ndarray,
    nir_dn: np.ndarray,
    scl: np.ndarray,
    dentro_lote: np.ndarray,
    escala_rojo: Escala,
    escala_nir: Escala,
    params: Parametros | None = None,
) -> ResultadoFecha:
    """Aplica escala, máscara de nubes y máscara del lote; calcula NDVI y estadísticas."""
    params = params or Parametros()
    rojo = a_reflectancia(rojo_dn, escala_rojo)
    nir = a_reflectancia(nir_dn, escala_nir)
    ndvi = calcular_ndvi(rojo, nir)

    limpio = mascara_limpia(scl, params.scl_validas) & np.isfinite(ndvi) & dentro_lote
    n_lote = int(dentro_lote.sum())
    n_limpio = int(limpio.sum())
    pct = 100.0 * n_limpio / n_lote if n_lote else 0.0

    ndvi = np.where(limpio, ndvi, np.nan).astype(np.float32)
    valores = ndvi[limpio]
    if valores.size:
        media = float(valores.mean())
        p10, mediana, p90 = (float(v) for v in np.percentile(valores, [10, 50, 90]))
    else:
        media = mediana = p10 = p90 = float("nan")

    return ResultadoFecha(
        ndvi=ndvi,
        media=media,
        mediana=mediana,
        p10=p10,
        p90=p90,
        pct_limpio=pct,
        n_pixeles_lote=n_lote,
        usable=pct >= 100 * params.min_fraccion_limpia,
    )
