import { type ReactNode, useEffect, useRef, useState } from "react";
import { refsIn, streamAgent } from "../api";
import { Icon } from "../icons";
import type {
  AgentEvent,
  Bounds,
  CheckStatus,
  LayerInfo,
  MapFocus,
  Proposal,
  ProviderInfo,
  Review,
  ValidationReport,
} from "../types";
import { focusFor } from "./Checks";
import { StatusBadge } from "./LayersPanel";

interface ReviewState {
  id: string;
  kind: Proposal["kind"];
  state: "waiting" | "accepted" | "rejected";
}

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
      review: ReviewState | null;
    }
  | { kind: "review"; review: ReviewState; path: string } // one no tool call claimed
  | { kind: "done"; model: string; turns: number; cost: number | null; tokens: number | null; seconds: number }
  | { kind: "error"; message: string; hint: string | null }
  | { kind: "divider"; text: string };

interface Props {
  open: boolean;
  model: string; // "<provider>:<model>"
  modelLabel: string;
  engine: ProviderInfo["engine"]; // conversations stay with the engine that began them
  setUp: ProviderInfo | null; // the chosen model's provider, when it still needs a key
  onSetUp: () => void;
  suggestions: string[]; // the project's own first questions, as a demo offers
  layers: LayerInfo[];
  byRef: Map<string, LayerInfo>;
  selected: LayerInfo | null;
  shownTime: string | null; // the date the map shows for the selected layer
  point: [number, number] | null;
  area: Bounds | null;
  scripts: string[]; // paths under analysis/
  openScript: string | null; // the script open in the editor
  review: Review;
  onReviewMode: (review: Review) => void;
  onProposal: (proposal: Proposal) => void;
  onDecision: (id: string) => void;
  onTurnEnd: () => void;
  onDecide: (proposal: Proposal, accepted: boolean, note: string) => void;
  onShowReview: () => void;
  onScriptTouched: (path: string) => void; // the agent wrote or edited this script
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
const thousands = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));
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
      const names = Object.keys((input.inputs as Record<string, string> | undefined) ?? {});
      const what = String(input.path ?? "inline script");
      return names.length ? `${what} · inputs ${names.join(", ")}` : what;
    }
    case "Read":
    case "Write":
    case "Edit":
      return String(input.file_path ?? "");
    case "validate":
      return (input.data_refs as string[] | undefined)?.join(", ") ?? "";
    case "lineage":
      return String(input.target ?? "");
    default:
      return String(input.data_ref ?? JSON.stringify(input)).slice(0, 80);
  }
}

/** A reply's text and the follow-up questions in its closing "Next:" list, if it has one. */
export function followUps(text: string): { body: string; next: string[] } {
  const lines = text.split("\n");
  const at = lines.findIndex((l) => /^\s*\**next:?\**\s*$/i.test(l));
  if (at < 0) return { body: text, next: [] };
  const rest = lines.slice(at + 1).filter((l) => l.trim());
  if (!rest.length || !rest.every((l) => /^\s*[-*] /.test(l))) return { body: text, next: [] };
  const next = rest.map((l) => l.replace(/^\s*[-*] /, "").trim()).slice(0, 3);
  return { body: lines.slice(0, at).join("\n").trimEnd(), next };
}

/** Light markdown: paragraphs, bullet lists, tables, **bold**, `code`, data_refs as links, and
 * a closing "Next:" list as questions to ask with one click. */
