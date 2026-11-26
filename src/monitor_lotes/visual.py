"""Piezas visuales reutilizables: colores del NDVI, capa para el mapa y gráfico de la serie.

Se separan de app.py para poder testearlas y para que la interfaz quede liviana.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from affine import Affine
from pyproj import Transformer
from rasterio.warp import Resampling, calculate_default_transform, reproject

# Escala secuencial amarillo -> verde (ColorBrewer YlGn, 9 clases).
# La luminosidad baja de forma monótona: se lee igual con daltonismo rojo-verde
# (a diferencia del clásico rojo-amarillo-verde) y "más oscuro = más vegetación".
PALETA_NDVI = [
    "#ffffe5",
    "#f7fcb9",
    "#d9f0a3",
    "#addd8e",
    "#78c679",
    "#41ab5d",
    "#238443",
    "#006837",
    "#004529",
]
# Escala FIJA para todas las fechas: así un mismo color significa lo mismo en
# septiembre y en enero (si se estirara por fecha, cada mapa "se vería verde").
NDVI_MIN, NDVI_MAX = 0.0, 1.0

# Tinta y series (paleta de referencia de la guía de visualización)
AZUL = "#2a78d6"
AZUL_BANDA = "rgba(42, 120, 214, 0.12)"
GRIS_MUDO = "#898781"
TINTA_SECUNDARIA = "#52514e"
GRILLA = "#e1e0d9"


def _hex_a_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def colorear(
    valores: np.ndarray,
    paleta: list[str] = PALETA_NDVI,
    vmin: float = NDVI_MIN,
    vmax: float = NDVI_MAX,
) -> np.ndarray:
    """Array 2D -> imagen RGBA (uint8). NaN queda transparente; fuera de rango se satura."""
    t = np.clip((valores - vmin) / (vmax - vmin), 0, 1)
    paradas = np.linspace(0, 1, len(paleta))
    rgb = np.array([_hex_a_rgb(c) for c in paleta], dtype=float)
    salida = np.zeros((*valores.shape, 4), dtype=np.uint8)
    validos = np.isfinite(valores)
    tv = np.where(validos, t, 0)
    for canal in range(3):
        salida[..., canal] = np.interp(tv, paradas, rgb[:, canal]).round().astype(np.uint8)
    salida[..., 3] = np.where(validos, 255, 0)
    return salida


def a_mercator(
    valores: np.ndarray, epsg: int, transform: tuple
) -> tuple[np.ndarray, list[list[float]]]:
    """Reproyecta de UTM a Web Mercator (EPSG:3857), que es lo que dibuja Leaflet.

    Devuelve el array reproyectado y sus límites [[sur, oeste], [norte, este]] en lat/lon.
    Se usa vecino más cercano para que cada píxel de 10 m siga siendo un píxel real.
    """
    alto, ancho = valores.shape
    t = Affine(*transform)
    izq, arriba = t @ (0, 0)
    der, abajo = t @ (ancho, alto)
    src_crs, dst_crs = f"EPSG:{epsg}", "EPSG:3857"
    dst_t, dst_ancho, dst_alto = calculate_default_transform(
        src_crs, dst_crs, ancho, alto, left=izq, bottom=abajo, right=der, top=arriba
    )
    destino = np.full((dst_alto, dst_ancho), np.nan, dtype=np.float32)
    reproject(
        valores.astype(np.float32),
        destino,
        src_transform=t,
        src_crs=src_crs,
        src_nodata=np.nan,
        dst_transform=dst_t,
        dst_crs=dst_crs,
        dst_nodata=np.nan,
        resampling=Resampling.nearest,
    )
    a_lonlat = Transformer.from_crs(dst_crs, "EPSG:4326", always_xy=True)
    oeste, norte = a_lonlat.transform(*(dst_t @ (0, 0)))
    este, sur = a_lonlat.transform(*(dst_t @ (dst_ancho, dst_alto)))
    return destino, [[sur, oeste], [norte, este]]


# ---------------------------------------------------------------- gráfico


def figura_serie(tabla: pd.DataFrame, fecha_sel: date | None = None):
    """Mediana de NDVI con banda p10-p90; las fechas descartadas como marcas grises."""
    import plotly.graph_objects as go

    ok = tabla[tabla["usable"]]
    malas = tabla[~tabla["usable"]]
    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=ok["fecha"],
            y=ok["p90"],
            mode="lines",
            line={"width": 0},
            hoverinfo="skip",
            showlegend=False,
        )
    )
    fig.add_trace(
        go.Scatter(
            x=ok["fecha"],
            y=ok["p10"],
            mode="lines",
            line={"width": 0},
            fill="tonexty",
            fillcolor=AZUL_BANDA,
            name="Rango p10–p90",
            hoverinfo="skip",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=ok["fecha"],
            y=ok["mediana"],
            mode="lines+markers",
            name="Mediana del lote",
            line={"color": AZUL, "width": 2},
            marker={"size": 8, "color": AZUL, "line": {"color": "#fcfcfb", "width": 2}},
            customdata=ok[["p10", "p90", "pct_limpio", "media"]].to_numpy(),
            hovertemplate=(
                "%{x|%d/%m/%Y}<br><b>NDVI mediana %{y:.2f}</b><br>media %{customdata[3]:.2f}"
                " · p10 %{customdata[0]:.2f} · p90 %{customdata[1]:.2f}"
                "<br>lote limpio %{customdata[2]:.0f}%<extra></extra>"
            ),
        )
    )
    if len(malas):
        fig.add_trace(
            go.Scatter(
                x=malas["fecha"],
                y=np.full(len(malas), 0.02),
                mode="markers",
                name="Descartada (nubes)",
                marker={
                    "symbol": "x-thin",
                    "size": 9,
                    "color": GRIS_MUDO,
                    "line": {"width": 2, "color": GRIS_MUDO},
                },
                customdata=malas[["pct_limpio"]].to_numpy(),
                hovertemplate=(
                    "%{x|%d/%m/%Y}<br>Descartada: lote limpio %{customdata[0]:.0f}%<extra></extra>"
                ),
            )
        )
    if fecha_sel is not None:
        fig.add_vline(
            x=pd.Timestamp(fecha_sel).timestamp() * 1000, line_width=1, line_color=TINTA_SECUNDARIA
        )

    fig.update_layout(
        height=360,
        margin={"l": 10, "r": 10, "t": 30, "b": 10},
        hovermode="closest",
        legend={"orientation": "h", "y": 1.08, "x": 0},
        yaxis={"title": "NDVI", "range": [0, 1], "gridcolor": GRILLA, "zeroline": False},
        xaxis={
            "showgrid": False,
            "tickformat": "%d/%m/%y",
            "hoverformat": "%d/%m/%Y",
            # línea guía vertical al pasar el mouse (crosshair)
            "showspikes": True,
            "spikemode": "across",
            "spikethickness": 1,
            "spikedash": "solid",
            "spikecolor": GRIS_MUDO,
        },
    )
    return fig
