import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import type { Bounds, CheckStatus, LayerDisplay, LayerInfo } from "../types";

export interface DrawnLayer {
  info: LayerInfo;
  display: LayerDisplay;
  opacity: number; // 0..1
}

export interface MapHandle {
  zoomIn(): void;
  zoomOut(): void;
  fit(bounds: Bounds): void;
}

interface Props {
  drawn: DrawnLayer[]; // bottom to top
  basemapVisible: boolean;
  marker: [number, number] | null;
  scaleSlot: HTMLElement | null;
  outsideTiles: string | null; // tiles of pixels outside a checked range, drawn above the data
  footprints: { bounds: Bounds; status: CheckStatus }[]; // inputs a check compared
  area: Bounds | null;
  drawing: boolean; // dragging draws an area instead of panning
  onArea: (bounds: Bounds) => void;
  onPick: (lon: number, lat: number) => void;
  onCursor: (lonLat: [number, number] | null) => void;
}

// MapLibre parses tiles in a worker; tell it where the bundler put the worker's file.
maplibregl.setWorkerUrl(workerUrl);

const ACCENT = "#8ab4ff";
const STATUS: Record<CheckStatus, string> = { pass: "#5bd49b", warn: "#f2b45a", fail: "#f2766b" };
// OpenFreeMap: OpenStreetMap vector tiles, free and without an API key.
const BASEMAP_STYLE = "https://tiles.openfreemap.org/styles/dark";
const OUTSIDE = "focus-outside";
const FOOTPRINTS = "focus-footprints";
const AREA = "area";
const MIN_DRAG = 6; // pixels: a shorter drag is a click, not an area

interface Managed {
  mode: LayerDisplay["mode"];
  url: string;
  source: string;
  layers: string[];
}

interface Polygons {
  type: "FeatureCollection";
  features: {
    type: "Feature";
    properties: { status?: CheckStatus };
    geometry: { type: "Polygon"; coordinates: [number, number][][] };
  }[];
}

const absolute = (url: string) => window.location.origin + url;

function boxes(items: { bounds: Bounds; status?: CheckStatus }[]): Polygons {
  return {
    type: "FeatureCollection",
    features: items.map(({ bounds: [w, s, e, n], status }) => ({
      type: "Feature",
      properties: { status },
      geometry: { type: "Polygon", coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] },
    })),
  };
}

function addLayer(map: maplibregl.Map, d: DrawnLayer, index: number, before?: string): Managed {
  const ref = d.info.data_ref;
  const source = `src-${ref}-${index}`;
  const { display } = d;
  const managed = { mode: display.mode, url: display.url, source };
  if (display.mode === "geojson") {
    map.addSource(source, { type: "geojson", data: absolute(display.url) });
    const polygons: maplibregl.FilterSpecification = [
      "match", ["geometry-type"], ["Polygon", "MultiPolygon"], true, false,
    ];
    const points: maplibregl.FilterSpecification = [
      "match", ["geometry-type"], ["Point", "MultiPoint"], true, false,
    ];
    map.addLayer({ id: `${source}-fill`, type: "fill", source, filter: polygons, paint: { "fill-color": ACCENT, "fill-opacity": 0.16 } }, before);
    map.addLayer({ id: `${source}-line`, type: "line", source, paint: { "line-color": ACCENT, "line-width": 1.5 } }, before);
    map.addLayer({
      id: `${source}-point`,
      type: "circle",
      source,
      filter: points,
      paint: { "circle-radius": 4.5, "circle-color": ACCENT, "circle-stroke-color": "#0a0b0d", "circle-stroke-width": 1.5 },
    }, before);
    return { ...managed, layers: [`${source}-fill`, `${source}-line`, `${source}-point`] };
  }
  map.addSource(source, {
    type: "raster",
    tiles: [absolute(display.url)],
    tileSize: 256,
    bounds: d.info.bounds_wgs84 ?? undefined,
    maxzoom: 22,
  });
  map.addLayer({
    id: `${source}-raster`,
    type: "raster",
    source,
    paint: { "raster-resampling": "nearest", "raster-fade-duration": 180 },
  }, before);
  return { ...managed, layers: [`${source}-raster`] };
}

