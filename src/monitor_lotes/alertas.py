"""Alertas por caídas bruscas de NDVI entre fechas limpias consecutivas.

Regla: se compara cada fecha limpia con la anterior, píxel a píxel, solo donde el píxel
está limpio en las dos. Si los píxeles que bajan más de `caida_ndvi` suman al menos
`min_ha_alerta` hectáreas, se genera una alerta con la zona afectada.

La app DETECTA un cambio; no sabe por qué pasó. Por eso el texto siempre lista causas
posibles y recomienda recorrer la zona.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
from affine import Affine
from rasterio.features import shapes
from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.ops import unary_union

from .config import AREA_PIXEL_HA, Parametros
from .formato import num
from .lotes import a_crs

DIAS_SIN_IMAGEN_AVISO = 20

# Estados de una alerta
CONFIRMADA = "confirmada"  # la zona sigue baja en la siguiente fecha limpia
SIN_CONFIRMAR = "sin confirmar"  # en la siguiente fecha volvió a la normalidad
PENDIENTE = "pendiente"  # todavía no hay una fecha limpia posterior

CAUSAS = (
    "cosecha, granizo, helada, plagas o enfermedades, falta de agua, anegamiento o un herbicida"
)


@dataclass(frozen=True)
class Alerta:
    fecha: date
    fecha_anterior: date
    hectareas: float
    pct_lote: float  # % de los píxeles comparables del lote
    caida_media: float  # caída promedio de NDVI en la zona afectada (valor positivo)
    mascara: np.ndarray  # True en los píxeles de la zona afectada
    diferencia: np.ndarray  # NDVI(fecha) - NDVI(anterior); NaN si no es comparable
    estado: str = PENDIENTE  # CONFIRMADA / SIN_CONFIRMAR / PENDIENTE
    fecha_control: date | None = None  # fecha limpia siguiente usada para confirmar

    @property
    def dias(self) -> int:
        return (self.fecha - self.fecha_anterior).days

    @property
    def titulo(self) -> str:
        return (
            f"{self.fecha:%d/%m/%Y}: caída de NDVI en {num(self.hectareas)} ha "
            f"({num(self.pct_lote, 0)} % del lote)"
        )

    def texto(self, caida_umbral: float) -> str:
        t = (
            f"Entre el {self.fecha_anterior:%d/%m/%Y} y el {self.fecha:%d/%m/%Y} "
            f"({self.dias} días), {num(self.hectareas)} ha bajaron más de {num(caida_umbral, 2)} "
            f"de NDVI (caída media en la zona: {num(self.caida_media, 2)}). "
            f"Puede deberse a {CAUSAS}, entre otras causas: la app detecta el cambio, "
            "no diagnostica. Conviene recorrer la zona marcada."
        )
        if self.estado == CONFIRMADA:
            t += f" La zona seguía baja en la imagen del {self.fecha_control:%d/%m/%Y}."
        elif self.estado == SIN_CONFIRMAR:
            t += (
                f" En la imagen del {self.fecha_control:%d/%m/%Y} la zona volvió a valores "
                "cercanos a los previos: puede haber sido un efecto atmosférico (bruma, una "
                "nube fina que la máscara no detectó) o un cambio pasajero."
            )
        else:
            t += " Todavía no hay una imagen limpia posterior para confirmarla."
        if self.dias > DIAS_SIN_IMAGEN_AVISO:
            t += (
                f" Ojo: hubo {self.dias} días sin imágenes limpias, así que el cambio pudo "
                "pasar en cualquier momento de ese período."
            )
        return t


def contar_vecinos(m: np.ndarray) -> np.ndarray:
    """Cuántos de los 8 vecinos de cada píxel son True."""
    alto, ancho = m.shape
    p = np.pad(m.astype(np.uint8), 1)
    total = np.zeros((alto, ancho), dtype=np.uint8)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy or dx:
                total += p[1 + dy : 1 + dy + alto, 1 + dx : 1 + dx + ancho]
    return total


def comparar(
    previo: np.ndarray, actual: np.ndarray, params: Parametros
) -> tuple[np.ndarray, np.ndarray, int]:
    """Devuelve (diferencia, máscara de caída, píxeles comparables)."""
    comparables = np.isfinite(previo) & np.isfinite(actual)
    diferencia = np.where(comparables, actual - previo, np.nan).astype(np.float32)
    with np.errstate(invalid="ignore"):
        cae = comparables & (diferencia <= -params.caida_ndvi)
    if params.min_vecinos_alerta > 0:
        cae &= contar_vecinos(cae) >= params.min_vecinos_alerta
    return diferencia, cae, int(comparables.sum())


def detectar_alertas(
    ndvi: dict[date, np.ndarray], params: Parametros | None = None
) -> list[Alerta]:
    """Recorre las fechas limpias en orden y compara cada una con la anterior.

    Después intenta confirmar cada alerta con la fecha limpia siguiente.
    """
    params = params or Parametros()
    fechas = sorted(ndvi)
    alertas = []
    for i in range(1, len(fechas)):
        anterior, fecha = fechas[i - 1], fechas[i]
        diferencia, cae, n_comparables = comparar(ndvi[anterior], ndvi[fecha], params)
        hectareas = float(cae.sum()) * AREA_PIXEL_HA
        if hectareas < params.min_ha_alerta or n_comparables == 0:
            continue
        control = fechas[i + 1] if i + 1 < len(fechas) else None
        estado = PENDIENTE
        if control is not None:
            estado = confirmar(ndvi[anterior], ndvi[control], cae, params)
        alertas.append(
            Alerta(
                fecha=fecha,
                fecha_anterior=anterior,
                hectareas=hectareas,
                pct_lote=100.0 * cae.sum() / n_comparables,
                caida_media=float(-diferencia[cae].mean()),
                mascara=cae,
                diferencia=diferencia,
                estado=estado,
                fecha_control=control,
            )
        )
    return alertas


def confirmar(previo: np.ndarray, control: np.ndarray, zona: np.ndarray, params: Parametros) -> str:
    """¿La zona sigue por debajo del nivel previo en la fecha limpia siguiente?

    Un cosechado, un granizo o una helada persisten; la bruma o una nube fina no
    detectada desaparecen en la pasada siguiente. Es la misma idea de "confirmación
    con observaciones sucesivas" que usan los sistemas de alertas de deforestación.
    """
    comparables = zona & np.isfinite(control) & np.isfinite(previo)
    if comparables.sum() == 0:
        return PENDIENTE
    with np.errstate(invalid="ignore"):
        sigue_baja = control[comparables] <= previo[comparables] - params.caida_ndvi
    return CONFIRMADA if sigue_baja.mean() >= params.fraccion_confirmacion else SIN_CONFIRMAR


def zona_afectada(alerta: Alerta, epsg: int, transform: tuple) -> Polygon | MultiPolygon:
    """Convierte la máscara de píxeles en polígonos (WGS84) para dibujar o exportar a KML."""
    partes = [
        shape(geom)
        for geom, valor in shapes(
            alerta.mascara.astype(np.uint8), mask=alerta.mascara, transform=Affine(*transform)
        )
        if valor == 1
    ]
    zona_utm = unary_union(partes)
    return a_crs(zona_utm, 4326, desde_epsg=epsg)