export function Rich({
  text,
  onRef,
  onAsk,
}: {
  text: string;
  onRef: (ref: string) => void;
  onAsk?: (question: string) => void;
}) {
  const { body, next } = followUps(text);
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
      {body.split(/\n{2,}/).map((block, b) => {
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
      {next.length > 0 && onAsk && (
        <div className="suggestions follow-ups" role="group" aria-label="Ask next">
          {next.map((q) => (
            <button key={q} type="button" className="chip" onClick={() => onAsk(q)}>
              <Icon name="next" size={12} /> {q}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** The conversation after one event; `model` names the model answering. */
function apply(items: Item[], event: AgentEvent, model: string): Item[] {
  switch (event.type) {
    case "text":
      return [...items, { kind: "text", text: event.text }];
    case "tool":
      return [
        ...items,
        {
          kind: "step",
          id: event.id,
          name: event.name,
          input: event.input,
          state: "running",
          result: "",
          checks: [],
          review: null,
        },
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
    case "proposal": {
      const review: ReviewState = { id: event.id, kind: event.kind, state: "waiting" };
      if (items.some((it) => it.kind === "step" && it.id === event.tool_use_id))
        return items.map((it) => (it.kind === "step" && it.id === event.tool_use_id ? { ...it, review } : it));
      return [...items, { kind: "review", review, path: event.path }];
    }
    case "decision": {
      const state = event.accepted ? "accepted" : "rejected";
      return items.map((it) =>
        (it.kind === "step" || it.kind === "review") && it.review?.id === event.id
          ? { ...it, review: { ...it.review, state } }
          : it,
      );
    }
    case "done": {
      const tokens = event.tokens ? event.tokens.input + event.tokens.output : null;
      return [...items, { kind: "done", model, turns: event.turns, cost: event.cost_usd, tokens, seconds: event.seconds }];
    }
    case "error":
      return [...items, { kind: "error", message: event.message, hint: event.hint ?? null }];
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
  modelLabel,
  engine,
  setUp,
  onSetUp,
  suggestions,
  layers,
  byRef,
  selected,
  shownTime,
  point,
  area,
  scripts,
  openScript,
  review,
  onReviewMode,
  onProposal,
  onDecision,
  onTurnEnd,
  onDecide,
  onShowReview,
  onScriptTouched,
  onClearArea,
  onRef,
  onFocus,
}: Props) {
  const [items, setItems] = useState<Item[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [attach, setAttach] = useState(true);
  const session = useRef<string | null>(null);
  const [sessionEngine, setSessionEngine] = useState<ProviderInfo["engine"] | null>(null);
  const abort = useRef<AbortController | null>(null);
  const proposals = useRef(new Map<string, Proposal>());
  const writes = useRef(new Map<string, string>()); // tool call id -> script it writes
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
    if (scripts.length) context.push(`Scripts in analysis/: ${scripts.map((s) => s.replace(/^analysis\//, "")).join(", ")}.`);
    if (openScript) context.push(`Open in the editor: ${openScript}.`);
    // Claude Code and loci's own loop keep their conversations apart: a switch starts a new one.
    const fresh = session.current !== null && sessionEngine !== engine;
    if (fresh) session.current = null;
    setItems((it) => [
      ...it,
      ...(fresh ? [{ kind: "divider" as const, text: `New conversation with ${modelLabel}` }] : []),
      { kind: "user", text: message },
    ]);
    setDraft("");
    setBusy(true);
    abort.current = new AbortController();
    try {
      const body = { message: [...context, message].join("\n"), model, session_id: session.current, review };
      for await (const event of streamAgent(body, abort.current.signal)) {
        if (event.type === "session" || event.type === "done") {
          session.current = event.session_id;
          setSessionEngine(engine);
        }
        if (event.type === "proposal") {
          proposals.current.set(event.id, event);
          onProposal(event);
        }
        if (event.type === "decision") onDecision(event.id);
        // Once the agent has written or edited a script, show it in the editor.
        if (event.type === "tool" && (event.name === "Write" || event.name === "Edit"))
          writes.current.set(event.id, String(event.input.file_path ?? ""));
        if (event.type === "tool_result" && !event.is_error && writes.current.has(event.id))
          onScriptTouched(writes.current.get(event.id)!);
        setItems((it) => apply(it, event, modelLabel));
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError")
        setItems((it) => apply(it, { type: "error", message: String(e) }, modelLabel));
    } finally {
      setBusy(false);
      abort.current = null;
      onTurnEnd();
    }
  }

  function reviewCard(state: ReviewState) {
    const proposal = proposals.current.get(state.id);
    const isEdit = state.kind === "edit";
    if (state.state !== "waiting")
      return (
        <div className="review-card">
          <span className={`badge ${state.state === "accepted" ? "pass" : "fail"}`}>
            {state.state === "accepted" ? (isEdit ? "Accepted" : "Approved") : isEdit ? "Rejected" : "Not run"}
          </span>
        </div>
      );
    return (
      <div className="review-card waiting">
        <span>{isEdit ? "Waiting for you to review this change." : "Waiting for you to approve this run."}</span>
        <div className="actions">
          <button type="button" className="secondary" onClick={onShowReview}>
            <Icon name="code" size={13} /> {isEdit ? "Review the change" : "See the script"}
          </button>
          {proposal && (
            <>
              <button type="button" className="primary" onClick={() => onDecide(proposal, true, "")}>
                {isEdit ? "Accept" : "Run it"}
              </button>
              <button type="button" className="secondary" onClick={() => onDecide(proposal, false, "")}>
                {isEdit ? "Reject" : "Don't run"}
              </button>
            </>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className={`panel${open ? " open" : ""}`} role="tabpanel" aria-label="Agent">
      <div className="conversation" aria-live="polite">
        {items.length === 0 && (
          <div className="reply">
            <p style={{ color: "var(--muted)" }}>
              Ask a question about a place. The agent works through loci&apos;s tools and writes each
              analysis as a script you can read in the editor; every layer it makes appears on the
              map, checked automatically.
            </p>
            <div className="suggestions">
              {(suggestions.length ? suggestions : area ? [...AREA_SUGGESTIONS, ...SUGGESTIONS.slice(1)] : SUGGESTIONS).map((s) => (
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
              return <Rich key={i} text={item.text} onRef={onRef} onAsk={busy ? undefined : (q) => void send(q)} />;
            case "review":
              return (
                <div key={i} className="step">
                  <div className="top">
                    <code className="tool">review</code>
                    <span className="what">{item.path}</span>
                  </div>
                  {reviewCard(item.review)}
                </div>
              );
            case "step":
              return (
                <div key={i} className="step">
                  <div className="top">
                    <code className="tool">{item.name}</code>
                    <span className="what">{summarise(item.name, item.input)}</span>
                    {item.state === "running" && !item.review && <span className="spinner" aria-label="Running" />}
                    {item.state === "error" && !item.review && <span className="badge fail">Error</span>}
                    {item.checks.map((c) => (
                      <StatusBadge key={c.data_ref} status={c.status} />
                    ))}
                  </div>
                  {item.review && reviewCard(item.review)}
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
                  {item.name === "Write" && typeof item.input.content === "string" ? (
                    <details>
                      <summary>Script</summary>
                      <pre>{item.input.content}</pre>
                    </details>
                  ) : (
                    item.result && (
                      <details>
                        <summary>{item.state === "error" ? "What went wrong" : "Result"}</summary>
                        <pre>{item.result}</pre>
                      </details>
                    )
                  )}
                </div>
              );
            case "done":
              return (
                <span key={i} className="done-line">
                  {item.model} · {item.turns} {item.turns === 1 ? "turn" : "turns"} · {item.seconds.toFixed(0)} s
                  {item.cost != null
                    ? ` · $${item.cost.toFixed(2)}`
                    : item.tokens != null
                      ? ` · ${thousands(item.tokens)} tokens`
                      : ""}
                </span>
              );
            case "error":
              return (
                <div key={i} className="error-line">
                  <span>{item.message}</span>
                  {item.hint && <span className="hint">{item.hint}</span>}
                </div>
              );
            case "divider":
              return (
                <div key={i} className="divider-line" role="separator">
                  <span>{item.text}</span>
                </div>
              );
          }
        })}
        <div ref={end} />
      </div>
      {setUp && (
        <p className="engine-note setup-note">
          {setUp.label} is not set up yet.{" "}
          <button type="button" className="link" onClick={onSetUp}>
            Add {setUp.vendor[0] && "AEIOU".includes(setUp.vendor[0]) ? "an" : "a"} {setUp.vendor} API key
          </button>{" "}
          to ask the agent; the map works without one.
        </p>
      )}
      {sessionEngine !== null && sessionEngine !== engine && !busy && (
        <p className="engine-note">
          {modelLabel} will start a new conversation: it cannot see the messages above.
        </p>
      )}
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
            <label htmlFor="review" className="visually-hidden">What waits for your answer</label>
            <select
              id="review"
              className="review-mode"
              value={review}
              title="What waits for your answer before the agent goes on"
              onChange={(e) => onReviewMode(e.target.value as Review)}
            >
              <option value="changes">Review changes</option>
              <option value="runs">Review every run</option>
            </select>
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
