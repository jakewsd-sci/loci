import Editor, { DiffEditor, loader, type Monaco } from "@monaco-editor/react";
import * as monaco from "monaco-editor";
import EditorWorker from "monaco-editor/editor/editor.worker?worker";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api";
import { Icon } from "../icons";
import type { LayerInfo, MapFocus, Proposal, RunResult, RunScript, ScriptFile } from "../types";
import { CheckList } from "./Checks";
import { StatusBadge } from "./LayersPanel";

// Monaco from the installed package, not a CDN, so the app works offline.
self.MonacoEnvironment = { getWorker: () => new EditorWorker() };
loader.config({ monaco });

const TEMPLATE = `from loci import script

# Inputs are declared when you run the script; read them by name.
data = script.read("data")

# ... your analysis ...

script.save(data, "result.tif")
`;

const EDITOR_OPTIONS = {
  fontFamily: "Geist Mono, ui-monospace, monospace",
  fontSize: 12.5,
  lineHeight: 21,
  minimap: { enabled: false },
  scrollBeyondLastLine: false,
  padding: { top: 12 },
  renderLineHighlight: "line" as const,
  smoothScrolling: true,
};

function defineTheme(m: Monaco) {
  m.editor.defineTheme("loci", {
    base: "vs-dark",
    inherit: true,
    rules: [
      { token: "keyword", foreground: "C6B8FF" },
      { token: "string", foreground: "A8DDB5" },
      { token: "number", foreground: "F5C08A" },
      { token: "comment", foreground: "7C8591", fontStyle: "italic" },
    ],
    colors: {
      "editor.background": "#0D0F12",
      "editor.lineHighlightBackground": "#16191E",
      "editorLineNumber.foreground": "#4B525B",
      "editorLineNumber.activeForeground": "#A1A9B3",
      "editor.selectionBackground": "#2B3A55",
      "diffEditor.insertedLineBackground": "#5BD49B1F",
      "diffEditor.removedLineBackground": "#F2766B1F",
    },
  });
}

