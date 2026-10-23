import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import type { Bounds, LayerDisplay, LayerInfo } from "../types";

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
  onPick: (lon: number, lat: number) => void;
  onCursor: (lonLat: [number, number] | null) => void;
}

// MapLibre parses tiles in a worker; tell it where the bundler put the worker's file.
maplibregl.setWorkerUrl(workerUrl);

const ACCENT = "#8ab4ff";
// OpenFreeMap: OpenStreetMap vector tiles, free and without an API key.
const BASEMAP_STYLE = "https://tiles.openfreemap.org/styles/dark";

interface Managed {
  key: string;
  source: string;
  layers: string[];
}

/** The display URL and corners decide whether a layer's source must be rebuilt. */
function keyOf(d: DrawnLayer): string {
  return `${d.display.mode}|${d.display.url}|${JSON.stringify(d.display.coordinates)}`;
}

const absolute = (url: string) => window.location.origin + url;

function addLayer(map: maplibregl.Map, d: DrawnLayer, index: number, before?: string): Managed {
  const ref = d.info.data_ref;
  const source = `src-${ref}-${index}`;
  const { display } = d;
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
    return { key: keyOf(d), source, layers: [`${source}-fill`, `${source}-line`, `${source}-point`] };
  }
  if (display.mode === "tiles") {
    map.addSource(source, {
      type: "raster",
      tiles: [absolute(display.url)],
      tileSize: 256,
      bounds: d.info.bounds_wgs84 ?? undefined,
      maxzoom: 22,
    });
  } else {
    const corners = display.coordinates as [[number, number], [number, number], [number, number], [number, number]];
    map.addSource(source, { type: "image", url: absolute(display.url), coordinates: corners });
  }
  map.addLayer({
    id: `${source}-raster`,
    type: "raster",
    source,
    paint: { "raster-resampling": "nearest", "raster-fade-duration": 180 },
  }, before);
  return { key: keyOf(d), source, layers: [`${source}-raster`] };
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
  { drawn, basemapVisible, marker, scaleSlot, onPick, onCursor },
  ref,
) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const managed = useRef(new Map<string, Managed>());
  const counter = useRef(0);
  const markerRef = useRef<maplibregl.Marker | null>(null);
  const basemapLayers = useRef<string[]>([]);
  const labels = useRef<string | undefined>(undefined); // data goes below the first label layer
  const handlers = useRef({ onPick, onCursor });
  handlers.current = { onPick, onCursor };
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
      setReady(true);
    });
    map.on("click", (e) => handlers.current.onPick(e.lngLat.lng, e.lngLat.lat));
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

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const wanted = new Map(drawn.map((d) => [d.info.data_ref, d]));
    for (const [ref, m] of managed.current) {
      const next = wanted.get(ref);
      if (!next || keyOf(next) !== m.key) {
        remove(map, m);
        managed.current.delete(ref);
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
  }, [ready, drawn]);

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
