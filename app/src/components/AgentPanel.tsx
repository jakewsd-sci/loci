import { type ReactNode, useEffect, useRef, useState } from "react";
import { refsIn, streamAgent } from "../api";
import { Icon } from "../icons";
import type { AgentEvent, Bounds, CheckStatus, LayerInfo, MapFocus, ValidationReport } from "../types";
import { focusFor } from "./Checks";
import { StatusBadge } from "./LayersPanel";

type Item =
  | { kind: "user"; text: string }
  | { kind: "text"; text: string }
  | {
      kind: "step";
      id: string;
      name: string;
      input: Record<string, unknown>;
      state: "running" | "ok" | "error";
      result: string;
      checks: { data_ref: string; status: CheckStatus }[];
    }
  | { kind: "done"; turns: number; cost: number | null; seconds: number }
  | { kind: "error"; message: string };

interface Props {
  open: boolean;
  model: string;
  layers: LayerInfo[];
  byRef: Map<string, LayerInfo>;
  selected: LayerInfo | null;
  shownTime: string | null; // the date the map shows for the selected layer
  point: [number, number] | null;
  area: Bounds | null;
  onClearArea: () => void;
  onRef: (ref: string) => void;
  onFocus: (focus: MapFocus) => void;
}

const SUGGESTIONS = [
  "Find Sentinel-2 scenes of Sydney Harbour from June 2024 with under 20% cloud",
  "Describe the selected layer and check it",
  "How was the selected layer made?",
];

const AREA_SUGGESTIONS = [
  "Find the clearest Sentinel-2 scenes of the drawn area from the last three months",
  "How green was the drawn area this year? Give the median NDVI and how far to trust it",
];

const today = () => new Date().toISOString().slice(0, 10);
const round = (v: number) => Number(v.toFixed(5));

/** The run's comparison of its inputs, from the tool result's JSON, if it made one. */
function inputChecks(name: string, result: string): ValidationReport | null {
  if (name !== "run") return null;
  try {
    return (JSON.parse(result) as { input_checks?: ValidationReport }).input_checks ?? null;
  } catch {
    return null;
  }
}

/** Width x height of an area in kilometres, roughly, for its chip. */
function areaSize([w, s, e, n]: Bounds): string {
  const km = 111.32;
  const width = (e - w) * km * Math.cos((((s + n) / 2) * Math.PI) / 180);
  return `${width.toFixed(width < 10 ? 1 : 0)} × ${((n - s) * km).toFixed((n - s) * km < 10 ? 1 : 0)} km`;
}

const base = (value: unknown) => String(value ?? "").split("/").pop();

/** One line saying what a tool call does, from its arguments. */
function summarise(name: string, input: Record<string, unknown>): string {
  switch (name) {
    case "stac_search":
      return `${input.collection ?? "sentinel-2-l2a"} · ${input.start} to ${input.end}`;
    case "load": {
      const bands = Array.isArray(input.bands) ? ` · ${(input.bands as string[]).join(", ")}` : "";
      return `${base(input.uri)}${bands}`;
    }
    case "run": {
      const lines = String(input.code ?? "").split("\n").filter((l) => l.trim()).length;
      const names = Object.keys((input.inputs as Record<string, string> | undefined) ?? {});
      const what = input.path ? base(input.path) ?? "" : `inline script, ${lines} lines`;
      return names.length ? `${what} · inputs ${names.join(", ")}` : what;
    }
    case "validate":
      return (input.data_refs as string[] | undefined)?.join(", ") ?? "";
    case "lineage":
      return String(input.target ?? "");
    default:
      return String(input.data_ref ?? JSON.stringify(input)).slice(0, 80);
  }
}

