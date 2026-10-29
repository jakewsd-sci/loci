import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, watchWorkspace } from "./api";
import { AgentPanel } from "./components/AgentPanel";
import { CodePanel, type ScriptRequest } from "./components/CodePanel";
import { LayersPanel } from "./components/LayersPanel";
import { LineagePanel } from "./components/LineagePanel";
import { type DrawnLayer, type MapHandle, MapView } from "./components/MapView";
import { findModel, ModelPicker } from "./components/ModelPicker";
import { FocusChip, Inspector, Legend, Readout } from "./components/Overlays";
import { Icon, Logo } from "./icons";
import type {
  Bounds,
  LayerDisplay,
  LayerInfo,
  MapFocus,
  ModelList,
  PointValues,
  Proposal,
  ProviderInfo,
  Review,
  ScriptFile,
} from "./types";

type Tab = "agent" | "code" | "lineage";

const message = (e: unknown) => (e instanceof ApiError ? e.message : String(e));
const REVIEW_KEY = "loci.review";
const MODEL_KEY = "loci.model";

/** The remembered review mode; browser storage can be missing or blocked, so fall back. */
function storedReview(): Review {
  try {
    return localStorage.getItem(REVIEW_KEY) === "runs" ? "runs" : "changes";
  } catch {
    return "changes";
  }
}

/** The remembered model, if there is one. */
function storedModel(): string | null {
  try {
    return localStorage.getItem(MODEL_KEY);
  } catch {
    return null;
  }
}

/** Whether a model can be used now: listed, and its provider set up. */
function usable(providers: ProviderInfo[], id: string | null): id is string {
  const found = id ? findModel(providers, id) : null;
  return found !== null && found.provider.configured;
}

/** The model to use: the current one if it can be used, else the default, else the first model
 * of a provider that is set up; with none set up, the default, so the app says how to set it up. */
function modelFor(list: ModelList, current: string | null): string {
  if (usable(list.providers, current)) return current;
  if (usable(list.providers, list.default)) return list.default;
  return list.providers.find((p) => p.configured)?.models[0]?.id ?? list.default;
}

/** An API error with its hint, for a message shown in the app. */
const withHint = (e: unknown) =>
  new Error(e instanceof ApiError && e.hint ? `${e.message} ${e.hint}` : message(e));

/** A layer's display at another time step: same bands and stretch, so dates compare. */
function atStep(display: LayerDisplay, step: number | undefined): LayerDisplay {
  if (step == null || display.step == null || step === display.step) return display;
  return { ...display, step, time: display.times[step], url: display.url.replace(/step=\d+/, `step=${step}`) };
}

