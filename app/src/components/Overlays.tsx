import { useState } from "react";
import { Icon } from "../icons";
import type { LayerDisplay, LayerInfo, PointValues } from "../types";

const fmt = (v: number | null | undefined, digits = 3) =>
  v == null || Number.isNaN(v) ? "–" : Math.abs(v) >= 1000 ? v.toFixed(0) : v.toPrecision(digits);

const fmtLonLat = (lon: number, lat: number) =>
  `${Math.abs(lon).toFixed(4)}° ${lon >= 0 ? "E" : "W"}, ${Math.abs(lat).toFixed(4)}° ${lat >= 0 ? "N" : "S"}`;

export function Inspector({
  values,
  layer,
  error,
  onClose,
}: {
  values: PointValues | null;
  layer: LayerInfo | null;
  error: string | null;
  onClose: () => void;
}) {
  const [step, setStep] = useState<number | null>(null);
  if (!values && !error) return null;
  const times = values?.times ?? [];
  const at = step ?? Math.max(0, times.length - 1);
  const first = values?.bands[0];
  return (
    <section className="overlay inspector glass" aria-label="Values at the point">
      <div className="head">
        <span className="label">{layer?.name ?? "Point"}</span>
        <button type="button" className="ghost" aria-label="Close" onClick={onClose} style={{ padding: 2, color: "var(--muted)" }}>
          <Icon name="close" size={14} />
        </button>
      </div>
      {error && <span className="error-line">{error}</span>}
      {values && (
        <>
          <span className="mono" style={{ fontSize: 11, color: "var(--muted)" }}>
            {fmtLonLat(values.lon, values.lat)}
          </span>
          {first && (
            <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
              <span className="big">{fmt(first.values[at])}</span>
              <span style={{ color: "var(--muted)" }}>
                {first.name}
                {times.length > 1 ? ` · ${times[at].slice(0, 10)}` : ""}
              </span>
            </div>
          )}
          {first && times.length > 1 && (
            <div className="bars" role="group" aria-label="Values through time">
              {first.values.map((v, i) => {
                const all = first.values.filter((x): x is number => x != null);
                const lo = Math.min(0, ...all);
                const hi = Math.max(...all, lo + 1e-9);
                const height = v == null ? 2 : Math.max(4, Math.round(((v - lo) / (hi - lo)) * 44));
                return (
                  <button
                    key={times[i]}
                    type="button"
                    className={`ghost bar${i === at ? " active" : ""}`}
                    aria-label={`${first.name} ${fmt(v)} on ${times[i].slice(0, 10)}`}
                    onClick={() => setStep(i)}
                  >
                    <span style={{ height }} />
                    <span>{times[i].slice(8, 10)}</span>
                  </button>
                );
              })}
            </div>
          )}
          {values.bands.length > 1 && (
            <dl>
              {values.bands.map((b) => (
                <div key={b.name} style={{ display: "contents" }}>
                  <dt>{b.name}</dt>
                  <dd>{fmt(b.values[at])}</dd>
                </div>
              ))}
            </dl>
          )}
          {values.features.length > 0 && (
            <dl>
              {Object.entries(values.features[0]).map(([k, v]) => (
                <div key={k} style={{ display: "contents" }}>
                  <dt>{k}</dt>
                  <dd>{v}</dd>
                </div>
              ))}
            </dl>
          )}
          {!values.bands.length && !values.features.length && (
            <span style={{ color: "var(--muted)" }}>No feature here.</span>
          )}
          <span className="foot">Read from the data in {values.crs}, not from the drawn map.</span>
        </>
      )}
    </section>
  );
}

export function Legend({ layer, display }: { layer: LayerInfo; display: LayerDisplay }) {
  if (display.mode === "geojson") return null;
  const [lo, hi] = display.stretch ?? [null, null];
  return (
    <div className="overlay legend glass">
      <span style={{ fontWeight: 500 }}>
        {layer.name}
        {display.time ? ` · ${display.time.slice(0, 10)}` : ""}
      </span>
      {display.colormap ? (
        <>
          <span aria-hidden="true" className="ramp" />
          <div className="ticks">
            <span>{fmt(lo)}</span>
            <span>{display.bands?.[0]}</span>
            <span>{fmt(hi)}</span>
          </div>
        </>
      ) : (
        <span style={{ color: "var(--muted)", fontSize: 12 }}>
          {display.bands?.join(", ")} as red, green, blue · {fmt(lo)} to {fmt(hi)}
        </span>
      )}
      {display.note && <span className="note">{display.note}</span>}
    </div>
  );
}

export function Readout({
  cursor,
  crs,
  scaleRef,
}: {
  cursor: [number, number] | null;
  crs: string | null;
  scaleRef: (el: HTMLDivElement | null) => void;
}) {
  return (
    <div className="overlay readout glass" style={{ right: 56 }}>
      <div className="scale-slot" ref={scaleRef} />
      <span className="divider" />
      <span style={{ display: "flex", flexDirection: "column" }}>
        <span className="mono">{cursor ? fmtLonLat(cursor[0], cursor[1]) : "–"}</span>
        <span>
          {crs ? `Data ${crs} · ` : ""}Display Web Mercator
        </span>
      </span>
    </div>
  );
}
