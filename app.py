"""Interfaz web del monitor de lotes (Streamlit).

Acá solo se arma la pantalla: toda la lógica (lotes, imágenes, NDVI, caché) vive en
src/monitor_lotes, y esta app la llama.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path

import folium
import streamlit as st
from folium.plugins import Draw
from shapely.geometry import mapping, shape
from shapely.ops import unary_union
from streamlit_folium import st_folium

from monitor_lotes import visual
from monitor_lotes.alertas import (
    CONFIRMADA,
    PENDIENTE,
    SIN_CONFIRMAR,
    detectar_alertas,
    zona_afectada,
)
from monitor_lotes.cache import Cache
from monitor_lotes.config import Parametros
from monitor_lotes.formato import num
from monitor_lotes.lotes import (
    Lote,
    LoteInvalidoError,
    a_kml,
    hectareas_wgs84,
    lote_desde_dibujo,
    lote_desde_texto,
)
from monitor_lotes.manijas import ManijasLote
from monitor_lotes.serie import Serie, calcular_serie, importar

RAIZ = Path(__file__).parent
EJEMPLO = RAIZ / "data" / "ejemplo" / "resultados"
CENTRO_INICIAL = (-30.79, -60.59)  # San Justo, Santa Fe
SATELITE = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
ORIGENES = ("Lote de ejemplo (San Justo)", "Subir KML o GeoJSON", "Dibujar en el mapa")

st.set_page_config(
    page_title="Monitor de lotes · NDVI",
    page_icon=str(RAIZ / "assets" / "favicon.png"),  # generado con scripts/generar_logo.py
    layout="wide",
)
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
    restaurar_dibujo()


def editar_dibujo() -> None:
    st.session_state["serie"] = None
    restaurar_dibujo()


def restaurar_dibujo() -> None:
    """El mapa de dibujo vuelve con los dibujos que había (ya girados o movidos), editables."""
    st.session_state["dibujos_iniciales"] = st.session_state.get("dibujos", [])


st.session_state.setdefault("lote", None)
st.session_state.setdefault("serie", None)
st.session_state.setdefault("dibujos", [])  # geometrías GeoJSON dibujadas (WGS84)
st.session_state.setdefault("dibujos_iniciales", [])  # con las que arranca el mapa de dibujo


# ---------------------------------------------------------------- mapas


def mapa_base(lote: Lote | None) -> folium.Map:
    m = folium.Map(location=CENTRO_INICIAL, zoom_start=13, tiles=None, control_scale=True)
    folium.TileLayer(SATELITE, attr="Esri World Imagery", name="Satélite").add_to(m)
    folium.TileLayer("OpenStreetMap", name="Calles", show=False).add_to(m)
    visual.CreditoLeaflet().add_to(m)
    if lote is not None:
        minx, miny, maxx, maxy = lote.geometria.bounds
        m.fit_bounds([[miny, minx], [maxy, maxx]], padding=(20, 20))
    return m


ESTILO_DIBUJO = {"color": "#ffd60a", "weight": 3, "fillOpacity": 0.12}  # amarillo: resalta


def mapa_dibujo(iniciales: list[dict]) -> folium.Map:
    """Mapa para dibujar el lote: polígono, rectángulo, editar, borrar, y manijas ↻ y ✥.

    Arranca con `iniciales` ya editables. Se arma siempre igual mientras se dibuja (no
    depende de lo último dibujado): si el HTML del mapa cambiara, el componente lo
    recargaría y se perderían los trazos y el zoom.
    """
    m = mapa_base(None)
    grupo = folium.FeatureGroup(name="Dibujos")
    for g in iniciales:
        geom = shape(g)
        for p in getattr(geom, "geoms", [geom]):
            folium.Polygon(
                [(y, x) for x, y in p.exterior.coords],
                color=ESTILO_DIBUJO["color"],
                weight=ESTILO_DIBUJO["weight"],
                fill=True,
                fill_opacity=ESTILO_DIBUJO["fillOpacity"],
            ).add_to(grupo)
    grupo.add_to(m)
    Draw(
        feature_group=grupo,
        show_geometry_on_click=False,
        draw_options={
            "polyline": False,
            "circle": False,
            "marker": False,
            "circlemarker": False,
            "polygon": {
                "allowIntersection": False,
                "showArea": True,
                "shapeOptions": ESTILO_DIBUJO,
            },
            "rectangle": {"showArea": True, "shapeOptions": ESTILO_DIBUJO},
        },
        # Ojo: pasar "edit": True rompe la edición de vértices de Leaflet.draw (pisa sus
        # opciones de estilo y falla en silencio). Con un diccionario funciona.
        edit_options={
            "edit": {
                "selectedPathOptions": {
                    "dashArray": "6, 6",
                    "fill": True,
                    "fillColor": ESTILO_DIBUJO["color"],
                    "fillOpacity": 0.2,
                    "maintainColor": True,
                }
            },
            "remove": True,
        },
    ).add_to(m)
    if iniciales:
        minx, miny, maxx, maxy = unary_union([shape(g) for g in iniciales]).bounds
        # margen amplio: la manija ↻ va 34 px por arriba del lote y no tiene que quedar cortada
        m.fit_bounds([[miny, minx], [maxy, maxx]], padding=(80, 80))
    ManijasLote(grupo).add_to(m)
    return m


def contorno(lote: Lote) -> folium.GeoJson:
    return folium.GeoJson(
        lote.a_geojson(),
        name="Contorno del lote",
        style_function=lambda _: {"color": "#ffffff", "weight": 2, "fillOpacity": 0},
    )


def mapa_capa(
    serie: Serie,
    valores,
    nombre: str,
    paleta: list[str] = visual.PALETA_NDVI,
    vmin: float = visual.NDVI_MIN,
    vmax: float = visual.NDVI_MAX,
    zona=None,
) -> folium.Map:
    """Mapa satelital con una capa raster (NDVI o cambio) y, opcional, la zona de alerta."""
    m = mapa_base(serie.lote)
    datos, limites = visual.a_mercator(valores, serie.epsg, serie.transform)
    folium.raster_layers.ImageOverlay(
        visual.colorear(datos, paleta, vmin, vmax),
        bounds=limites,
        opacity=0.9,
        pixelated=True,
        name=nombre,
    ).add_to(m)
    contorno(serie.lote).add_to(m)
    if zona is not None:
        folium.GeoJson(
            mapping(zona),
            name="Zona afectada",
            style_function=lambda _: {"color": "#0b0b0b", "weight": 2.5, "fillOpacity": 0},
            tooltip="Zona afectada",
        ).add_to(m)
    folium.LayerControl(collapsed=True).add_to(m)
    return m


def leyenda(paleta: list[str], marcas: list[str], texto: str) -> str:
    """Barra de colores en HTML, debajo del mapa (dentro del mapa tapaba los controles)."""
    gradiente = ", ".join(paleta)
    etiquetas = "".join(f"<span>{m}</span>" for m in marcas)
    return f"""
