import { useState } from "react";
import { Icon } from "../icons";
import type { LayerDisplay, LayerInfo, MapFocus, PointValues, StepInfo } from "../types";

// Plain digits: 300, not toPrecision's 3.0e+2.
const fmt = (v: number | null | undefined, digits = 3) =>
  v == null || Number.isNaN(v) ? "–" : Math.abs(v) >= 1000 ? v.toFixed(0) : String(Number(v.toPrecision(digits)));

const fmtLonLat = (lon: number, lat: number) =>
  `${Math.abs(lon).toFixed(4)}° ${lon >= 0 ? "E" : "W"}, ${Math.abs(lat).toFixed(4)}° ${lat >= 0 ? "N" : "S"}`;

const day = (iso: string) => iso.slice(0, 10);
const shortDay = (iso: string, withYear = false) =>
  new Date(`${day(iso)}T00:00:00Z`).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
    ...(withYear ? { year: "numeric" } : {}),
  });
/** Whether dates across these steps fall in more than one year, so labels need the year. */
const spansYears = (steps: { time: string }[]) =>
  steps.length > 0 && steps[0].time.slice(0, 4) !== steps[steps.length - 1].time.slice(0, 4);

/** What hides the ground at a step, in words; empty when nothing is known. */
function sky(step: StepInfo): string {
  const parts = [];
  if (step.scene_class) parts.push(step.scene_class);
  if (step.cloud_cover != null) parts.push(`scene ${step.cloud_cover.toFixed(0)}% cloud`);
  return parts.join(" · ");
}

