import { type ReactNode, useEffect, useRef, useState } from "react";
import { refsIn, streamAgent } from "../api";
import { Icon } from "../icons";
import type { AgentEvent, CheckStatus, LayerInfo } from "../types";
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
  selected: LayerInfo | null;
  point: [number, number] | null;
  onRef: (ref: string) => void;
}

const SUGGESTIONS = [
  "Find Sentinel-2 scenes of Sydney Harbour from June 2024 with under 20% cloud",
  "Describe the selected layer and check it",
  "How was the selected layer made?",
];

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
      const firstLine = String(input.code ?? "")
        .split("\n")
        .find((l) => l.trim() && !l.startsWith("import") && !l.startsWith("from"));
      return input.path ? base(input.path) ?? "" : firstLine ?? "script";
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

export function AgentPanel({ open, model, selected, point, onRef }: Props) {
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
    const context: string[] = [];
    if (attach && selected) context.push(`Selected on the map: ${selected.name} (${selected.data_ref}).`);
    if (attach && point) context.push(`Point clicked on the map: lon ${point[0].toFixed(5)}, lat ${point[1].toFixed(5)}.`);
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
              {SUGGESTIONS.map((s) => (
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
            placeholder="Ask about this map, or click a place first"
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
