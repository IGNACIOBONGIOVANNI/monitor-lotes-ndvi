# Decisiones técnicas

Cada decisión importante del proyecto, con las alternativas que descarté y por qué.
Formato: **qué elegí → qué descarté → por qué**. Al final hay preguntas probables de
entrevista con la respuesta corta.

---

## 1. Datos satelitales

### 1.1 Sentinel-2 L2A desde un catálogo STAC
- **Elegí:** Sentinel-2 nivel 2A (reflectancia de superficie, ya corregida por atmósfera),
  buscado en un catálogo **STAC**.
- **Descarté:** Landsat 8/9 (30 m y cada 8–16 días: poco para un lote), imágenes comerciales
  (Planet: 3 m pero paga), Google Earth Engine (muy potente, pero requiere cuenta, el código
  queda atado a su plataforma y no se puede explicar "qué pasa por debajo"), Sentinel Hub
  (cuota gratuita limitada y con registro).
- **Por qué:** Sentinel-2 es gratis, tiene 10 m en rojo e infrarrojo y pasa cada pocos días.
  STAC es un estándar abierto: el mismo código sirve para otros catálogos.

### 1.2 Earth Search como fuente principal, Planetary Computer de respaldo
- **Elegí:** Earth Search (Element 84), colección `sentinel-2-c1-l2a`; si falla, Microsoft
  Planetary Computer (`sentinel-2-l2a`).
- **Descarté:** la colección vieja `sentinel-2-l2a` de Earth Search.
- **Por qué:** `c1` es la **Collection 1** de ESA: todo el archivo reprocesado con la misma
  versión del algoritmo, así una fecha de 2019 y una de 2026 son comparables. No necesita
  registro ni firma de URLs. Planetary Computer sí firma las URLs (`planetary_computer.sign`),
  por eso esa lógica vive solo en la fuente de respaldo.
- **Verificado:** antes de escribir código revisé la metadata real de la colección (asset keys
  `red`, `nir`, `scl`; `raster:bands` con `scale` 0.0001 y `offset` −0.1).

### 1.3 Prefiltro por nubosidad de la escena (80 %) y filtro real por lote
- **Elegí:** pedirle al catálogo escenas con `eo:cloud_cover < 80` y después decidir con la SCL
  **mirando solo el lote**.
- **Descarté:** filtrar por nubosidad de escena baja (por ejemplo < 20 %).
- **Por qué:** la escena mide 110 × 110 km. Una escena 50 % nublada puede tener el lote
  totalmente despejado; con un filtro de escena estricto se pierden fechas útiles.

---

## 2. Lectura liviana

### 2.1 Leer solo la ventana del lote (COG + lecturas parciales)
- **Elegí:** `odc-stac` con `geopolygon=lote`: arma una grilla del tamaño del lote y GDAL pide
  por HTTP solo los bloques del COG que caen ahí.
- **Descarté:** bajar la imagen entera (cientos de MB por banda) o usar los `.SAFE` originales.
- **Por qué:** un lote de 70 ha son ~7.000 píxeles. Se baja del orden de KB por fecha. Así anda
  en una PC común y en Streamlit Cloud (poca memoria).

### 2.2 odc-stac en vez de stackstac
- **Elegí:** `odc-stac`.
- **Descarté:** `stackstac` (sin versión nueva desde agosto de 2024 y su README dice que no se
  use en producción), `rioxarray` a mano (habría que reimplementar la unión de tiles y la
  reproyección).
- **Por qué:** mantenido, soporta `groupby="solar_day"`, `crs`, `resolution` y `patch_url`
  para firmar URLs.

### 2.3 Escala y offset aplicados por mí, leídos de la metadata
- **Elegí:** leer `scale`/`offset` de `raster:bands` de **cada imagen** y aplicarlos en
  `ndvi.py`: `reflectancia = DN × 0.0001 − 0.1`.
