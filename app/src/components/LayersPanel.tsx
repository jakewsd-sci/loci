import { useEffect, useRef, useState } from "react";
import { Icon } from "../icons";
import type { CheckStatus, DataFile, ExportResult, LayerDisplay, LayerInfo } from "../types";

interface Props {
  project: string;
  layers: LayerInfo[];
  files: DataFile[]; // the project's readable files
  moreFiles: boolean;
  exportStep: number | null; // the time step the map shows for the selected layer
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
  onAddFile: (path: string) => void;
  onUpload: (files: File[]) => void;
  onExport: (ref: string, format: string, step: number | null) => Promise<ExportResult>;
}

const EXPORTS: Record<LayerInfo["kind"], { format: string; label: string }[]> = {
  raster: [{ format: "geotiff", label: "GeoTIFF (.tif)" }],
  vector: [
    { format: "gpkg", label: "GeoPackage (.gpkg)" },
    { format: "geojson", label: "GeoJSON (.geojson)" },
    { format: "csv", label: "CSV (.csv)" },
  ],
};

const size = (bytes: number) =>
  bytes < 1024 ** 2 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 ** 2).toFixed(1)} MB`;

/** A file's name and the folder it is in, for one row of the list. */
function splitPath(path: string): { name: string; folder: string } {
  const at = path.lastIndexOf("/");
  return at < 0 ? { name: path, folder: "" } : { name: path.slice(at + 1), folder: path.slice(0, at) };
}

/** Export the selected layer to exports/, then offer the file and say what the format left out. */
function ExportSetting({ layer, step, onExport }: { layer: LayerInfo; step: number | null; onExport: Props["onExport"] }) {
  const choices = EXPORTS[layer.kind];
  const [format, setFormat] = useState(choices[0].format);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<ExportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const stack = layer.kind === "raster" && layer.times > 1;

  useEffect(() => {
    setFormat(EXPORTS[layer.kind][0].format);
    setDone(null);
    setError(null);
  }, [layer.data_ref, layer.kind]);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      setDone(await onExport(layer.data_ref, format, stack ? (step ?? 0) : null));
    } catch (e) {
      setDone(null);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="setting export">
      <label className="setting-head" htmlFor="export-format">
        <span>Export{stack ? " the date shown" : ""}</span>
      </label>
      <div className="export-row">
        <select id="export-format" value={format} onChange={(e) => setFormat(e.target.value)}>
          {choices.map((c) => (
            <option key={c.format} value={c.format}>{c.label}</option>
          ))}
        </select>
        <button type="button" className="secondary" disabled={busy} onClick={() => void run()}>
          {busy ? "Exporting" : "Export"}
        </button>
      </div>
      {done && (
        <div className="export-done">
          <span>
            Saved as exports/
            <a href={`/api/exports/${encodeURIComponent(done.download)}`} download>
              {done.download}
            </a>{" "}
            · {size(done.bytes)}
          </span>
          {done.notes.map((note) => (
            <span key={note} className="note">{note}</span>
          ))}
        </div>
      )}
      {error && <p className="export-done error">{error}</p>}
    </div>
  );
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
  const picker = useRef<HTMLInputElement>(null);
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
        <section className="layer-group files" aria-label="Files">
          <div className="group-head">
            <h2 className="label">Files</h2>
            <button type="button" className="ghost add-files" onClick={() => picker.current?.click()}>
              <Icon name="plus" size={14} /> Add files
            </button>
            <input
              ref={picker}
              type="file"
              multiple
              hidden
              onChange={(e) => {
                if (e.target.files?.length) props.onUpload([...e.target.files]);
                e.target.value = "";
              }}
            />
          </div>
          {props.files.length === 0 && (
            <p className="files-empty">
              Drop files on the map, or add GeoTIFF, GeoPackage, GeoJSON, shapefile or CSV files to the
              project folder.
            </p>
          )}
          {props.files.map((file) => {
            const { name, folder } = splitPath(file.path);
            const shown = file.data_ref;
            return (
              <div key={file.path} className={`layer-row file-row${shown && shown === selected ? " selected" : ""}`}>
                <span aria-hidden="true" className={`swatch ${file.kind === "vector" ? "vector" : "file"}`} />
                <span className="names">
                  <span className="name" title={file.path}>{name}</span>
                  <span className="ref">{folder ? `${folder} · ` : ""}{size(file.size)}</span>
                </span>
                {shown ? (
                  <button type="button" className="ghost on-map" onClick={() => props.onSelect(shown)}>
                    On map
                  </button>
                ) : (
                  <button
                    type="button"
                    className="ghost add"
                    aria-label={`Add ${name} to the map`}
                    title="Add to the map"
                    onClick={() => props.onAddFile(file.path)}
                  >
                    <Icon name="plus" size={14} />
                  </button>
                )}
              </div>
            );
          })}
          {props.moreFiles && (
            <p className="files-empty">More files than are listed: open a smaller folder to see them all.</p>
          )}
        </section>
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
      {chosen && (
        <div className="layer-settings">
          {chosen.drawable && (
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
          )}
          {chosen.drawable && chosen.kind === "raster" && chosen.bands.length > 1 && (
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
          <ExportSetting layer={chosen} step={props.exportStep} onExport={props.onExport} />
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