export function Inspector({
  values,
  layer,
  error,
  reading,
  step,
  band,
  onStep,
  onClose,
}: {
  values: PointValues | null;
  layer: LayerInfo | null;
  error: string | null;
  reading: boolean;
  step: number | null; // the time step the map shows for this layer, if any
  band: string | null; // the band the map shows, charted through time
  onStep: (step: number) => void;
  onClose: () => void;
}) {
  if (!values && !error && !reading) return null;
  const steps = values?.steps ?? [];
  const charted =
    values?.bands.find((b) => b.name === band) ?? values?.bands.find((b) => b.name !== "scl") ?? values?.bands[0];
  const at = step ?? Math.max(0, steps.length - 1);
  return (
    <section className="overlay inspector glass" aria-label="Values at the point">
      <div className="head">
        <span className="label">{layer?.name ?? "Point"}</span>
        <button type="button" className="ghost" aria-label="Close" onClick={onClose} style={{ padding: 2, color: "var(--muted)" }}>
          <Icon name="close" size={14} />
        </button>
      </div>
      {reading && (
        <span style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--muted)", fontSize: 12 }}>
          <span className="spinner" /> Reading values from the data…
        </span>
      )}
      {error && <span className="error-line">{error}</span>}
      {values && (
        <>
          <span className="mono" style={{ fontSize: 11, color: "var(--muted)" }}>
            {fmtLonLat(values.lon, values.lat)}
          </span>
          {charted && (
            <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
              <span className="big">{fmt(charted.values[at])}</span>
              <span style={{ color: "var(--muted)" }}>
                {charted.name}
                {steps.length > 0 ? ` · ${shortDay(steps[at].time, spansYears(steps))}` : ""}
              </span>
            </div>
          )}
          {charted && steps.length > 1 && (
            <TimeSeries name={charted.name} values={charted.values} steps={steps} at={at} onStep={onStep} />
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

const W = 232;
const H = 76;
const LEFT = 34; // room for the value ticks
const PAD = 6;

/** One band through time at a point. Steps where the ground was hidden are hollow and left out
 * of the line, never dropped: their values stay readable. Clicking a date shows it on the map. */
function TimeSeries({
  name,
  values,
  steps,
  at,
  onStep,
}: {
  name: string;
  values: (number | null)[];
  steps: StepInfo[];
  at: number;
  onStep: (step: number) => void;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const times = steps.map((s) => Date.parse(`${s.time}Z`));
  const present = values.filter((v): v is number => v != null);
  let lo = Math.min(...present);
  let hi = Math.max(...present);
  if (!present.length) [lo, hi] = [0, 1];
  if (hi - lo < 1e-9) [lo, hi] = [lo - 0.1, hi + 0.1];
  const t0 = times[0];
  const span = Math.max(times[times.length - 1] - t0, 1);
  const x = (i: number) => LEFT + PAD + ((times[i] - t0) / span) * (W - LEFT - 2 * PAD);
  const y = (v: number) => PAD + (1 - (v - lo) / (hi - lo)) * (H - 2 * PAD);
  const clear = values.map((v, i) => (v != null && !steps[i].obscured ? i : -1)).filter((i) => i >= 0);
  const line = clear.map((i, k) => `${k ? "L" : "M"}${x(i).toFixed(1)},${y(values[i] as number).toFixed(1)}`).join("");
  const nearest = (px: number) =>
    times.reduce((best, _, i) => (Math.abs(x(i) - px) < Math.abs(x(best) - px) ? i : best), 0);
  const shown = hover ?? at;
  const hidden = steps.some((s) => s.obscured);

  return (
    <div className="series">
      <svg
        width={W}
        height={H}
        role="slider"
        tabIndex={0}
        aria-label={`${name} through time; arrow keys pick the date shown on the map`}
        aria-valuemin={0}
        aria-valuemax={steps.length - 1}
        aria-valuenow={at}
        aria-valuetext={`${day(steps[at].time)}: ${fmt(values[at])}`}
        onPointerMove={(e) => setHover(nearest(e.clientX - e.currentTarget.getBoundingClientRect().left))}
        onPointerLeave={() => setHover(null)}
        onClick={(e) => onStep(nearest(e.clientX - e.currentTarget.getBoundingClientRect().left))}
        onKeyDown={(e) => {
          if (e.key === "ArrowLeft" && at > 0) onStep(at - 1);
          if (e.key === "ArrowRight" && at < steps.length - 1) onStep(at + 1);
        }}
      >
        <line className="grid" x1={LEFT} x2={W} y1={y(hi)} y2={y(hi)} />
        <line className="grid" x1={LEFT} x2={W} y1={y(lo)} y2={y(lo)} />
        <text className="tick" x={LEFT - 6} y={y(hi) + 3} textAnchor="end">{fmt(hi, 2)}</text>
        <text className="tick" x={LEFT - 6} y={y(lo) + 3} textAnchor="end">{fmt(lo, 2)}</text>
        <line className="crosshair" x1={x(shown)} x2={x(shown)} y1={0} y2={H} />
        <path className="trace" d={line} />
        {values.map((v, i) =>
          v == null ? (
            <line key={i} className="missing" x1={x(i)} x2={x(i)} y1={H - PAD - 3} y2={H - PAD + 3} />
          ) : (
            <circle
              key={i}
              className={`${steps[i].obscured ? "hidden-ground" : "dot"}${i === at ? " current" : ""}`}
              cx={x(i)}
              cy={y(v)}
              r={i === at ? 5 : 4}
            />
          ),
        )}
      </svg>
      <div className="axis">
        <span>{shortDay(steps[0].time, spansYears(steps))}</span>
        <span>{shortDay(steps[steps.length - 1].time, spansYears(steps))}</span>
      </div>
      <span className="readout-line">
        <strong>{fmt(values[shown])}</strong> {shortDay(steps[shown].time, spansYears(steps))}
        {sky(steps[shown]) ? ` · ${sky(steps[shown])}` : ""}
      </span>
      {hidden && (
        <span className="key">
          <svg width="10" height="10" aria-hidden="true">
            <circle className="hidden-ground" cx="5" cy="5" r="3.5" />
          </svg>
          Ground hidden here (scene classification); not joined to the line.
        </span>
      )}
      <details>
        <summary>Table</summary>
        <table>
          <thead>
            <tr>
              <th>Date</th>
              <th>{name}</th>
              <th>Sky</th>
            </tr>
          </thead>
          <tbody>
            {steps.map((s, i) => (
              <tr key={s.time}>
                <td>{day(s.time)}</td>
                <td>{fmt(values[i])}</td>
                <td>{sky(s) || "–"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}

export function Legend({
  layer,
  display,
  onStep,
}: {
  layer: LayerInfo;
  display: LayerDisplay;
  onStep: (step: number) => void;
}) {
  if (display.mode === "geojson") return null;
  const [lo, hi] = display.stretch ?? [null, null];
  const step = display.step;
  const count = display.times.length;
  return (
    <div className="overlay legend glass">
      <span style={{ fontWeight: 500 }}>{layer.name}</span>
      {step != null && count > 1 && (
        <div className="dates">
          <button type="button" className="ghost" aria-label="Earlier date" disabled={step === 0} onClick={() => onStep(step - 1)}>
            <Icon name="prev" size={14} />
          </button>
          <label htmlFor="date" className="visually-hidden">Date shown</label>
          <input
            id="date"
            type="range"
            min={0}
            max={count - 1}
            value={step}
            onChange={(e) => onStep(Number(e.target.value))}
            list="date-ticks"
          />
          <datalist id="date-ticks">
            {display.times.map((t, i) => (
              <option key={t} value={i} label={day(t)} />
            ))}
          </datalist>
          <button type="button" className="ghost" aria-label="Later date" disabled={step === count - 1} onClick={() => onStep(step + 1)}>
            <Icon name="next" size={14} />
          </button>
          <span className="mono when">{day(display.times[step])}</span>
        </div>
      )}
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
      {count > 1 && <span className="note">One colour scale for every date, so dates compare.</span>}
      {display.note && <span className="note">{display.note}</span>}
    </div>
  );
}

export function FocusChip({ focus, onClose }: { focus: MapFocus; onClose: () => void }) {
  return (
    <div className="overlay focus-chip glass" role="status">
      <span aria-hidden="true" className={`focus-key ${focus.kind}`} />
      <span>{focus.label}</span>
      <button type="button" className="ghost" aria-label="Stop showing this" onClick={onClose}>
        <Icon name="close" size={12} />
      </button>
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