- **Descarté:** confiar en que la librería lo haga, o dejar los valores fijos.
- **Por qué:** revisé el código fuente de odc-stac: **no** los aplica. Desde 2022 ESA suma
  1000 a los números digitales; si no se resta, el NDVI sale sesgado (un cultivo con 0,84
  aparece como 0,59). Hay un test que lo muestra. Planetary Computer no publica el offset,
  así que ahí se deduce de la versión de procesamiento (`s2:processing_baseline >= 04.00`).

### 2.4 Reflectancias negativas recortadas a 0
- **Por qué:** por el offset, un píxel muy oscuro puede dar −0,01. Físicamente no existe y
  además podría sacar el NDVI fuera de [−1, 1]. Con reflectancias ≥ 0 el NDVI queda siempre
  en rango (hay un test con 10.000 valores al azar).

### 2.5 Vecino más cercano al remuestrear
- **Elegí:** `resampling="nearest"` para todo.
- **Descarté:** bilineal.
- **Por qué:** la SCL son **clases** (4 = vegetación, 9 = nube): promediarlas no tiene sentido.
  Para rojo y NIR la grilla nativa ya es de 10 m.

### 2.6 Unir tiles del mismo día
- **Por qué:** el lote de ejemplo cae justo en el borde entre dos tiles (20JQL y 20JQM). Con
  `groupby="solar_day"` las dos imágenes del mismo paso se combinan en una sola fecha.

### 2.7 Descargas en paralelo con hilos
- **Elegí:** `ThreadPoolExecutor` con 6 hilos.
- **Descarté:** secuencial (unos 6 minutos por campaña), `dask` (otra dependencia y más difícil
  de explicar), `asyncio` (GDAL no es asíncrono).
- **Por qué:** leer un recorte es casi todo espera de red, no CPU. Con 6 hilos la campaña bajó
  de unos 6 min a **98 s** con el mismo resultado. La caché se escribe desde un solo hilo.
- **Dato de la demo online:** en Streamlit Community Cloud la misma campaña sin caché tarda
  ~25 s. Los servidores están en EE. UU., cerca del bucket de AWS donde viven las imágenes
  (us-west-2): la latencia de red pesa más que la CPU.

---

## 3. Nubes y NDVI

### 3.1 Clases SCL que cuentan como "limpias"
- **Elegí:** conservar 4 (vegetación), 5 (suelo desnudo), 6 (agua) y 7 (sin clasificar);
  descartar 0 (sin datos), 1 (saturado), 2 (sombras / áreas oscuras), 3 (sombra de nube),
  8 y 9 (nubes), 10 (cirros) y 11 (nieve).
- **Discutible:** la clase 7. La dejé porque en lotes con rastrojo aparece seguido y
  descartarla hace perder fechas buenas. Está en `config.py` para cambiarla.

### 3.2 Umbral de 80 % calculado **solo con los píxeles del lote**
- **Por qué:** se cargan los datos del rectángulo que contiene al lote, pero el % limpio se
  mide sobre los píxeles cuyo centro cae dentro del polígono. Si no, nubes fuera del lote
  descartarían fechas buenas (hay un test para esto). El umbral es configurable.

### 3.3 Estadísticas por fecha: media, mediana, p10, p90 y % limpio
- **Por qué:** la **mediana** es robusta a píxeles raros (un árbol, un charco). La banda
  **p10–p90** muestra qué tan parejo está el lote: si es ancha, hay zonas muy distintas.

---

## 4. Geometría

### 4.1 Zona UTM calculada desde el centroide del lote
- **Elegí:** `zona = floor((lon + 180) / 6) + 1`; hemisferio sur → EPSG 327xx. Para San Justo
  da EPSG:32720.
- **Descarté:** dejarla fija en 32720 (fallaría para un lote en Buenos Aires, que es 32721),
  calcular hectáreas en grados (no son metros) o en Web Mercator (exagera las áreas: a 31° de
  latitud el factor es 1/cos²(31°) ≈ 1,36, o sea un 36 % de más).
- **Por qué:** UTM es conforme y a escala de un lote la distorsión de área es mínima. Hay tests
  con San Justo, Córdoba, Buenos Aires y París.

