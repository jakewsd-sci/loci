import { Icon } from "../icons";
import type { CheckStatus, LayerDisplay, LayerInfo } from "../types";

interface Props {
  project: string;
  layers: LayerInfo[];
  visible: Record<string, boolean>;
  opacity: Record<string, number>;
  displays: Record<string, LayerDisplay>;
  selected: string | null;
  basemapVisible: boolean;
  onToggle: (ref: string) => void;
  onSelect: (ref: string) => void;
  onFit: (ref: string) => void;
  onOpacity: (ref: string, value: number) => void;
  onBands: (ref: string, bands: string[]) => void;
  onToggleBasemap: () => void;
}

const GROUPS: { title: string; test: (l: LayerInfo) => boolean }[] = [
  { title: "Results", test: (l) => l.produced_by === "run" },
  { title: "Inputs", test: (l) => l.produced_by !== "run" && l.produced_by !== "stac_search" },
  { title: "Searches", test: (l) => l.produced_by === "stac_search" },
];

export function StatusBadge({ status }: { status: CheckStatus | null }) {
  const label = status ? status[0].toUpperCase() + status.slice(1) : "Not checked";
  return <span className={`badge ${status ?? "none"}`}>{label}</span>;
}

function swatchClass(layer: LayerInfo, display?: LayerDisplay): string {
  if (layer.kind === "vector") return "swatch vector";
  const bands = display?.bands ?? layer.bands;
  return bands.length >= 3 && display?.colormap == null && display ? "swatch rgb" : "swatch viridis";
}

/** Band choices for a raster: true colour when it has red, green and blue, then each band. */
function bandChoices(layer: LayerInfo): { label: string; bands: string[] }[] {
  const choices = layer.bands.map((b) => ({ label: b, bands: [b] }));
  const rgb = ["red", "green", "blue"];
  if (rgb.every((b) => layer.bands.includes(b))) choices.unshift({ label: "True colour", bands: rgb });
  return choices;
}

export function LayersPanel(props: Props) {
  const { layers, selected, displays } = props;
  const chosen = layers.find((l) => l.data_ref === selected) ?? null;
  const counts = { pass: 0, warn: 0, fail: 0, none: 0 };
  for (const l of layers) counts[l.validation ?? "none"] += 1;

  return (
    <aside className="layers" aria-label="Layers">
      <header>
        <span className="label">Project</span>
        <h1 title={props.project}>{props.project.split("/").filter(Boolean).pop() ?? "Workspace"}</h1>
        <span className="sub">{layers.length} {layers.length === 1 ? "layer" : "layers"}</span>
      </header>
      <div className="layer-groups">
        {GROUPS.map(({ title, test }) => {
          const members = layers.filter(test);
          if (!members.length) return null;
          return (
            <section key={title} className="layer-group" aria-label={title}>
              <h2 className="label">{title}</h2>
              {members.map((layer) => (
                <div
                  key={layer.data_ref}
                  className={`layer-row${layer.data_ref === selected ? " selected" : ""}`}
                >
                  <button
                    type="button"
                    className="ghost toggle"
                    disabled={!layer.drawable}
                    title={layer.note ?? undefined}
                    aria-label={`${props.visible[layer.data_ref] ? "Hide" : "Show"} ${layer.name}`}
                    onClick={() => props.onToggle(layer.data_ref)}
                  >
                    <Icon name={props.visible[layer.data_ref] ? "eye" : "eyeOff"} size={16} />
                  </button>
                  <button
                    type="button"
                    className="ghost pick"
                    onClick={() => props.onSelect(layer.data_ref)}
                    onDoubleClick={() => props.onFit(layer.data_ref)}
                    title="Click to select, double-click to zoom to it"
                  >
                    <span aria-hidden="true" className={swatchClass(layer, displays[layer.data_ref])} />
                    <span className="names">
                      <span className="name">{layer.name}</span>
                      <span className="ref">{layer.data_ref}</span>
                    </span>
                    <StatusBadge status={layer.validation} />
                  </button>
                </div>
              ))}
            </section>
          );
        })}
        <section className="layer-group" aria-label="Basemap">
          <h2 className="label">Basemap</h2>
          <div className="layer-row">
            <button
              type="button"
              className="ghost toggle"
              aria-label={`${props.basemapVisible ? "Hide" : "Show"} basemap`}
              onClick={props.onToggleBasemap}
            >
              <Icon name={props.basemapVisible ? "eye" : "eyeOff"} size={16} />
            </button>
            <span className="pick">
              <span aria-hidden="true" className="swatch" />
              <span className="names">
                <span className="name">Streets, dark</span>
                <span className="ref">OpenStreetMap · OpenFreeMap</span>
              </span>
            </span>
          </div>
        </section>
      </div>
      {chosen && chosen.drawable && (
        <div className="layer-settings">
          <div className="setting">
            <label className="setting-head" htmlFor="opacity">
              <span>Opacity · {chosen.name}</span>
              <span className="mono">{Math.round((props.opacity[chosen.data_ref] ?? 1) * 100)}%</span>
            </label>
            <input
              id="opacity"
              type="range"
              min={0}
              max={100}
              value={Math.round((props.opacity[chosen.data_ref] ?? 1) * 100)}
              onChange={(e) => props.onOpacity(chosen.data_ref, Number(e.target.value) / 100)}
            />
          </div>
          {chosen.kind === "raster" && chosen.bands.length > 1 && (
            <div className="setting">
              <label className="setting-head" htmlFor="bands">
                <span>Bands</span>
              </label>
              <select
                id="bands"
                value={(displays[chosen.data_ref]?.bands ?? []).join(",")}
                onChange={(e) => props.onBands(chosen.data_ref, e.target.value.split(","))}
              >
                {bandChoices(chosen).map((c) => (
                  <option key={c.label} value={c.bands.join(",")}>
                    {c.label}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>
      )}
      <div className="checks-summary">
        <Icon name="shield" size={16} />
        <span>
          {counts.pass} pass · {counts.warn} warn · {counts.fail} fail
          {counts.none ? ` · ${counts.none} not checked` : ""}. New layers are checked automatically.
        </span>
      </div>
    </aside>
  );
}
