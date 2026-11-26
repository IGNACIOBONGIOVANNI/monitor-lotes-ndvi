"""Interfaz web del monitor de lotes (Streamlit).

Acá solo se arma la pantalla: toda la lógica (lotes, imágenes, NDVI, caché) vive en
src/monitor_lotes, y esta app la llama.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

import folium
import streamlit as st
from folium.plugins import Draw
from shapely.geometry import shape
from streamlit_folium import st_folium

from monitor_lotes import visual
from monitor_lotes.cache import Cache
from monitor_lotes.config import Parametros
from monitor_lotes.lotes import Lote, LoteInvalidoError, crear_lote, lote_desde_texto
from monitor_lotes.serie import Serie, calcular_serie, importar

RAIZ = Path(__file__).parent
EJEMPLO = RAIZ / "data" / "ejemplo" / "resultados"
CENTRO_INICIAL = (-30.79, -60.59)  # San Justo, Santa Fe
SATELITE = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
ORIGENES = ("Lote de ejemplo (San Justo)", "Subir KML o GeoJSON", "Dibujar en el mapa")

st.set_page_config(page_title="Monitor de lotes · NDVI", page_icon="🌱", layout="wide")
logging.getLogger("rasterio").setLevel(logging.ERROR)


# ---------------------------------------------------------------- estado


@st.cache_resource
def serie_ejemplo() -> Serie:
    """Resultados ya calculados: la demo carga al instante, sin ir a internet."""
    return importar(EJEMPLO)


def fijar_lote(lote: Lote | None) -> None:
    """Cambiar de lote invalida la serie calculada para el anterior."""
    actual = st.session_state.get("lote")
    if lote is None or actual is None or actual.id != lote.id:
        st.session_state["serie"] = None
    st.session_state["lote"] = lote


def al_cambiar_origen() -> None:
    fijar_lote(None)


st.session_state.setdefault("lote", None)
st.session_state.setdefault("serie", None)


# ---------------------------------------------------------------- mapas


def mapa_base(lote: Lote | None) -> folium.Map:
    m = folium.Map(location=CENTRO_INICIAL, zoom_start=13, tiles=None, control_scale=True)
    folium.TileLayer(SATELITE, attr="Esri World Imagery", name="Satélite").add_to(m)
    folium.TileLayer("OpenStreetMap", name="Calles", show=False).add_to(m)
    if lote is not None:
        minx, miny, maxx, maxy = lote.geometria.bounds
        m.fit_bounds([[miny, minx], [maxy, maxx]], padding=(20, 20))
    return m


def contorno(lote: Lote) -> folium.GeoJson:
    return folium.GeoJson(
        lote.a_geojson(),
        name="Contorno del lote",
        style_function=lambda _: {"color": "#ffffff", "weight": 2, "fillOpacity": 0},
    )


def mapa_ndvi(serie: Serie, fecha: date) -> folium.Map:
    m = mapa_base(serie.lote)
    datos, limites = visual.a_mercator(serie.ndvi[fecha], serie.epsg, serie.transform)
    folium.raster_layers.ImageOverlay(
        visual.colorear(datos),
        bounds=limites,
        opacity=0.9,
        pixelated=True,
        name=f"NDVI {fecha:%d/%m/%Y}",
    ).add_to(m)
    contorno(serie.lote).add_to(m)
    folium.LayerControl(collapsed=True).add_to(m)
    return m


def leyenda_ndvi() -> str:
    """Barra de colores en HTML, debajo del mapa (dentro del mapa tapaba los controles)."""
    gradiente = ", ".join(visual.PALETA_NDVI)
    marcas = "".join(f"<span>{v:.1f}</span>" for v in (0, 0.2, 0.4, 0.6, 0.8, 1.0))
    return f"""
<div style="max-width:420px;font-size:0.8rem;color:#52514e">
  <div style="height:10px;border-radius:4px;background:linear-gradient(90deg,{gradiente});
              outline:1px solid rgba(11,11,11,0.10)"></div>
  <div style="display:flex;justify-content:space-between;margin-top:2px">{marcas}</div>
  <div>NDVI · suelo desnudo / rastrojo → vegetación densa</div>
