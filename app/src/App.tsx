import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, watchWorkspace } from "./api";
import { AgentPanel } from "./components/AgentPanel";
import { CodePanel } from "./components/CodePanel";
import { LayersPanel } from "./components/LayersPanel";
import { LineagePanel } from "./components/LineagePanel";
import { type DrawnLayer, type MapHandle, MapView } from "./components/MapView";
import { Inspector, Legend, Readout } from "./components/Overlays";
import { Icon, Logo } from "./icons";
import type { LayerDisplay, LayerInfo, Model, PointValues } from "./types";

type Tab = "agent" | "code" | "lineage";

const message = (e: unknown) => (e instanceof ApiError ? e.message : String(e));

export function App() {
  const [project, setProject] = useState("");
  const [layers, setLayers] = useState<LayerInfo[]>([]);
  const [version, setVersion] = useState(0);
  const [visible, setVisible] = useState<Record<string, boolean>>({});
  const [opacity, setOpacity] = useState<Record<string, number>>({});
  const [bands, setBands] = useState<Record<string, string[]>>({});
  const [displays, setDisplays] = useState<Record<string, LayerDisplay>>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("agent");
  const [layersOpen, setLayersOpen] = useState(true);
  const [basemap, setBasemap] = useState(true);
  const [models, setModels] = useState<Model[]>([]);
  const [model, setModel] = useState("opus");
  const [point, setPoint] = useState<[number, number] | null>(null);
  const [values, setValues] = useState<PointValues | null>(null);
  const [pointError, setPointError] = useState<string | null>(null);
  const [cursor, setCursor] = useState<[number, number] | null>(null);
  const [scaleSlot, setScaleSlot] = useState<HTMLDivElement | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [scriptRequest, setScriptRequest] = useState<{ path: string; key: number } | null>(null);
  const [finder, setFinder] = useState("");
  const mapRef = useRef<MapHandle>(null);
  const known = useRef<Set<string> | null>(null);
  const loading = useRef(new Set<string>());

  const byRef = useMemo(() => new Map(layers.map((l) => [l.data_ref, l])), [layers]);

  const fit = useCallback(
    (ref: string) => {
      const bounds = byRef.get(ref)?.bounds_wgs84;
      if (bounds) mapRef.current?.fit(bounds);
    },
    [byRef],
  );

  const refreshLayers = useCallback(async () => {
    const list = await api.layers();
    setLayers(list);
    const drawable = list.filter((l) => l.drawable);
    if (known.current === null) {
      // First load: show everything except search footprints; select the newest layer.
      known.current = new Set(list.map((l) => l.data_ref));
      setVisible(Object.fromEntries(drawable.map((l) => [l.data_ref, l.produced_by !== "stac_search"])));
      const first = drawable.find((l) => l.produced_by !== "stac_search") ?? drawable[0];
      if (first) {
        setSelected(first.data_ref);
        if (first.bounds_wgs84) setTimeout(() => mapRef.current?.fit(first.bounds_wgs84!), 300);
      }
      return;
    }
    // Later: new layers (from the agent or a run) appear, get selected, and the map flies there.
    const fresh = drawable.filter((l) => !known.current!.has(l.data_ref));
    for (const l of list) known.current.add(l.data_ref);
    if (fresh.length) {
      setVisible((v) => ({ ...v, ...Object.fromEntries(fresh.map((l) => [l.data_ref, l.produced_by !== "stac_search"])) }));
      const newest = fresh[0];
      setSelected(newest.data_ref);
      if (newest.bounds_wgs84) mapRef.current?.fit(newest.bounds_wgs84);
    }
  }, []);

  useEffect(() => {
    api.models().then((m) => {
      setModels(m.models);
      setModel(m.default);
    }, () => undefined);
    fetch("/api/project")
      .then((r) => r.json())
      .then((p) => setProject(p.project), () => undefined);
    void refreshLayers();
    return watchWorkspace(() => {
      void refreshLayers();
      setVersion((n) => n + 1);
    });
  }, [refreshLayers]);

  // Fetch how to draw each visible layer (again when its bands change).
  useEffect(() => {
    for (const layer of layers) {
      const ref = layer.data_ref;
      const want = bands[ref];
      const have = displays[ref];
      const stale = have && want && have.bands?.join(",") !== want.join(",");
      if (!visible[ref] || !layer.drawable || (have && !stale) || loading.current.has(ref)) continue;
      loading.current.add(ref);
      api
        .display(ref, want)
        .then((d) => setDisplays((all) => ({ ...all, [ref]: d })))
        .catch((e) => setToast(`${layer.name}: ${message(e)}`))
        .finally(() => loading.current.delete(ref));
    }
  }, [layers, visible, bands, displays]);

  const drawn: DrawnLayer[] = useMemo(
    () =>
      [...layers]
        .reverse()
        .filter((l) => visible[l.data_ref] && displays[l.data_ref])
        .map((l) => ({ info: l, display: displays[l.data_ref], opacity: opacity[l.data_ref] ?? 1 })),
    [layers, visible, displays, opacity],
  );

  const selectRef = useCallback(
    (ref: string) => {
      if (!byRef.has(ref)) {
        setToast(`${ref} is not in this workspace.`);
        return;
      }
      setSelected(ref);
      setVisible((v) => ({ ...v, [ref]: true }));
      fit(ref);
    },
    [byRef, fit],
  );

  const pick = useCallback(
    async (lon: number, lat: number) => {
      const target =
        (selected && visible[selected] && selected) ||
        [...drawn].reverse().find((d) => d.info.kind === "raster")?.info.data_ref ||
        drawn[drawn.length - 1]?.info.data_ref;
      setPoint([lon, lat]);
      if (!target) return;
      try {
        setValues(await api.point(target, lon, lat));
        setPointError(null);
      } catch (e) {
        setValues(null);
        setPointError(message(e));
      }
    },
    [selected, visible, drawn],
  );

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), 5000);
    return () => clearTimeout(timer);
  }, [toast]);

  const chosen = selected ? byRef.get(selected) ?? null : null;
  const matches = finder.trim()
    ? layers.filter((l) => `${l.name} ${l.data_ref}`.toLowerCase().includes(finder.toLowerCase())).slice(0, 6)
    : [];

  return (
    <div className={`app${layersOpen ? "" : " layers-hidden"}`}>
      <nav className="rail" aria-label="Workspace">
        <div className="logo"><Logo /></div>
        <button type="button" className={`ghost icon-button${layersOpen ? " active" : ""}`} aria-label="Layers" title="Layers" onClick={() => setLayersOpen((o) => !o)}>
          <Icon name="layers" size={20} />
        </button>
        {(["agent", "code", "lineage"] as Tab[]).map((t) => (
          <button key={t} type="button" className={`ghost icon-button${tab === t ? " active" : ""}`} aria-label={t} title={t[0].toUpperCase() + t.slice(1)} onClick={() => setTab(t)}>
            <Icon name={t} size={20} />
          </button>
        ))}
      </nav>

      <LayersPanel
        project={project}
        layers={layers}
        visible={visible}
        opacity={opacity}
        displays={displays}
        selected={selected}
        basemapVisible={basemap}
        onToggle={(ref) => setVisible((v) => ({ ...v, [ref]: !v[ref] }))}
        onSelect={setSelected}
        onFit={fit}
        onOpacity={(ref, value) => setOpacity((o) => ({ ...o, [ref]: value }))}
        onBands={(ref, value) => setBands((b) => ({ ...b, [ref]: value }))}
        onToggleBasemap={() => setBasemap((b) => !b)}
      />

      <main className="map-area">
        <MapView
          ref={mapRef}
          drawn={drawn}
          basemapVisible={basemap}
          marker={point}
          scaleSlot={scaleSlot}
          onPick={(lon, lat) => void pick(lon, lat)}
          onCursor={setCursor}
        />
        <div className="overlay finder">
          <span className="icon"><Icon name="search" size={16} /></span>
          <label htmlFor="finder" className="visually-hidden">Find a layer</label>
          <input
            id="finder"
            className="glass"
            placeholder="Find a layer by name or data_ref"
            value={finder}
            onChange={(e) => setFinder(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && matches[0]) {
                selectRef(matches[0].data_ref);
                setFinder("");
              }
            }}
          />
          {matches.length > 0 && (
            <ul className="glass">
              {matches.map((l) => (
                <li key={l.data_ref}>
                  <button type="button" className="ghost" onClick={() => { selectRef(l.data_ref); setFinder(""); }}>
                    <span>{l.name}</span>
                    <span className="ref">{l.data_ref}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="overlay zoom glass">
          <button type="button" className="ghost" aria-label="Zoom in" onClick={() => mapRef.current?.zoomIn()}><Icon name="plus" size={16} /></button>
          <button type="button" className="ghost" aria-label="Zoom out" onClick={() => mapRef.current?.zoomOut()}><Icon name="minus" size={16} /></button>
          <button type="button" className="ghost" aria-label="Zoom to the selected layer" disabled={!selected} onClick={() => selected && fit(selected)}><Icon name="fit" size={16} /></button>
        </div>
        <Inspector
          key={`${point?.join(",")}`}
          values={values}
          layer={values ? byRef.get(values.data_ref) ?? null : chosen}
          error={pointError}
          onClose={() => { setPoint(null); setValues(null); setPointError(null); }}
        />
        {chosen && visible[chosen.data_ref] && displays[chosen.data_ref] && (
          <Legend layer={chosen} display={displays[chosen.data_ref]} />
        )}
        <Readout cursor={cursor} crs={chosen?.crs ?? null} scaleRef={setScaleSlot} />
        {layers.length === 0 && (
          <div className="overlay empty glass">
            <h2>Your workspace is empty</h2>
            <p>Ask the agent to find data for a place, or load a file. Every layer it makes appears here, checked.</p>
          </div>
        )}
        {toast && <div className="overlay toast glass" role="status">{toast}</div>}
      </main>

      <aside className="side" aria-label="Agent, code and lineage">
        <div className="tabs" role="tablist" aria-label="Panels">
          {(["agent", "code", "lineage"] as Tab[]).map((t) => (
            <button key={t} type="button" role="tab" aria-selected={tab === t} className="ghost" onClick={() => setTab(t)}>
              {t[0].toUpperCase() + t.slice(1)}
            </button>
          ))}
          <span className="spacer" />
          <label htmlFor="model" className="visually-hidden">Model</label>
          <select id="model" value={model} onChange={(e) => setModel(e.target.value)}>
            {models.map((m) => (
              <option key={m.id} value={m.id}>{m.label} · {m.note}</option>
            ))}
          </select>
        </div>
        <AgentPanel open={tab === "agent"} model={model} selected={chosen} point={point} onRef={selectRef} />
        <CodePanel open={tab === "code"} layers={layers} request={scriptRequest} onRef={selectRef} />
        <LineagePanel
          open={tab === "lineage"}
          selected={selected}
          version={version}
          onRef={selectRef}
          onOpenScript={(path) => { setScriptRequest({ path, key: Date.now() }); setTab("code"); }}
        />
      </aside>
    </div>
  );
}
