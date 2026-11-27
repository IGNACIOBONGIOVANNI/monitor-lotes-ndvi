/*
 * Manijas para girar (↻) y mover (✥) los lotes dibujados en el mapa.
 *
 * Cómo se integra:
 * - Los dibujos viven en el FeatureGroup editable de Leaflet.draw (lo trae folium).
 * - Cada polígono del grupo recibe dos manijas: ✥ en su centro y ↻ arriba de su borde.
 *   Se mantiene apretada una manija y se arrastra: la figura se mueve o gira en vivo.
 * - Al soltar se dispara "draw:edited" en el mapa, el mismo evento que usa Leaflet.draw al
 *   editar vértices. streamlit-folium escucha ese evento, relee todo el grupo y le pasa los
 *   dibujos a Python: no hace falta otro canal de comunicación.
 *
 * Geometría: los cálculos se hacen en píxeles de Web Mercator (map.project). Mercator es una
 * proyección conforme: a la escala de un lote conserva los ángulos y la forma, así que girar
 * ahí no deforma el lote. Los ángulos se redondean a grados enteros.
 *
 * Las funciones de geometría no dependen de Leaflet para poder testearlas con Node.
 */
var ManijasLote = (function () {
  "use strict";

  // ------------------------------------------------------------ geometría pura

  /** Centroide (por área) de un anillo de puntos {x, y}. */
  function centroide(pts) {
    var n = pts.length;
    var area = 0;
    var cx = 0;
    var cy = 0;
    for (var i = 0; i < n; i++) {
      var p = pts[i];
      var q = pts[(i + 1) % n];
      var f = p.x * q.y - q.x * p.y;
      area += f;
      cx += (p.x + q.x) * f;
      cy += (p.y + q.y) * f;
    }
    if (Math.abs(area) < 1e-9) {
      // Polígono degenerado: promedio simple de los vértices
      var sx = 0;
      var sy = 0;
      for (var j = 0; j < n; j++) {
        sx += pts[j].x;
        sy += pts[j].y;
      }
      return { x: sx / n, y: sy / n };
    }
    return { x: cx / (3 * area), y: cy / (3 * area) };
  }

  /** Gira el punto p alrededor de c. En pantalla (y hacia abajo), positivo = horario. */
  function girarPunto(p, c, grados) {
    var a = (grados * Math.PI) / 180;
    var cos = Math.cos(a);
    var sin = Math.sin(a);
    var x = p.x - c.x;
    var y = p.y - c.y;
    return { x: c.x + x * cos - y * sin, y: c.y + x * sin + y * cos };
  }

  /** Grados enteros que giró la manija alrededor de c, de `desde` a `hasta`, en (-180, 180]. */
  function anguloGrados(c, desde, hasta) {
    var a0 = Math.atan2(desde.y - c.y, desde.x - c.x);
    var a1 = Math.atan2(hasta.y - c.y, hasta.x - c.x);
    var g = Math.round(((a1 - a0) * 180) / Math.PI);
    g = ((((g + 180) % 360) + 360) % 360) - 180; // normaliza a [-180, 180)
    return g === -180 ? 180 : g;
  }

  /** Aplica fn a cada punto de un arreglo anidado (anillos de un polígono) sin cambiarlo. */
  function mapear(arr, fn) {
    return arr.map(function (x) {
      return Array.isArray(x) ? mapear(x, fn) : fn(x);
    });
  }

  /** Primer anillo (el borde exterior) de un arreglo anidado de puntos. */
  function exterior(arr) {
    while (arr.length && Array.isArray(arr[0])) {
      arr = arr[0];
    }
    return arr;
  }

  // ------------------------------------------------------------ manijas en el mapa

  var ZOOM_CALCULO = 20; // zoom fijo para proyectar a píxeles (más precisión)
  var SEPARACION_GIRO = 34; // píxeles entre el borde superior del lote y la manija ↻

  var ICONO_MOVER =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" ' +
    'stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v18M3 12h18"/>' +
    '<path d="M9 6l3-3 3 3M9 18l3 3 3-3M6 9l-3 3 3 3M18 9l3 3-3 3"/></svg>';
  var ICONO_GIRAR =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" ' +
    'stroke-linecap="round" stroke-linejoin="round"><path d="M20 12a8 8 0 1 1-2.6-5.9"/>' +
    '<path d="M20 4v5h-5"/></svg>';

  var CSS =
    ".ml-manija{background:transparent;border:none}" +
    ".ml-manija svg{width:32px;height:32px;box-sizing:border-box;padding:6px;" +
    "border-radius:50%;background:#fff;color:#0b0b0b;border:2px solid #ffd60a;" +
    "box-shadow:0 1px 4px rgba(0,0,0,.55)}" +
    ".ml-mover svg{cursor:move}" +
    ".ml-girar svg{cursor:grab}" +
    ".ml-girar.leaflet-drag-target svg{cursor:grabbing}";

  function agregarEstilos() {
    if (typeof document === "undefined" || document.getElementById("ml-manijas-css")) {
      return;
    }
    var estilo = document.createElement("style");
    estilo.id = "ml-manijas-css";
    estilo.textContent = CSS;
    document.head.appendChild(estilo);
  }

  /**
   * Instala las manijas sobre todos los polígonos de `grupo` (actuales y futuros).
   * `mapa` es el L.Map y `grupo` el FeatureGroup editable de Leaflet.draw.
   */
  function instalar(mapa, grupo) {
    agregarEstilos();
    var capa = L.layerGroup().addTo(mapa); // las manijas NO van en el grupo: no son dibujos
    var manijas = {}; // id de Leaflet del polígono -> {mover, girar}
    var estado = null; // datos del arrastre en curso

    function proyectar(ll) {
      return mapa.project(ll, ZOOM_CALCULO);
    }

    function desproyectar(p) {
      return mapa.unproject(L.point(p.x, p.y), ZOOM_CALCULO);
    }

    function icono(svg, clase) {
      return L.divIcon({
        className: "ml-manija " + clase,
        html: svg,
        iconSize: [32, 32],
        iconAnchor: [16, 16],
      });
    }

    function ubicar(pol) {
      var m = manijas[L.stamp(pol)];
      if (!m) {
        return;
      }
      var pts = exterior(pol.getLatLngs()).map(function (ll) {
        return mapa.latLngToLayerPoint(ll);
      });
      if (!pts.length) {
        return;
      }
      var c = centroide(pts);
      var arriba = Math.min.apply(
        null,
        pts.map(function (p) {
          return p.y;
        })
      );
      m.mover.setLatLng(mapa.layerPointToLatLng(L.point(c.x, c.y)));
      m.girar.setLatLng(mapa.layerPointToLatLng(L.point(c.x, arriba - SEPARACION_GIRO)));
    }

    function ubicarTodas() {
      grupo.eachLayer(ubicar);
    }

    function empezar(pol, manija, tipo, otra) {
      var pts = mapear(pol.getLatLngs(), proyectar);
      estado = {
        tipo: tipo,
        pts: pts,
        centro: centroide(exterior(pts)),
        inicio: proyectar(manija.getLatLng()),
      };
      otra.setOpacity(0); // la otra manija se esconde mientras se arrastra
    }

    function arrastrar(pol, manija) {
      if (!estado) {
        return;
      }
      var p = proyectar(manija.getLatLng());
      var nuevo;
      if (estado.tipo === "mover") {
        var dx = p.x - estado.inicio.x;
        var dy = p.y - estado.inicio.y;
        nuevo = mapear(estado.pts, function (q) {
          return desproyectar({ x: q.x + dx, y: q.y + dy });
        });
      } else {
        var grados = anguloGrados(estado.centro, estado.inicio, p);
        nuevo = mapear(estado.pts, function (q) {
          return desproyectar(girarPunto(q, estado.centro, grados));
        });
        manija.setTooltipContent((grados > 0 ? "+" : "") + grados + "°");
        manija.openTooltip();
      }
      pol.setLatLngs(nuevo);
    }

    function terminar(pol, manija, otra) {
      estado = null;
      otra.setOpacity(1);
      if (manija.getTooltip()) {
        manija.closeTooltip();
      }
      ubicar(pol);
      // Leaflet.draw guarda una referencia a los vértices de cuando se creó la figura; si no
      // se le avisa, el modo ✎ Editar mostraría los vértices de antes del giro.
      pol.fire("revert-edited", { layer: pol });
      // Mismo evento que Leaflet.draw al editar: streamlit-folium relee los dibujos
      mapa.fire("draw:edited", { layers: L.layerGroup([pol]), layer: pol });
    }

    function agregar(pol) {
      if (!(pol instanceof L.Polygon)) {
        return;
      }
      var id = L.stamp(pol);
      if (manijas[id]) {
        return;
      }
      var mover = L.marker(pol.getBounds().getCenter(), {
        icon: icono(ICONO_MOVER, "ml-mover"),
        draggable: true,
        keyboard: false,
        zIndexOffset: 1000,
        title: "Mantené apretado y arrastrá para mover el lote",
      });
      var girar = L.marker(pol.getBounds().getCenter(), {
        icon: icono(ICONO_GIRAR, "ml-girar"),
        draggable: true,
        keyboard: false,
        zIndexOffset: 1000,
        title: "Mantené apretado y arrastrá para girar el lote",
      });
      girar.bindTooltip("", { direction: "top", offset: [0, -18] });

      mover.on("dragstart", function () {
        empezar(pol, mover, "mover", girar);
      });
      mover.on("drag", function () {
        arrastrar(pol, mover);
      });
      mover.on("dragend", function () {
        terminar(pol, mover, girar);
      });
      girar.on("dragstart", function () {
        empezar(pol, girar, "girar", mover);
      });
      girar.on("drag", function () {
        arrastrar(pol, girar);
      });
      girar.on("dragend", function () {
        terminar(pol, girar, mover);
      });

      manijas[id] = { mover: mover, girar: girar };
      capa.addLayer(mover);
      capa.addLayer(girar);
      ubicar(pol);
    }

    function quitar(pol) {
      var id = L.stamp(pol);
      var m = manijas[id];
      if (m) {
        capa.removeLayer(m.mover);
        capa.removeLayer(m.girar);
        delete manijas[id];
      }
    }

    grupo.on("layeradd", function (e) {
      agregar(e.layer);
    });
    grupo.on("layerremove", function (e) {
      quitar(e.layer);
    });

    // Un rectángulo de Leaflet.draw vuelve a quedar alineado norte-sur al editarlo. Como
    // polígono común se puede girar y después editar vértice por vértice. Se convierte acá
    // (y no en "layeradd") porque folium agrega cada figura nueva al grupo dos veces: este
    // manejador corre después de los dos agregados y antes de que streamlit-folium la lea.
    mapa.on("draw:created", function (e) {
      if (e.layer instanceof L.Rectangle) {
        var pol = L.polygon(e.layer.getLatLngs(), e.layer.options);
        grupo.removeLayer(e.layer);
        grupo.addLayer(pol);
        e.layer = pol;
      }
    });

    // Mientras se dibuja, edita o borra con Leaflet.draw, las manijas se esconden
    mapa.on("draw:drawstart draw:editstart draw:deletestart", function () {
      mapa.removeLayer(capa);
    });
    mapa.on("draw:drawstop draw:editstop draw:deletestop", function () {
      capa.addTo(mapa);
      ubicarTodas();
    });
    mapa.on("zoomend draw:edited", ubicarTodas);

    grupo.eachLayer(agregar);
  }

  return {
    centroide: centroide,
    girarPunto: girarPunto,
    anguloGrados: anguloGrados,
    instalar: instalar,
  };
})();

if (typeof module !== "undefined" && module.exports) {
  module.exports = ManijasLote;
}