/** Names the script reads with script.read("name"): each needs a data_ref to run. */
function declaredInputs(code: string): string[] {
  return [...new Set([...code.matchAll(/script\.read\(\s*["']([^"']+)["']/g)].map((m) => m[1]))];
}

export interface ScriptRequest {
  path?: string; // a script under analysis/ to open
  runId?: string; // or the exact script a run executed, read-only
  auto?: boolean; // the agent touched it: open it, but never over unsaved edits
  key: number;
}

interface Props {
  open: boolean;
  layers: LayerInfo[];
  byRef: Map<string, LayerInfo>;
  files: ScriptFile[];
  filesVersion: number; // changes when a script is saved anywhere, the agent included
  request: ScriptRequest | null; // a script to open, from elsewhere in the app
  review: Proposal | null; // what the agent is waiting on, if anything
  onOpen: (path: string | null) => void;
  onDecide: (proposal: Proposal, accepted: boolean, note: string) => void;
  onRef: (ref: string) => void;
  onFocus: (focus: MapFocus) => void;
}

export function CodePanel({
  open,
  layers,
  byRef,
  files,
  filesVersion,
  request,
  review,
  onOpen,
  onDecide,
  onRef,
  onFocus,
}: Props) {
  const [path, setPath] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [saved, setSaved] = useState("");
  const [snapshot, setSnapshot] = useState<(RunScript & { path?: string }) | null>(null);
  const [onDisk, setOnDisk] = useState<string | null>(null); // a newer version saved elsewhere
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [result, setResult] = useState<RunResult | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [note, setNote] = useState("");
  const [picked, setPicked] = useState(false); // opened on purpose, not just the first in the list

  const open_ = useCallback(async (next: string, onPurpose = true) => {
    try {
      const file = await api.script(next);
      setPath(file.path);
      setCode(file.content);
      setSaved(file.content);
      setPicked(onPurpose);
      setSnapshot(null);
      setOnDisk(null);
      setResult(null);
      setMessage(null);
    } catch (e) {
      setMessage(e instanceof ApiError ? e.message : String(e));
    }
  }, []);

  // The agent hears which script is open only when someone chose it.
  useEffect(() => onOpen(snapshot || !picked ? null : path), [path, snapshot, picked, onOpen]);

  useEffect(() => {
    if (!request) return;
    if (request.runId) {
      const runId = request.runId;
      api.runScript(runId).then(
        (s) => setSnapshot({ ...s, path: request.path }),
        (e) => setMessage(e instanceof ApiError ? e.message : String(e)),
      );
    } else if (request.path && !(request.auto && code !== saved && request.path !== path)) {
      void open_(request.path);
    }
    // Only a new request should trigger this, not typing.
  }, [request, open_]);

  useEffect(() => {
    if (!path && files.length) void open_(files[0].path, false);
  }, [files, path, open_]);

  // A script saved elsewhere (by the agent, say): reload it, or say so if there are unsaved edits.
  useEffect(() => {
    if (!path || !filesVersion) return;
    let live = true;
    api.script(path).then(
      (file) => {
        if (!live || file.content === saved) return;
        if (code === saved) {
          setCode(file.content);
          setSaved(file.content);
        } else {
          setOnDisk(file.content);
        }
      },
      () => undefined,
    );
    return () => {
      live = false;
    };
    // Only a change on disk should trigger this, not typing.
  }, [filesVersion]);

  useEffect(() => setNote(""), [review?.id]);

  const names = useMemo(() => declaredInputs(code), [code]);
  const dirty = code !== saved;

  async function save(): Promise<boolean> {
    if (!path) return false;
    try {
      await api.saveScript(path, code);
      setSaved(code);
      setOnDisk(null);
      return true;
    } catch (e) {
      setMessage(e instanceof ApiError ? e.message : String(e));
      return false;
    }
  }

  async function create() {
    const name = window.prompt("New script name", "analysis/new_analysis.py");
    if (!name) return;
    await api.saveScript(name, TEMPLATE);
    await open_(name);
  }

  async function run() {
    if (!path || !(await save())) return;
    setRunning(true);
    setMessage(null);
    try {
      setResult(await api.run(path, inputs));
    } catch (e) {
      setMessage(e instanceof ApiError ? `${e.message}${e.hint ? ` ${e.hint}` : ""}` : String(e));
    } finally {
      setRunning(false);
    }
  }

  const missing = names.filter((n) => !inputs[n]);

  if (review) {
    const isEdit = review.kind === "edit";
    return (
      <div className={`panel${open ? " open" : ""}`} role="tabpanel" aria-label="Code">
        <div className="review-bar">
          <span className="badge running">{isEdit ? "Change to review" : "Run to review"}</span>
          <span className="mono what">{review.path}</span>
        </div>
        <p className="review-lead">
          {isEdit
            ? "The agent wants to change this script. It is waiting for your answer; nothing changes until you accept."
            : "The agent wants to run this script. It is waiting for your answer."}
        </p>
        {!isEdit && Object.keys(review.inputs).length > 0 && (
          <dl className="review-inputs">
            {Object.entries(review.inputs).map(([name, ref]) => (
              <div key={name} style={{ display: "contents" }}>
                <dt className="mono">{name}</dt>
                <dd>
                  <button type="button" className="ref-link" onClick={() => onRef(ref)}>
                    {ref}
                  </button>
                  {byRef.get(ref) ? ` ${byRef.get(ref)!.name}` : ""}
                </dd>
              </div>
            ))}
          </dl>
        )}
        <div className="editor">
          {isEdit ? (
            <DiffEditor
              language="python"
              theme="loci"
              beforeMount={defineTheme}
              original={review.before}
              modified={review.after}
              // One pair of models per review, kept on unmount: the widget otherwise outlives them.
              originalModelPath={`review/${review.id}/before.py`}
              modifiedModelPath={`review/${review.id}/after.py`}
              keepCurrentOriginalModel
              keepCurrentModifiedModel
              options={{ ...EDITOR_OPTIONS, readOnly: true, renderSideBySide: false, originalEditable: false }}
            />
          ) : (
            <Editor
              language="python"
              theme="loci"
              beforeMount={defineTheme}
              value={review.code}
              options={{ ...EDITOR_OPTIONS, readOnly: true }}
            />
          )}
        </div>
        <div className="review-actions">
          <label htmlFor="review-note" className="visually-hidden">Note for the agent</label>
          <input
            id="review-note"
            value={note}
            placeholder="Note for the agent (optional), e.g. why not"
            onChange={(e) => setNote(e.target.value)}
          />
          <div style={{ display: "flex", gap: 8 }}>
            <button type="button" className="primary" onClick={() => onDecide(review, true, note)}>
              <Icon name="check" size={14} /> {isEdit ? "Accept" : "Run it"}
            </button>
            <button type="button" className="secondary" onClick={() => onDecide(review, false, note)}>
              <Icon name="close" size={14} /> {isEdit ? "Reject" : "Don't run"}
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (snapshot) {
    return (
      <div className={`panel${open ? " open" : ""}`} role="tabpanel" aria-label="Code">
        <div className="review-bar">
          <span className="badge none">Read-only</span>
          <span className="mono what">
            {snapshot.path ?? "script"} as {snapshot.run_id} ran it · sha256 {snapshot.sha256.slice(0, 12)}
          </span>
          <button type="button" className="ghost close" aria-label="Close" onClick={() => setSnapshot(null)}>
            <Icon name="close" size={14} />
          </button>
        </div>
        <div className="editor">
          <Editor
            language="python"
            theme="loci"
            beforeMount={defineTheme}
            value={snapshot.content}
            options={{ ...EDITOR_OPTIONS, readOnly: true }}
          />
        </div>
      </div>
    );
  }

  return (
    <div className={`panel${open ? " open" : ""}`} role="tabpanel" aria-label="Code">
      <div className="code-bar">
        <label htmlFor="script" className="visually-hidden">Script</label>
        <select id="script" value={path ?? ""} onChange={(e) => void open_(e.target.value)}>
          {!path && <option value="">No scripts in analysis/ yet</option>}
          {files.map((f) => (
            <option key={f.path} value={f.path}>
              {f.path}
              {f.path === path && dirty ? " ●" : ""}
            </option>
          ))}
        </select>
        <button type="button" className="secondary" onClick={() => void create()}>
          New
        </button>
        <button type="button" className="secondary" disabled={!dirty} onClick={() => void save()} aria-label="Save">
          <Icon name="save" size={14} />
        </button>
      </div>
      {onDisk !== null && (
        <div className="on-disk">
          <span>This script was saved elsewhere, perhaps by the agent, while you were editing it.</span>
          <button
            type="button"
            className="secondary"
            onClick={() => {
              setCode(onDisk);
              setSaved(onDisk);
              setOnDisk(null);
            }}
          >
            Load that version
          </button>
        </div>
      )}
      <div className="editor">
        <Editor
          language="python"
          theme="loci"
          beforeMount={defineTheme}
          value={code}
          onChange={(v) => setCode(v ?? "")}
          options={{ ...EDITOR_OPTIONS, readOnly: !path }}
        />
      </div>
      {path && (
        <div className="run-setup">
          {names.map((name) => (
            <label key={name}>
              <span className="mono">{name}</span>
              <select
                value={inputs[name] ?? ""}
                onChange={(e) => setInputs((v) => ({ ...v, [name]: e.target.value }))}
              >
                <option value="">Choose a layer…</option>
                {layers.map((l) => (
                  <option key={l.data_ref} value={l.data_ref}>
                    {l.name} · {l.data_ref}
                  </option>
                ))}
              </select>
            </label>
          ))}
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <button type="button" className="primary" disabled={running || missing.length > 0} onClick={() => void run()}>
              {running ? <span className="spinner" style={{ borderTopColor: "#0a0b0d" }} /> : <Icon name="play" size={14} />}
              {running ? "Running" : "Save and run"}
            </button>
            {missing.length > 0 && (
              <span style={{ fontSize: 12, color: "var(--muted)" }}>Choose a layer for each input.</span>
            )}
          </div>
          {message && <span className="error-line">{message}</span>}
        </div>
      )}
      {result && (
        <div className="run-result">
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span className={`badge ${result.status === "ok" ? "pass" : "fail"}`}>
              {result.status === "ok" ? "Finished" : result.status === "timeout" ? "Timed out" : "Failed"}
            </span>
            <span className="ref">
              {result.run_id} · {result.seconds.toFixed(1)} s
            </span>
          </div>
          {result.outputs.map((o) => (
            <div key={o.file} className="output-row">
              <span className="mono" style={{ fontSize: 12 }}>{o.file}</span>
              {o.data_ref && (
                <button type="button" className="ref-link" onClick={() => onRef(o.data_ref as string)}>
                  {o.data_ref}
                </button>
              )}
              {o.data_ref && <StatusBadge status={o.validation} />}
            </div>
          ))}
          {result.input_checks && (
            <>
              <span className="label">Inputs checked together</span>
              <CheckList checks={result.input_checks.checks} byRef={byRef} onFocus={onFocus} />
            </>
          )}
          {[...result.warnings, ...result.notes].map((w) => (
            <span key={w} style={{ fontSize: 12, color: "var(--muted)" }}>{w}</span>
          ))}
          {result.stdout && <pre>{result.stdout}</pre>}
          {result.stderr && result.status !== "ok" && <pre style={{ color: "var(--fail)" }}>{result.stderr}</pre>}
        </div>
      )}
    </div>
  );
}