### 4.2 Parser de KML propio en vez de GDAL o fastkml
- **Elegí:** ~40 líneas con `xml.etree` que leen los `<Polygon>` (con huecos) y el `<name>`.
- **Descarté:** el driver KML de GDAL vía geopandas (cambia según cómo se compiló GDAL en cada
  sistema), `fastkml` (otra dependencia).
- **Por qué:** los KML de Google Earth son simples; un parser chico se entiende, se testea y
  no depende del entorno. Por la misma razón **saqué geopandas** de las dependencias: no hacía
  falta.

### 4.3 Validaciones del lote
- `make_valid` repara polígonos que se cruzan; se descartan líneas y puntos sueltos; se exige
  entre 0,5 y 5.000 ha (para que la app siga liviana).

---

## 5. Caché

### 5.1 Guardar las bandas **crudas** en GeoTIFF
- **Elegí:** un GeoTIFF por lote y fecha con rojo, NIR y SCL sin procesar, más la escala en los
  metadatos. Nombre: `<id del lote>/<fecha>_<hash de las imágenes>.tif`.
- **Descarté:** guardar el NDVI ya calculado (si cambiás el umbral o las clases SCL habría que
  volver a bajar todo), SQLite o Zarr (más complejo, no se abre en QGIS).
- **Por qué:** se recalcula todo sin red; se abre en QGIS; la escritura es atómica (se escribe
  `.tmp` y se renombra) para no dejar archivos a medias. El id del lote es un hash de su
  geometría redondeada a ~1 cm.

---

## 6. Interfaz

### 6.1 Streamlit + streamlit-folium + Plotly
- **Descarté:** Dash (más código para lo mismo), React + API (mucho más trabajo para una sola
  persona), pydeck (no trae herramienta de dibujo).
- **Por qué:** todo en Python, deploy gratis en Streamlit Community Cloud, y `folium.plugins.Draw`
  para dibujar el lote. Uso `returned_objects` en `st_folium` para que el mapa no recargue la
  app a cada clic.

### 6.2 `app.py` sin lógica
- La interfaz solo llama a funciones de `src/monitor_lotes`. Por eso toda la lógica se testea
  sin abrir un navegador, y la CLI reutiliza lo mismo.

### 6.3 Colores del NDVI: amarillo → verde (ColorBrewer YlGn)
- **Descarté:** el clásico rojo–amarillo–verde.
- **Por qué:** ~8 % de los hombres tiene daltonismo rojo-verde y no distingue el rojo del
  verde. En YlGn la luminosidad baja siempre: "más oscuro = más vegetación" se lee igual con
  daltonismo y en blanco y negro.

### 6.4 Escala fija de 0 a 1 para todas las fechas
- **Por qué:** si cada fecha estirara sus colores, todos los mapas "se verían verdes" y no se
  podría comparar septiembre con enero.

### 6.5 Mapa de cambio divergente (rojo bajó, azul subió, gris sin cambio)
- **Por qué:** dos tonos opuestos y un centro **neutro** (gris, no amarillo), para que "sin
  cambio" se lea como nada.

### 6.6 Reproyección a Web Mercator con vecino más cercano
- **Por qué:** el mapa web dibuja en EPSG:3857; si se superpone una imagen UTM sin reproyectar
  queda corrida. Verifiqué a ojo que los bordes de los potreros coinciden dentro y fuera de la
  capa.

### 6.7 La variación de NDVI se muestra en gris, no en rojo/verde
- **Por qué:** una baja no es "mala" en sí misma (puede ser cosecha). Coherente con "detecta, no
  diagnostica".

### 6.8 Dibujo del lote: manijas para girar y mover, edición y borrado
- **Elegí:** las herramientas de Leaflet.draw que ya trae folium (polígono punto por punto,
  rectángulo, editar vértices, borrar el dibujo que elijas) más **manijas propias** sobre cada
  lote: **↻** arriba para girar y **✥** en el centro para mover, manteniendo apretado y
  arrastrando, como en un programa de diseño (`src/monitor_lotes/manijas.js`, ~300 líneas).