<div style="max-width:420px;font-size:0.8rem;color:#52514e">
  <div style="height:10px;border-radius:4px;background:linear-gradient(90deg,{gradiente});
              outline:1px solid rgba(11,11,11,0.10)"></div>
  <div style="display:flex;justify-content:space-between;margin-top:2px">{etiquetas}</div>
  <div>{texto}</div>
</div>"""


LEYENDA_NDVI = leyenda(
    visual.PALETA_NDVI,
    ["0", "0,2", "0,4", "0,6", "0,8", "1"],
    "NDVI · suelo desnudo / rastrojo → vegetación densa",
)
LEYENDA_CAMBIO = leyenda(
    visual.PALETA_CAMBIO,
    ["−0,4", "−0,2", "0", "+0,2", "+0,4"],
    "Cambio de NDVI · bajó (rojo) ← sin cambio (gris) → subió (azul)",
)
ICONO_ESTADO = {CONFIRMADA: "🔶", SIN_CONFIRMAR: "⚪", PENDIENTE: "⏳"}


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
        st.caption("Dibujá el lote con las herramientas de la izquierda del mapa.")
        if st.session_state["serie"] is not None:
            st.button("Editar el dibujo", on_click=editar_dibujo)


# ---------------------------------------------------------------- dibujo del lote

st.title("Monitor de lotes con NDVI")
st.caption(
    "Imágenes Sentinel-2 gratuitas (10 m), filtradas por nubes dentro del lote. "
    "La app detecta cambios; no diagnostica sus causas."
)

if origen == ORIGENES[2] and st.session_state["serie"] is None:
    st.subheader("Dibujá el lote")
    st.markdown(
        "- **⬟ Polígono:** clic en cada esquina del lote y clic en el primer punto para cerrar.\n"
        "- **■ Rectángulo:** hacé clic y arrastrá.\n"
        "- **↻ Girar** y **✥ Mover:** mantené apretada la manija (↻ arriba del lote, ✥ en su "
        "centro) y arrastrá.\n"
        "- **✎ Editar** vértices o **🗑 Borrar** el dibujo que elijas; después tocá *Save*."
    )
    dibujos: list[dict] = st.session_state["dibujos"]
    i_dibujo = len(dibujos) - 1
    if len(dibujos) > 1:
        i_dibujo = st.selectbox(
            "¿Cuál dibujo es el lote?",
            range(len(dibujos)),
            index=len(dibujos) - 1,
            format_func=lambda i: f"Dibujo {i + 1} · {num(hectareas_wgs84(shape(dibujos[i])))} ha",
        )

    lote_dibujado, error_dibujo = None, None
    if dibujos:
        try:
            lote_dibujado = lote_desde_dibujo(dibujos[i_dibujo])
        except LoteInvalidoError as e:
            error_dibujo = str(e)

    # Con varios dibujos se marca cuál es el lote elegido. Va como capa aparte para que el
    # mapa no se recargue (se perderían los trazos y el zoom).
    resaltado = folium.FeatureGroup(name="Lote elegido")
    if lote_dibujado is not None and len(dibujos) > 1:
        folium.GeoJson(
            lote_dibujado.a_geojson(),
            style_function=lambda _: {
                "color": "#ffffff",
                "weight": 3,
                "dashArray": "6 5",
                "fillOpacity": 0,
            },
        ).add_to(resaltado)

    salida = st_folium(
        mapa_dibujo(st.session_state["dibujos_iniciales"]),
        key="dibujo",
        height=560,
        use_container_width=True,
        returned_objects=["all_drawings"],
        feature_group_to_add=resaltado,
    )
    nuevos = (salida or {}).get("all_drawings")
    if nuevos is not None:
        nuevos = [
            d["geometry"]
            for d in nuevos
            if (d.get("geometry") or {}).get("type") in ("Polygon", "MultiPolygon")
        ]
        if nuevos != dibujos:
            st.session_state["dibujos"] = nuevos
            st.rerun()

    if error_dibujo:
        st.error(error_dibujo)
    elif len(dibujos) > 1:
        st.caption("Contorno **blanco punteado**: el dibujo elegido como lote.")
    fijar_lote(lote_dibujado)


# ---------------------------------------------------------------- barra lateral (cont.)

with st.sidebar:
    lote: Lote | None = st.session_state["lote"]
    if lote is not None:
        st.success(f"**{lote.nombre}** · {num(lote.hectareas)} ha · UTM EPSG:{lote.epsg_utm}")

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
        st.markdown("**Alertas**")
        caida = st.slider(
            "Caída de NDVI que dispara una alerta",
            0.05,
            0.50,
            0.15,
            0.05,
            help="Se compara cada píxel con la fecha limpia anterior.",
        )
        min_ha = st.number_input(
            "Superficie mínima afectada (ha)", 0.1, 100.0, 1.0, 0.5, format="%.1f"
        )
    params = Parametros(
        min_fraccion_limpia=min_limpio / 100,
        max_nubosidad_escena=max_nubes,
        caida_ndvi=caida,
        min_ha_alerta=min_ha,
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

if calcular and lote is not None:
    barra = st.progress(0.0, text="Buscando imágenes en el catálogo…")

    def progreso(i: int, n: int, _etapa: str) -> None:
        barra.progress(i / n if n else 1.0, text=f"Leyendo imágenes del lote: {i} de {n}")

    try:
        st.session_state["serie"] = calcular_serie(
            lote,
            rango[0],
            rango[1],
            params,
            Cache(),
            progreso=progreso,
        )
    except RuntimeError as e:
        barra.empty()
        st.error(f"No se pudo consultar el catálogo de imágenes. Probá de nuevo más tarde. ({e})")
    else:
        st.rerun()  # vuelve a dibujar la pantalla ya con resultados (y sin el mapa de dibujo)

serie: Serie | None = st.session_state["serie"]


# ---------------------------------------------------------------- pantalla principal

if lote is None:
    if origen != ORIGENES[2]:  # en modo dibujo el mapa ya está arriba
        st.info("Cargá un lote en la barra de la izquierda para empezar.")
    st.stop()

if serie is None:
    if origen != ORIGENES[2]:
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
# Las alertas se recalculan al instante con los umbrales de la barra lateral
# (no hace falta volver a descargar nada: se usan los NDVI ya calculados).
alertas = detectar_alertas(serie.ndvi, params)

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Superficie", f"{num(serie.lote.hectareas)} ha")
c2.metric("Fechas limpias", f"{len(fechas)} de {len(tabla)}")
c3.metric(
    "NDVI mediano",
    num(fila["mediana"], 2),
    delta=None if anterior is None else f"{fila['mediana'] - anterior['mediana']:+.2f}",
    delta_description=None if anterior is None else f"vs. {anterior['fecha']:%d/%m}",
    delta_color="off",  # una baja no es "mala" en sí: puede ser cosecha. Detecta, no juzga.
)
c4.metric("Lote sin nubes", f"{num(fila['pct_limpio'], 0)} %")
c5.metric("Alertas", len(alertas))

col_mapa, col_graf = st.columns([1, 1.15], gap="medium")
with col_mapa:
    st.subheader(f"Mapa de NDVI · {fecha:%d/%m/%Y}")
    st_folium(
        mapa_capa(serie, serie.ndvi[fecha], f"NDVI {fecha:%d/%m/%Y}"),
        key="mapa_ndvi",
        height=470,
        use_container_width=True,
        returned_objects=[],
    )
    st.html(LEYENDA_NDVI)
    st.caption("Píxeles de 10 × 10 m. Solo se pintan los píxeles sin nubes dentro del lote.")
with col_graf:
    st.subheader("NDVI durante la campaña")
    st.plotly_chart(visual.figura_serie(tabla, fecha, alertas), width="stretch")
    st.caption(
        "Línea: mediana del lote. Banda: 80 % central de los píxeles (p10–p90); "
        "una banda ancha indica un lote desparejo. ▼ alerta confirmada · ▽ sin confirmar."
    )

# ---------------------------------------------------------------- alertas

st.divider()
st.subheader(f"Alertas por caída brusca de NDVI · {len(alertas)}")
st.caption(
    f"Se avisa cuando píxeles que bajan más de {num(caida, 2)} de NDVI respecto de la fecha "
    f"limpia anterior suman al menos {num(min_ha)} ha. Una alerta queda **confirmada** si la "
    "zona sigue baja en la imagen limpia siguiente."
)

if not alertas:
    st.success("Sin caídas bruscas en el período con los umbrales elegidos.", icon="✅")
else:
    ocultar = st.toggle("Ocultar las alertas sin confirmar", value=False)
    visibles = [a for a in alertas if not (ocultar and a.estado == SIN_CONFIRMAR)]
    if not visibles:
        st.info("Todas las alertas del período quedaron sin confirmar.")
    else:
        visibles = sorted(visibles, key=lambda a: a.fecha, reverse=True)  # la más reciente arriba
        i_alerta = st.selectbox(
            "Alerta",
            range(len(visibles)),
            format_func=lambda i: (
                f"{ICONO_ESTADO[visibles[i].estado]} {visibles[i].titulo} · {visibles[i].estado}"
            ),
        )
        alerta = visibles[i_alerta]
        zona = zona_afectada(alerta, serie.epsg, serie.transform)

        col_am, col_ad = st.columns([1, 1.15], gap="medium")
        with col_am:
            vista_alerta = st.radio(
                "Ver en el mapa",
                [
                    "Cambio",
                    f"Antes ({alerta.fecha_anterior:%d/%m})",
                    f"Después ({alerta.fecha:%d/%m})",
                ],
                horizontal=True,
                label_visibility="collapsed",
            )
            if vista_alerta == "Cambio":
                m = mapa_capa(
                    serie,
                    alerta.diferencia,
                    "Cambio de NDVI",
                    visual.PALETA_CAMBIO,
                    -visual.CAMBIO_MAX,
                    visual.CAMBIO_MAX,
                    zona,
                )
                ley = LEYENDA_CAMBIO
            else:
                f = alerta.fecha_anterior if vista_alerta.startswith("Antes") else alerta.fecha
                m = mapa_capa(serie, serie.ndvi[f], f"NDVI {f:%d/%m/%Y}", zona=zona)
                ley = LEYENDA_NDVI
            st_folium(
                m, key="mapa_alerta", height=430, use_container_width=True, returned_objects=[]
            )
            st.html(ley)
            st.caption("Contorno negro: zona afectada. Contorno blanco: lote.")
        with col_ad:
            a1, a2, a3 = st.columns(3)
            a1.metric("Superficie afectada", f"{num(alerta.hectareas)} ha")
            a2.metric("Del lote", f"{num(alerta.pct_lote, 0)} %")
            a3.metric("Caída media", num(alerta.caida_media, 2))
            st.warning(alerta.texto(caida), icon=ICONO_ESTADO[alerta.estado])
            descripcion = alerta.texto(caida)
            nombre_archivo = f"zona_{serie.lote.nombre}_{alerta.fecha:%Y%m%d}".replace(" ", "_")
            d1, d2 = st.columns(2)
            d1.download_button(
                "Descargar zona (KML)",
                a_kml(zona, f"Zona afectada {alerta.fecha:%d/%m/%Y}", descripcion),
                file_name=f"{nombre_archivo}.kml",
                mime="application/vnd.google-earth.kml+xml",
                help="Para abrir en Google Earth o cargar en el planificador de vuelo del dron.",
            )
            d2.download_button(
                "Descargar zona (GeoJSON)",
                json.dumps(
                    {
                        "type": "Feature",
                        "properties": {
                            "fecha": alerta.fecha.isoformat(),
                            "hectareas": round(alerta.hectareas, 2),
                            "estado": alerta.estado,
                        },
                        "geometry": mapping(zona),
                    }
                ),
                file_name=f"{nombre_archivo}.geojson",
                mime="application/geo+json",
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