function setOpacity(map: maplibregl.Map, managed: Managed, opacity: number) {
  for (const id of managed.layers) {
    if (id.endsWith("-raster")) map.setPaintProperty(id, "raster-opacity", opacity);
    if (id.endsWith("-fill")) map.setPaintProperty(id, "fill-opacity", 0.16 * opacity);
    if (id.endsWith("-line")) map.setPaintProperty(id, "line-opacity", opacity);
    if (id.endsWith("-point")) map.setPaintProperty(id, "circle-opacity", opacity);
  }
}

function remove(map: maplibregl.Map, managed: Managed) {
  for (const id of managed.layers) if (map.getLayer(id)) map.removeLayer(id);
  if (map.getSource(managed.source)) map.removeSource(managed.source);
}

export const MapView = forwardRef<MapHandle, Props>(function MapView(
  { drawn, basemapVisible, marker, scaleSlot, outsideTiles, footprints, area, drawing, onArea, onPick, onCursor },
  ref,
) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const managed = useRef(new Map<string, Managed>());
  const counter = useRef(0);
  const markerRef = useRef<maplibregl.Marker | null>(null);
  const basemapLayers = useRef<string[]>([]);
  const labels = useRef<string | undefined>(undefined); // data goes below the first label layer
  const drewAt = useRef(0); // when an area was last finished, so its mouseup is not also a click
  const handlers = useRef({ onPick, onCursor, onArea });
  handlers.current = { onPick, onCursor, onArea };
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!container.current) return;
    const map = new maplibregl.Map({
      container: container.current,
      style: BASEMAP_STYLE,
      center: [151.21, -33.85],
      zoom: 2,
      attributionControl: false,
      fadeDuration: 150,
    });
    map.addControl(new maplibregl.AttributionControl({ compact: true }), "bottom-right");
    map.on("load", () => {
      const layers = map.getStyle().layers;
      basemapLayers.current = layers.filter((l) => l.type !== "background").map((l) => l.id);
      // Labels go above everything, data goes just below them: roads and land stay underneath.
      const symbols = layers.filter((l) => l.type === "symbol").map((l) => l.id);
      for (const id of symbols) map.moveLayer(id);
      labels.current = symbols[0];
      const empty = boxes([]);
      map.addSource(FOOTPRINTS, { type: "geojson", data: empty });
      map.addLayer({
        id: `${FOOTPRINTS}-line`,
        type: "line",
        source: FOOTPRINTS,
        paint: { "line-color": ["match", ["get", "status"], "fail", STATUS.fail, STATUS.warn], "line-width": 2 },
      });
      map.addSource(AREA, { type: "geojson", data: empty });
      map.addLayer({ id: `${AREA}-fill`, type: "fill", source: AREA, paint: { "fill-color": ACCENT, "fill-opacity": 0.08 } });
      map.addLayer({ id: `${AREA}-line`, type: "line", source: AREA, paint: { "line-color": ACCENT, "line-width": 2 } });
      setReady(true);
    });
    map.on("click", (e) => {
      if (Date.now() - drewAt.current > 300) handlers.current.onPick(e.lngLat.lng, e.lngLat.lat);
    });
    map.on("mousemove", (e) => handlers.current.onCursor([e.lngLat.lng, e.lngLat.lat]));
    map.on("mouseout", () => handlers.current.onCursor(null));
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !scaleSlot) return;
    const scale = new maplibregl.ScaleControl({ maxWidth: 90, unit: "metric" });
    const element = scale.onAdd(map);
    scaleSlot.appendChild(element);
    return () => {
      scale.onRemove();
    };
  }, [scaleSlot]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    for (const id of basemapLayers.current) {
      map.setLayoutProperty(id, "visibility", basemapVisible ? "visible" : "none");
    }
  }, [ready, basemapVisible]);

  // Data layers, then what a check found above them; only labels and the drawn area are higher.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const wanted = new Map(drawn.map((d) => [d.info.data_ref, d]));
    for (const [ref, m] of managed.current) {
      const next = wanted.get(ref);
      if (!next || next.display.mode !== m.mode) {
        remove(map, m);
        managed.current.delete(ref);
      } else if (next.display.url !== m.url) {
        // Another date or band: swap the tiles in place, so the map does not flash empty.
        const source = map.getSource(m.source);
        if (source instanceof maplibregl.RasterTileSource) source.setTiles([absolute(next.display.url)]);
        if (source instanceof maplibregl.GeoJSONSource) source.setData(absolute(next.display.url));
        m.url = next.display.url;
      }
    }
    for (const d of drawn) {
      let m = managed.current.get(d.info.data_ref);
      if (!m) {
        m = addLayer(map, d, counter.current++, labels.current);
        managed.current.set(d.info.data_ref, m);
      }
      setOpacity(map, m, d.opacity);
    }
    for (const d of drawn) {
      for (const id of managed.current.get(d.info.data_ref)?.layers ?? []) map.moveLayer(id, labels.current);
    }
    if (map.getLayer(`${OUTSIDE}-raster`)) map.moveLayer(`${OUTSIDE}-raster`, labels.current);
    map.moveLayer(`${FOOTPRINTS}-line`, labels.current);
  }, [ready, drawn]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    if (map.getLayer(`${OUTSIDE}-raster`)) map.removeLayer(`${OUTSIDE}-raster`);
    if (map.getSource(OUTSIDE)) map.removeSource(OUTSIDE);
    if (!outsideTiles) return;
    map.addSource(OUTSIDE, { type: "raster", tiles: [absolute(outsideTiles)], tileSize: 256, maxzoom: 22 });
    map.addLayer(
      { id: `${OUTSIDE}-raster`, type: "raster", source: OUTSIDE, paint: { "raster-resampling": "nearest" } },
      `${FOOTPRINTS}-line`,
    );
  }, [ready, outsideTiles]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    (map.getSource(FOOTPRINTS) as maplibregl.GeoJSONSource).setData(boxes(footprints));
  }, [ready, footprints]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    (map.getSource(AREA) as maplibregl.GeoJSONSource).setData(boxes(area ? [{ bounds: area }] : []));
  }, [ready, area]);

  // Drawing an area: drag a box; panning is off until it is drawn.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !drawing) return;
    const canvas = map.getCanvas();
    map.dragPan.disable();
    canvas.style.cursor = "crosshair";
    let start: maplibregl.MapMouseEvent | null = null;
    const corners = (a: maplibregl.LngLat, b: maplibregl.LngLat): Bounds => [
      Math.min(a.lng, b.lng),
      Math.min(a.lat, b.lat),
      Math.max(a.lng, b.lng),
      Math.max(a.lat, b.lat),
    ];
    const source = map.getSource(AREA) as maplibregl.GeoJSONSource;
    const down = (e: maplibregl.MapMouseEvent) => {
      start = e;
    };
    const move = (e: maplibregl.MapMouseEvent) => {
      if (start) source.setData(boxes([{ bounds: corners(start.lngLat, e.lngLat) }]));
    };
    const up = (e: maplibregl.MapMouseEvent) => {
      if (!start) return;
      const far = Math.hypot(e.point.x - start.point.x, e.point.y - start.point.y) >= MIN_DRAG;
      if (far) {
        drewAt.current = Date.now();
        handlers.current.onArea(corners(start.lngLat, e.lngLat));
      }
      start = null;
    };
    map.on("mousedown", down);
    map.on("mousemove", move);
    map.on("mouseup", up);
    return () => {
      map.off("mousedown", down);
      map.off("mousemove", move);
      map.off("mouseup", up);
      map.dragPan.enable();
      canvas.style.cursor = "";
    };
  }, [ready, drawing]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (!marker) {
      markerRef.current?.remove();
      markerRef.current = null;
      return;
    }
    if (!markerRef.current) {
      const element = document.createElement("div");
      element.className = "marker";
      markerRef.current = new maplibregl.Marker({ element });
    }
    markerRef.current.setLngLat(marker).addTo(map);
  }, [marker]);

  useImperativeHandle(ref, () => ({
    zoomIn: () => mapRef.current?.zoomIn({ duration: 350 }),
    zoomOut: () => mapRef.current?.zoomOut({ duration: 350 }),
    fit: ([west, south, east, north]) =>
      mapRef.current?.fitBounds(
        [
          [west, south],
          [east, north],
        ],
        { padding: 80, duration: 900, maxZoom: 17 },
      ),
  }));

  return <div ref={container} className="map" role="region" aria-label="Map" />;
});