/** Light markdown: paragraphs, bullet lists, **bold**, `code`, and data_refs as links. */
export function Rich({ text, onRef }: { text: string; onRef: (ref: string) => void }) {
  const inline = (line: string): ReactNode[] =>
    line.split(/(\*\*[^*]+\*\*|`[^`]+`|\b(?:raster|vector)-[0-9a-f]{8}\b)/g).map((part, i) => {
      if (/^(?:raster|vector)-[0-9a-f]{8}$/.test(part))
        return (
          <button key={i} type="button" className="ref-link" onClick={() => onRef(part)}>
            {part}
          </button>
        );
      if (part.startsWith("**") && part.endsWith("**"))
        return <strong key={i} style={{ color: "var(--text)", fontWeight: 600 }}>{part.slice(2, -2)}</strong>;
      if (part.startsWith("`") && part.endsWith("`") && part.length > 1) return <code key={i}>{part.slice(1, -1)}</code>;
      return part;
    });
  return (
    <div className="reply">
      {text.split(/\n{2,}/).map((block, b) => {
        const lines = block.split("\n").filter((l) => l.trim());
        if (lines.length > 1 && lines.every((l) => /^\s*\|.*\|\s*$/.test(l))) {
          const cells = (l: string) => l.trim().slice(1, -1).split("|").map((c) => c.trim());
          const [head, ...rows] = lines.filter((l) => !/^\s*\|[\s:|-]+\|\s*$/.test(l)).map(cells);
          return (
            <table key={b} className="reply-table">
              <thead>
                <tr>{head.map((c, i) => <th key={i}>{inline(c)}</th>)}</tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i}>{r.map((c, j) => <td key={j}>{inline(c)}</td>)}</tr>
                ))}
              </tbody>
            </table>
          );
        }
        if (lines.length && lines.every((l) => /^\s*[-*] /.test(l)))
          return (
            <ul key={b}>
              {lines.map((l, i) => (
                <li key={i}>{inline(l.replace(/^\s*[-*] /, ""))}</li>
              ))}
            </ul>
          );
        return (
          <p key={b}>
            {lines.map((l, i) => (
              <span key={i}>
                {i > 0 && <br />}
                {inline(l.replace(/^#+\s*/, ""))}
              </span>
            ))}
          </p>
        );
      })}
    </div>
  );
}

function apply(items: Item[], event: AgentEvent): Item[] {
  switch (event.type) {
    case "text":
      return [...items, { kind: "text", text: event.text }];
    case "tool":
      return [
        ...items,
        { kind: "step", id: event.id, name: event.name, input: event.input, state: "running", result: "", checks: [] },
      ];
    case "tool_result":
      return items.map((it) =>
        it.kind === "step" && it.id === event.id
          ? { ...it, state: event.is_error ? "error" : "ok", result: event.text }
          : it,
      );
    case "check":
      return items.map((it) =>
        it.kind === "step" && it.id === event.tool_use_id
          ? { ...it, checks: [...it.checks.filter((c) => c.data_ref !== event.data_ref), event] }
          : it,
      );
    case "done":
      return [...items, { kind: "done", turns: event.turns, cost: event.cost_usd, seconds: event.seconds }];
    case "error":
      return [...items, { kind: "error", message: event.message }];
    default:
      return items;
  }
}

const MAX_LISTED = 25;

/** What the map shows, so the agent can refer to layers without searching for them. */
function mapContext(layers: LayerInfo[]): string {
  if (!layers.length) return "The map has no layers yet.";
  const listed = layers
    .slice(0, MAX_LISTED)
    .map((l) => `${l.name} (${l.data_ref}, ${l.validation ?? "not checked"})`)
    .join("; ");
  const more = layers.length > MAX_LISTED ? `; and ${layers.length - MAX_LISTED} more` : "";
  return `Layers on the map, newest first: ${listed}${more}.`;
}

export function AgentPanel({
  open,
  model,
  layers,
  byRef,
  selected,
  shownTime,
  point,
  area,
  onClearArea,
  onRef,
  onFocus,
}: Props) {
  const [items, setItems] = useState<Item[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [attach, setAttach] = useState(true);
  const session = useRef<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [items]);

  async function send(text: string) {
    const message = text.trim();
    if (!message || busy) return;
    const context: string[] = [`Today is ${today()}.`, mapContext(layers)];
    if (attach && selected) {
      const when = shownTime ? `, showing ${shownTime.slice(0, 10)}` : "";
      context.push(`Selected on the map: ${selected.name} (${selected.data_ref}${when}).`);
    }
    if (attach && point) context.push(`Point clicked on the map: lon ${point[0].toFixed(5)}, lat ${point[1].toFixed(5)}.`);
    if (area) {
      const bbox = area.map(round).join(", ");
      context.push(`Area drawn on the map, as a bbox in lon/lat (WGS 84): [${bbox}].`);
    }
    setItems((it) => [...it, { kind: "user", text: message }]);
    setDraft("");
    setBusy(true);
    abort.current = new AbortController();
    try {
      const body = { message: [...context, message].join("\n"), model, session_id: session.current };
      for await (const event of streamAgent(body, abort.current.signal)) {
        if (event.type === "session" || event.type === "done") session.current = event.session_id;
        setItems((it) => apply(it, event));
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") setItems((it) => apply(it, { type: "error", message: String(e) }));
    } finally {
      setBusy(false);
      abort.current = null;
    }
  }

  return (
    <div className={`panel${open ? " open" : ""}`} role="tabpanel" aria-label="Agent">
      <div className="conversation" aria-live="polite">
        {items.length === 0 && (
          <div className="reply">
            <p style={{ color: "var(--muted)" }}>
              Ask a question about a place. The agent works through loci&apos;s tools; every layer it
              makes appears on the map, checked automatically.
            </p>
            <div className="suggestions">
              {(area ? [...AREA_SUGGESTIONS, ...SUGGESTIONS.slice(1)] : SUGGESTIONS).map((s) => (
                <button key={s} type="button" className="chip" onClick={() => send(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {items.map((item, i) => {
          switch (item.kind) {
            case "user":
              return <div key={i} className="bubble">{item.text}</div>;
            case "text":
              return <Rich key={i} text={item.text} onRef={onRef} />;
            case "step":
              return (
                <div key={i} className="step">
                  <div className="top">
                    <code className="tool">{item.name}</code>
                    <span className="what">{summarise(item.name, item.input)}</span>
                    {item.state === "running" && <span className="spinner" aria-label="Running" />}
                    {item.state === "error" && <span className="badge fail">Error</span>}
                    {item.checks.map((c) => (
                      <StatusBadge key={c.data_ref} status={c.status} />
                    ))}
                  </div>
                  {(() => {
                    const report = item.state === "ok" ? inputChecks(item.name, item.result) : null;
                    if (!report) return null;
                    const focus = report.checks.map((c) => focusFor(c, byRef)).find((f) => f !== null);
                    return (
                      <div className="inputs-checked">
                        <span>Inputs checked together</span>
                        <StatusBadge status={report.status} />
                        {focus && (
                          <button type="button" className="ghost show" onClick={() => onFocus(focus)}>
                            <Icon name="target" size={13} /> Show on map
                          </button>
                        )}
                      </div>
                    );
                  })()}
                  {item.state === "ok" && refsIn(item.result).length > 0 && (
                    <div className="refs">
                      {refsIn(item.result)
                        .slice(0, 4)
                        .map((ref) => (
                          <button key={ref} type="button" className="ref-link" onClick={() => onRef(ref)}>
                            {ref}
                          </button>
                        ))}
                    </div>
                  )}
                  {item.result && (
                    <details>
                      <summary>{item.state === "error" ? "What went wrong" : "Result"}</summary>
                      <pre>{item.result}</pre>
                    </details>
                  )}
                </div>
              );
            case "done":
              return (
                <span key={i} className="done-line">
                  {item.turns} turns · {item.seconds.toFixed(0)} s
                  {item.cost != null ? ` · $${item.cost.toFixed(2)}` : ""}
                </span>
              );
            case "error":
              return <span key={i} className="error-line">{item.message}</span>;
          }
        })}
        <div ref={end} />
      </div>
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          void send(draft);
        }}
      >
        <label htmlFor="composer" className="visually-hidden">Message the agent</label>
        <div className="box">
          <textarea
            id="composer"
            rows={2}
            value={draft}
            placeholder="Ask about this map; click a place or draw an area first"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send(draft);
              }
            }}
          />
          <div className="row">
            {attach && selected && (
              <button type="button" className="ghost context" title="Not sent with the message when removed" onClick={() => setAttach(false)}>
                {selected.name} <Icon name="close" size={12} />
              </button>
            )}
            {attach && point && (
              <span className="context mono">
                {point[0].toFixed(4)}, {point[1].toFixed(4)}
              </span>
            )}
            {area && (
              <button type="button" className="ghost context" title="Remove the drawn area" onClick={onClearArea}>
                <Icon name="area" size={12} /> Area {areaSize(area)} <Icon name="close" size={12} />
              </button>
            )}
            {!attach && (
              <button type="button" className="ghost context" onClick={() => setAttach(true)}>
                Attach map selection
              </button>
            )}
            {busy ? (
              <button type="button" className="send" aria-label="Stop" onClick={() => abort.current?.abort()}>
                <span className="spinner" style={{ borderTopColor: "#0a0b0d" }} />
              </button>
            ) : (
              <button type="submit" className="send" aria-label="Send" disabled={!draft.trim()}>
                <Icon name="send" size={16} />
              </button>
            )}
          </div>
        </div>
      </form>
    </div>
  );
}