- **Primera versión descartada (por feedback de uso):** un control deslizante "Girar el lote"
  con una vista previa aparte. Era indirecto y confundía: se veían dos figuras, la dibujada
  quieta y la girada encima.
- **Descarté también:** Leaflet-Geoman y Leaflet.Path.Transform (traen giro con el mouse, pero
  streamlit-folium solo escucha los eventos de Leaflet.draw: los cambios no volverían a Python).
- **Cómo vuelven los cambios a Python:** al soltar una manija se dispara `draw:edited`, el mismo
  evento que usa Leaflet.draw. streamlit-folium lo escucha y relee todo el grupo de dibujos
  (`drawnItems.toGeoJSON()`). No hizo falta ningún canal nuevo; hay un test que verifica que,
  después del renombrado de variables que hace streamlit-folium, las manijas apuntan a ese grupo.
- **Geometría en el navegador:** giro y desplazamiento en píxeles de Web Mercator. Mercator es
  conforme (conserva ángulos), así que un rectángulo girado sigue siendo un rectángulo. La
  superficie cambia ~0,04 % en un lote de 1,5 km por la variación de escala con la latitud
  (235,2 → 235,3 ha): despreciable frente a píxeles de 10 m. Las funciones de geometría
  (centroide, giro, ángulo) están testeadas con Node.
- **Dos trampas de Leaflet.draw que aparecieron probando:**
  1. Pasarle `edit: true` (lo que hace folium con `edit_options={"edit": True}`) pisa sus
     opciones de estilo y el modo ✎ Editar falla en silencio: no aparecen los vértices. Se
     arregla pasando un diccionario con `selectedPathOptions`.
  2. Leaflet.draw guarda una referencia a los vértices de cuando se creó la figura; después de
     girarla hay que avisarle con el evento `revert-edited`, o el modo ✎ Editar muestra los
     vértices viejos. Además, los rectángulos se convierten en polígonos al crearlos: un
     rectángulo de Leaflet.draw vuelve a quedar alineado norte-sur al editarlo.
- **Detalle de Streamlit:** el mapa de dibujo se arma siempre igual mientras se dibuja (si su
  HTML cambiara, se recargaría y se perderían los trazos y el zoom); el resaltado del lote
  elegido, cuando hay varios dibujos, va como capa aparte (`feature_group_to_add`).

### 6.9 Lote de ejemplo precalculado
- **Por qué:** la demo online carga al instante y sin internet: lee `data/ejemplo/resultados`
  (CSV + GeoTIFF multibanda). El lote es **aproximado**.

---

## 7. Alertas

### 7.1 Comparación píxel a píxel con la fecha limpia anterior
- **Descarté:** comparar la media del lote (una caída fuerte en 5 ha de un lote de 100 ha casi
  no mueve la media).
- **Por qué:** permite decir **dónde** y **cuántas hectáreas**. Solo se comparan píxeles limpios
  en las dos fechas.

### 7.2 Filtro de píxeles sueltos (≥ 2 vecinos)
- **Por qué:** píxeles aislados suelen ser bordes de nubes que la SCL no marcó o pequeños
  desfasajes entre imágenes. Implementado con numpy (sin scipy). Configurable.

### 7.3 Confirmación con la fecha limpia siguiente (agregado propio)
- **Elegí:** cada alerta queda **confirmada** (la zona sigue baja en la imagen siguiente),
  **sin confirmar** (volvió a la normalidad) o **pendiente** (todavía no hay imagen posterior).
- **Por qué:** un cosechado, un granizo o una helada persisten; un efecto atmosférico no. Es la
  idea de "confirmación con observaciones sucesivas" de los sistemas de alertas de
  deforestación. No oculta nada por defecto: hay un interruptor.

### 7.4 Hipótesis descartada: filtrar por aerosoles (AOT)
- **Qué pasó:** en el lote de ejemplo, el 05/01/2026 el NDVI cayó de 0,87 a 0,38 en todo el lote
  y volvió a 0,89 dos semanas después. El rojo subió de 0,03 a 0,11 y el NIR casi no cambió,
  típico de bruma. Leí la banda AOT (espesor óptico de aerosoles): **0,228, igual que en días
  normales**.