function union(bounds: Bounds[]): Bounds {
  return [
    Math.min(...bounds.map((b) => b[0])),
    Math.min(...bounds.map((b) => b[1])),
    Math.max(...bounds.map((b) => b[2])),
    Math.max(...bounds.map((b) => b[3])),
  ];
}

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
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [model, setModel] = useState<string>(() => storedModel() ?? "");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [point, setPoint] = useState<[number, number] | null>(null);
  const [values, setValues] = useState<PointValues | null>(null);
  const [pointError, setPointError] = useState<string | null>(null);
  const [reading, setReading] = useState(false);
  const picked = useRef(0); // only the latest click's values are shown
  const [cursor, setCursor] = useState<[number, number] | null>(null);
  const [scaleSlot, setScaleSlot] = useState<HTMLDivElement | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [scriptRequest, setScriptRequest] = useState<ScriptRequest | null>(null);
  const [scripts, setScripts] = useState<ScriptFile[]>([]);
  const [scriptsVersion, setScriptsVersion] = useState(0);
  const [openScript, setOpenScript] = useState<string | null>(null);
  const [pendingReview, setPendingReview] = useState<Proposal | null>(null);
  const [reviewMode, setReviewMode] = useState<Review>(storedReview);
  const [finder, setFinder] = useState("");
  const [steps, setSteps] = useState<Record<string, number>>({});
  const [focus, setFocus] = useState<MapFocus | null>(null);
  const [area, setArea] = useState<Bounds | null>(null);
  const [drawing, setDrawing] = useState(false);
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

  const showModels = useCallback((list: ModelList) => {
    setProviders(list.providers);
    setModelsError(null);
    setModel((current) => modelFor(list, current));
  }, []);

  const refreshModels = useCallback(() => {
    api.models().then(showModels, (e) => setModelsError(withHint(e).message));
  }, [showModels]);

  const openPicker = useCallback(
    (open: boolean) => {
      if (open) refreshModels();
      setPickerOpen(open);
    },
    [refreshModels],
  );

  const saveKey = useCallback(
    async (provider: string, key: string) => {
      try {
        showModels(await api.saveKey(provider, key));
      } catch (e) {
        throw withHint(e);
      }
    },
    [showModels],
  );

  const forgetKey = useCallback(
    async (provider: string) => {
      try {
        showModels(await api.forgetKey(provider));
      } catch (e) {
        setModelsError(withHint(e).message);
      }
    },
    [showModels],
  );

  const chooseModel = useCallback((next: string) => {
    setModel(next);
    try {
      localStorage.setItem(MODEL_KEY, next);
    } catch {
      // Not remembered this time; the choice still holds for this page.
    }
  }, []);

  useEffect(() => {
    refreshModels();
    fetch("/api/project")
      .then((r) => r.json())
      .then(
        (p) => {
          setProject(p.project);
          setSuggestions(p.suggestions ?? []);
        },
        () => undefined,
      );
    void refreshLayers();
    const refreshScripts = () => api.scripts().then(setScripts, () => undefined);
    void refreshScripts();
    return watchWorkspace(
      () => {
        void refreshLayers();
        setVersion((n) => n + 1);
      },
      () => {
        void refreshScripts();
        setScriptsVersion((n) => n + 1);
      },
    );
  }, [refreshLayers, refreshModels]);

  const chooseReview = useCallback((next: Review) => {
    setReviewMode(next);
    try {
      localStorage.setItem(REVIEW_KEY, next);
    } catch {
      // Not remembered this time; the choice still holds for this page.
    }
  }, []);

  const decide = useCallback(async (proposal: Proposal, accepted: boolean, note: string) => {
    try {
      await api.decide(proposal.id, accepted, note);
      setPendingReview((r) => (r?.id === proposal.id ? null : r));
    } catch (e) {
      setToast(message(e));
    }
  }, []);

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

  const shown = useMemo(
    () => Object.fromEntries(Object.entries(displays).map(([ref, d]) => [ref, atStep(d, steps[ref])])),
    [displays, steps],
  );

  const drawn: DrawnLayer[] = useMemo(
    () =>
      [...layers]
        .reverse()
        .filter((l) => visible[l.data_ref] && shown[l.data_ref])
        .map((l) => ({ info: l, display: shown[l.data_ref], opacity: opacity[l.data_ref] ?? 1 })),
    [layers, visible, shown, opacity],
  );

  // What a check found, on the map: out-of-range pixels of one band, or the inputs' footprints.
  const outsideTiles = useMemo(() => {
    if (focus?.kind !== "outside") return null;
    const step = shown[focus.ref]?.step;
    const [low, high] = focus.range;
    return `/api/layers/${focus.ref}/tiles/{z}/{x}/{y}.png?bands=${encodeURIComponent(focus.band)}&outside=${low},${high ?? "inf"}${step != null ? `&step=${step}` : ""}`;
  }, [focus, shown]);
  const footprints = useMemo(
    () =>
      focus?.kind === "footprints"
        ? focus.refs.flatMap((r) => {
            const bounds = byRef.get(r)?.bounds_wgs84;
            return bounds ? [{ bounds, status: focus.status }] : [];
          })
        : [],
    [focus, byRef],
  );

  const showFocus = useCallback(
    (next: MapFocus) => {
      setFocus(next);
      const refs = next.kind === "outside" ? [next.ref] : next.refs;
      if (next.kind === "outside") {
        setSelected(next.ref);
        setVisible((v) => ({ ...v, [next.ref]: true }));
      }
      const bounds = refs.flatMap((r) => (byRef.get(r)?.bounds_wgs84 ? [byRef.get(r)!.bounds_wgs84!] : []));
      if (bounds.length) mapRef.current?.fit(union(bounds));
    },
    [byRef],
  );

  const setStep = useCallback((ref: string, step: number) => {
    setSteps((s) => ({ ...s, [ref]: step }));
    setVisible((v) => ({ ...v, [ref]: true }));
  }, []);

  useEffect(() => {
    if (!drawing) return;
    const cancel = (e: KeyboardEvent) => e.key === "Escape" && setDrawing(false);
    window.addEventListener("keydown", cancel);
    return () => window.removeEventListener("keydown", cancel);
  }, [drawing]);

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
      const click = ++picked.current;
      setReading(true);
      setValues(null);
      setPointError(null);
      try {
        const found = await api.point(target, lon, lat);
        if (click === picked.current) setValues(found);
      } catch (e) {
        if (click === picked.current) setPointError(message(e));
      } finally {
        if (click === picked.current) setReading(false);
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
  const chosenModel = findModel(providers, model);
  const unset = chosenModel && !chosenModel.provider.configured ? chosenModel.provider : null;
  const inspected = values ? shown[values.data_ref] : undefined;
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
          outsideTiles={outsideTiles}
          footprints={footprints}
          area={area}
          drawing={drawing}
          onArea={(bounds) => {
            setArea(bounds);
            setDrawing(false);
            setTab("agent");
          }}
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
          <button
            type="button"
            className={`ghost${drawing ? " active" : ""}`}
            aria-label={drawing ? "Stop drawing (Esc)" : "Draw an area to ask about"}
            aria-pressed={drawing}
            title={drawing ? "Drag a box on the map; Esc stops" : "Draw an area to ask about"}
            onClick={() => setDrawing((d) => !d)}
          >
            <Icon name="area" size={16} />
          </button>
        </div>
        {drawing && <div className="overlay hint-chip glass" role="status">Drag a box on the map to choose an area. Esc cancels.</div>}
        {focus && <FocusChip focus={focus} onClose={() => setFocus(null)} />}
        <Inspector
          key={`${point?.join(",")}`}
          values={values}
          layer={values ? byRef.get(values.data_ref) ?? null : chosen}
          error={pointError}
          reading={reading}
          step={inspected?.step ?? null}
          band={inspected?.bands?.length === 1 ? inspected.bands[0] : null}
          onStep={(step) => values && setStep(values.data_ref, step)}
          onClose={() => { picked.current++; setPoint(null); setValues(null); setPointError(null); setReading(false); }}
        />
        {chosen && visible[chosen.data_ref] && shown[chosen.data_ref] && (
          <Legend layer={chosen} display={shown[chosen.data_ref]} onStep={(step) => setStep(chosen.data_ref, step)} />
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
              {t === "code" && pendingReview && <span className="tab-dot" aria-label="waiting for your review" />}
            </button>
          ))}
          <span className="spacer" />
          <ModelPicker
            providers={providers}
            value={model}
            error={modelsError}
            open={pickerOpen}
            onOpenChange={openPicker}
            onChange={chooseModel}
            onSaveKey={saveKey}
            onForgetKey={forgetKey}
          />
        </div>
        <AgentPanel
          open={tab === "agent"}
          model={model}
          modelLabel={chosenModel?.model.label ?? model}
          engine={chosenModel?.provider.engine ?? "claude"}
          setUp={unset}
          onSetUp={() => openPicker(true)}
          suggestions={suggestions}
          layers={layers}
          byRef={byRef}
          selected={chosen}
          shownTime={chosen ? shown[chosen.data_ref]?.time ?? null : null}
          point={point}
          area={area}
          scripts={scripts.map((s) => s.path)}
          openScript={openScript}
          review={reviewMode}
          onReviewMode={chooseReview}
          onProposal={setPendingReview}
          onDecision={(id) => setPendingReview((r) => (r?.id === id ? null : r))}
          onTurnEnd={() => setPendingReview(null)}
          onDecide={(proposal, accepted, note) => void decide(proposal, accepted, note)}
          onShowReview={() => setTab("code")}
          onScriptTouched={(path) => setScriptRequest({ path, auto: true, key: Date.now() })}
          onClearArea={() => setArea(null)}
          onRef={selectRef}
          onFocus={showFocus}
        />
        <CodePanel
          open={tab === "code"}
          layers={layers}
          byRef={byRef}
          files={scripts}
          filesVersion={scriptsVersion}
          request={scriptRequest}
          review={pendingReview}
          onOpen={setOpenScript}
          onDecide={(proposal, accepted, note) => void decide(proposal, accepted, note)}
          onRef={selectRef}
          onFocus={showFocus}
        />
        <LineagePanel
          open={tab === "lineage"}
          selected={selected}
          version={version}
          byRef={byRef}
          onRef={selectRef}
          onOpenScript={(script) => { setScriptRequest({ ...script, key: Date.now() }); setTab("code"); }}
          onFocus={showFocus}
        />
      </aside>
    </div>
  );
}