</div>"""


# ---------------------------------------------------------------- barra lateral

with st.sidebar:
    st.header("1 · Lote")
    origen = st.radio(
        "¿De dónde sale el lote?", ORIGENES, key="origen", on_change=al_cambiar_origen
    )

    if origen == ORIGENES[0]:
        ej = serie_ejemplo()
        if st.session_state["lote"] is None:
            fijar_lote(ej.lote)
            st.session_state["serie"] = ej
        st.caption(
            "Lote **aproximado** al oeste de San Justo, con la campaña 2025/26 ya calculada."
        )

    elif origen == ORIGENES[1]:
        archivo = st.file_uploader(
            "Archivo del lote",
            type=["kml", "geojson", "json"],
            help="Desde Google Earth: clic derecho en el polígono → Guardar lugar como… → .kml",
        )
        if archivo is not None:
            try:
                fijar_lote(
                    lote_desde_texto(
                        archivo.getvalue().decode("utf-8"),
                        Path(archivo.name).suffix,
                        Path(archivo.name).stem,
                    )
                )
            except (LoteInvalidoError, UnicodeDecodeError, ValueError) as e:
                fijar_lote(None)
                st.error(f"No pude leer el lote: {e}")

    else:
        st.caption("Usá ⬟ (polígono) o ■ (rectángulo) arriba a la izquierda del mapa.")
        if st.session_state["lote"] is not None and st.button("Borrar y dibujar otro"):
            fijar_lote(None)
            st.rerun()

    lote: Lote | None = st.session_state["lote"]
    if lote is not None:
        st.success(f"**{lote.nombre}** · {lote.hectareas:,.1f} ha · UTM EPSG:{lote.epsg_utm}")

    st.header("2 · Campaña")
    hoy = date.today()
    rango = st.date_input(
        "Desde / hasta",
        value=(date(2025, 9, 1), date(2026, 4, 30)),
        min_value=date(2017, 1, 1),
        max_value=hoy,
        format="DD/MM/YYYY",
    )
    rango_ok = isinstance(rango, tuple) and len(rango) == 2 and rango[0] < rango[1]

    with st.expander("Opciones avanzadas"):
        min_limpio = st.slider(
            "Mínimo del lote sin nubes (%)",
            50,
            100,
            80,
            5,
            help="Si queda limpio menos que esto, la fecha no se usa.",
        )
        max_nubes = st.slider(
            "Nubosidad máxima de la escena (%)",
            10,
            100,
            80,
            10,
            help="Prefiltro grueso sobre la escena entera (110 x 110 km). "
            "La decisión final se toma mirando solo el lote.",
        )

    if rango_ok and (rango[1] - rango[0]) > timedelta(days=240):
        st.caption("Rango largo: la primera vez puede tardar 1–2 minutos.")
    calcular = st.button(
        "Calcular NDVI",
        type="primary",
        disabled=lote is None or not rango_ok,
        width="stretch",
    )


# ---------------------------------------------------------------- cálculo

st.title("Monitor de lotes con NDVI")
st.caption(
    "Imágenes Sentinel-2 gratuitas (10 m), filtradas por nubes dentro del lote. "
    "La app detecta cambios; no diagnostica sus causas."
)

if calcular and lote is not None:
    barra = st.progress(0.0, text="Buscando imágenes en el catálogo…")

    def progreso(i: int, n: int, _etapa: str) -> None:
        barra.progress(i / n if n else 1.0, text=f"Leyendo imágenes del lote: {i} de {n}")

    try:
        st.session_state["serie"] = calcular_serie(
            lote,
            rango[0],
            rango[1],
            Parametros(min_fraccion_limpia=min_limpio / 100, max_nubosidad_escena=max_nubes),
            Cache(),
            progreso=progreso,
        )
    except RuntimeError as e:
        st.error(f"No se pudo consultar el catálogo de imágenes. Probá de nuevo más tarde. ({e})")
    finally:
        barra.empty()

serie: Serie | None = st.session_state["serie"]


# ---------------------------------------------------------------- pantalla principal

if lote is None:
    if origen == ORIGENES[2]:
        m = mapa_base(None)
        Draw(
            draw_options={
                "polyline": False,
                "circle": False,
                "marker": False,
                "circlemarker": False,
                "polygon": {"allowIntersection": False},
                "rectangle": True,
            },
            edit_options={"edit": False, "remove": False},
        ).add_to(m)
        salida = st_folium(
            m, key="dibujo", height=560, use_container_width=True, returned_objects=["all_drawings"]
        )
        dibujos = (salida or {}).get("all_drawings") or []
        if dibujos:
            try:
                fijar_lote(crear_lote(shape(dibujos[-1]["geometry"]), "Lote dibujado"))
                st.rerun()
            except LoteInvalidoError as e:
                st.error(str(e))
    else:
        st.info("Cargá un lote en la barra de la izquierda para empezar.")
    st.stop()

if serie is None:
    st_folium(
        mapa_base(lote).add_child(contorno(lote)),
        key="vista_lote",
        height=480,
        use_container_width=True,
        returned_objects=[],
    )
    st.info("Elegí el rango de fechas y tocá **Calcular NDVI**.")
    st.stop()

tabla = serie.tabla
if not serie.ndvi:
    st.warning(
        f"Se encontraron {len(tabla)} pasadas, pero ninguna con al menos "
        f"{min_limpio}% del lote sin nubes. Probá con un rango más largo o bajá el umbral."
    )
    st.stop()

fechas = sorted(serie.ndvi)
fecha = st.select_slider(
    "Fecha de la imagen (solo fechas limpias)",
    options=fechas,
    value=fechas[-1],
    format_func=lambda d: d.strftime("%d/%m/%Y"),
)
fila = tabla[tabla["fecha"] == fecha].iloc[0]
idx = fechas.index(fecha)
anterior = tabla[tabla["fecha"] == fechas[idx - 1]].iloc[0] if idx > 0 else None

c1, c2, c3, c4 = st.columns(4)
c1.metric("Superficie", f"{serie.lote.hectareas:,.1f} ha")
c2.metric("Fechas limpias", f"{len(fechas)} de {len(tabla)}")
c3.metric(
    "NDVI mediano",
    f"{fila['mediana']:.2f}",
    delta=None if anterior is None else f"{fila['mediana'] - anterior['mediana']:+.2f}",
    delta_description=None if anterior is None else f"vs. {anterior['fecha']:%d/%m}",
    delta_color="off",  # una baja no es "mala" en sí: puede ser cosecha. Detecta, no juzga.
)
c4.metric("Lote sin nubes", f"{fila['pct_limpio']:.0f} %")

col_mapa, col_graf = st.columns([1, 1.15], gap="medium")
with col_mapa:
    st.subheader(f"Mapa de NDVI · {fecha:%d/%m/%Y}")
    st_folium(
        mapa_ndvi(serie, fecha),
        key="mapa_ndvi",
        height=470,
        use_container_width=True,
        returned_objects=[],
    )
    st.html(leyenda_ndvi())
    st.caption("Píxeles de 10 × 10 m. Solo se pintan los píxeles sin nubes dentro del lote.")
with col_graf:
    st.subheader("NDVI durante la campaña")
    st.plotly_chart(visual.figura_serie(tabla, fecha), width="stretch")
    st.caption(
        "Línea: mediana del lote. Banda: 80 % central de los píxeles (p10–p90); "
        "una banda ancha indica un lote desparejo."
    )

with st.expander("Ver tabla de datos"):
    vista = tabla.drop(columns=["imagenes"]).rename(
        columns={
            "pct_limpio": "% limpio",
            "n_pixeles_lote": "píxeles",
            "usable": "usada",
        }
    )
    st.dataframe(vista, hide_index=True, width="stretch")
    st.download_button(
        "Descargar CSV",
        tabla.to_csv(index=False).encode("utf-8"),
        file_name=f"ndvi_{serie.lote.nombre}.csv",
        mime="text/csv",
    )