- **Decisión:** no agregué un filtro por AOT porque no habría resuelto este caso. La caída es
  gradual y la ven tres satélites distintos (S2A, S2B, S2C), así que podría ser algo real
  (por ejemplo, estrés por calor). Justo para esto el texto de la alerta no diagnostica.
- **Lección:** medir antes de agregar código.

### 7.5 Exportar la zona afectada a KML y GeoJSON
- **Por qué:** la máscara de píxeles se vectoriza (`rasterio.features.shapes`), se pasa a WGS84
  y se descarga. El KML se abre en Google Earth o se carga en el planificador de vuelo de un dron
  para ir a mirar exactamente esa zona.

### 7.6 El texto siempre lista causas posibles
- Cosecha, granizo, helada, plagas o enfermedades, falta de agua, anegamiento o herbicida, y
  "conviene recorrer la zona". Si pasaron más de 20 días entre fechas, lo avisa.

---

## 8. Calidad y deploy

### 8.1 Tests sin internet
- Arrays inventados con valores de reflectancia realistas (por ejemplo, soja: rojo 0,04, NIR
  0,45 → NDVI 0,84). Prueban escala/offset, máscara SCL, umbral, UTM, hectáreas, caché ida y
  vuelta, alertas (bloque, ruido, nubes, confirmación) y KML ida y vuelta.
- Un test de humo carga la app entera con `streamlit.testing.AppTest` usando el lote de ejemplo.
- **Limitación:** `AppTest` no permite mover un `select_slider` con fechas; eso lo probé a mano.

### 8.2 ruff para lint y formato
- **Descarté:** flake8 + black + isort (tres herramientas). ruff hace todo, en una sola.

### 8.3 GitHub Actions con las mismas dependencias que producción
- La CI instala `requirements.txt` (no el `pyproject`), así prueba exactamente lo que corre en
  la demo. Python 3.13, igual que en local.

### 8.4 `requirements.txt` con versiones fijas y `-e .`
- **Fijas:** la demo usa lo mismo que se probó; verifiqué la instalación en un entorno limpio.
- **`-e .` (editable):** Streamlit Cloud reinstala dependencias solo si cambia
  `requirements.txt`. Con una instalación normal, un cambio en `src/` no se vería después de un
  push; con modo editable sí.

### 8.5 Commits con email "noreply" de GitHub
- Para no exponer el email personal en el historial público.

---

## Preguntas probables de entrevista

**¿Por qué NDVI y no otro índice?** Es el estándar, se entiende fácil y usa las bandas de 10 m.
Su límite es que se satura en cultivos densos; el próximo paso sería NDRE (borde rojo, 20 m).

**¿Cómo manejás las nubes?** Con la banda SCL de Sen2Cor, mirando solo los píxeles del lote, con
un umbral de 80 % limpio. Como la SCL no es perfecta, las alertas se confirman con la fecha
siguiente.

**¿Por qué tu app no baja imágenes enteras?** Porque son COG: tienen bloques internos y se pueden
pedir por rangos HTTP. Leo solo los bloques del lote.

**¿Qué pasa si Earth Search se cae?** Se reintenta en Planetary Computer automáticamente.

**¿Cómo calculás hectáreas?** Reproyecto a la zona UTM del lote (calculada desde su centroide) y
mido en metros. En grados o Web Mercator daría mal.

**¿Qué es el offset de −0,1?** Desde 2022 ESA suma 1000 a los valores digitales para poder
guardar reflectancias negativas. Si no se resta, el NDVI sale más bajo. Lo leo de la metadata
STAC de cada imagen.

**¿Cómo sabés que tus números están bien?** Tests con valores conocidos, rango siempre en [−1, 1],
y comprobación con datos reales: la curva del lote dibujado muestra trigo y después soja de
segunda, y una alerta coincide con un potrero cosechado.

**¿Qué mejorarías con más tiempo?** Radar Sentinel-1 para épocas de nubes, NDRE, zonas de
manejo para prescripciones variables y avisos automáticos.
