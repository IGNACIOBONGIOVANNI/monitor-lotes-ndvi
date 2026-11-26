"""Uso desde la terminal.

python -m monitor_lotes.cli data/ejemplo/lote.geojson --desde 2025-09-01 --hasta 2026-03-31
"""

from __future__ import annotations

import argparse
import logging
import sys
import textwrap
from datetime import date

from .alertas import detectar_alertas
from .cache import Cache
from .config import Parametros
from .lotes import LoteInvalidoError, leer_lote
from .serie import calcular_serie, exportar


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Serie de NDVI de un lote con Sentinel-2.")
    p.add_argument("lote", help="archivo .kml o .geojson")
    p.add_argument("--desde", type=date.fromisoformat, required=True, help="AAAA-MM-DD")
    p.add_argument("--hasta", type=date.fromisoformat, required=True, help="AAAA-MM-DD")
    p.add_argument("--min-limpio", type=float, default=0.80, help="fracción mínima limpia (0-1)")
    p.add_argument("--caida", type=float, default=0.15, help="caída de NDVI para alertar")
    p.add_argument("--min-ha", type=float, default=1.0, help="hectáreas mínimas para alertar")
    p.add_argument("--fuente", choices=["earth-search", "planetary-computer"], default=None)
    p.add_argument("--cache", default="cache", help="carpeta de caché")
    p.add_argument("--salida", default=None, help="carpeta donde exportar CSV y GeoTIFF")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING, format="%(message)s")
    logging.getLogger("rasterio").setLevel(logging.ERROR)  # avisos internos de GDAL/boto3

    try:
        lote = leer_lote(a.lote)
    except (LoteInvalidoError, OSError) as e:
        print(f"Error con el lote: {e}", file=sys.stderr)
        return 2

    print(f"Lote '{lote.nombre}': {lote.hectareas:.1f} ha, UTM EPSG:{lote.epsg_utm}")

    def progreso(i, n, fecha):
        print(f"  [{i}/{n}] {fecha}", end="\r", flush=True)

    params = Parametros(
        min_fraccion_limpia=a.min_limpio, caida_ndvi=a.caida, min_ha_alerta=a.min_ha
    )
    serie = calcular_serie(
        lote,
        a.desde,
        a.hasta,
        params,
        Cache(a.cache),
        a.fuente,
        progreso,
    )
    t = serie.tabla
    print(
        f"\n{len(t)} pasadas, {int(t['usable'].sum())} limpias (>= {a.min_limpio:.0%} del lote)\n"
    )
    cols = ["fecha", "pct_limpio", "media", "mediana", "p10", "p90", "usable"]
    print(t[cols].to_string(index=False, float_format=lambda x: f"{x:.3f}"))

    alertas = detectar_alertas(serie.ndvi, params)
    print(f"\n{len(alertas)} alertas (caída > {a.caida:.2f} de NDVI en >= {a.min_ha:g} ha)")
    for al in alertas:
        print(f"\n  [{al.estado.upper()}] {al.titulo}")
        print(textwrap.fill(al.texto(a.caida), 92, initial_indent="    ", subsequent_indent="    "))

    if a.salida:
        exportar(serie, a.salida)
        print(f"\nResultados guardados en {a.salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
