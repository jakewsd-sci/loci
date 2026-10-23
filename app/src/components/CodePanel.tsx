import Editor, { loader, type Monaco } from "@monaco-editor/react";
import * as monaco from "monaco-editor";
import EditorWorker from "monaco-editor/editor/editor.worker?worker";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api";
import { Icon } from "../icons";
import type { LayerInfo, MapFocus, RunResult, ScriptFile } from "../types";
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
    },
  });
}

/** Names the script reads with script.read("name"): each needs a data_ref to run. */
function declaredInputs(code: string): string[] {
  return [...new Set([...code.matchAll(/script\.read\(\s*["']([^"']+)["']/g)].map((m) => m[1]))];
}

interface Props {
  open: boolean;
  layers: LayerInfo[];
  byRef: Map<string, LayerInfo>;
  request: { path: string; key: number } | null; // a script to open, from elsewhere in the app
  onRef: (ref: string) => void;
  onFocus: (focus: MapFocus) => void;
}

export function CodePanel({ open, layers, byRef, request, onRef, onFocus }: Props) {
  const [files, setFiles] = useState<ScriptFile[]>([]);
  const [path, setPath] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [saved, setSaved] = useState("");
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [result, setResult] = useState<RunResult | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  const refresh = useCallback(async () => setFiles(await api.scripts()), []);
  useEffect(() => {
    void refresh();
  }, [refresh]);

  const open_ = useCallback(async (next: string) => {
    try {
      const file = await api.script(next);
      setPath(file.path);
      setCode(file.content);
      setSaved(file.content);
      setResult(null);
      setMessage(null);
    } catch (e) {
      setMessage(e instanceof ApiError ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    if (request) void open_(request.path);
  }, [request, open_]);

  useEffect(() => {
    if (!path && files.length) void open_(files[0].path);
  }, [files, path, open_]);

  const names = useMemo(() => declaredInputs(code), [code]);
  const dirty = code !== saved;

  async function save(): Promise<boolean> {
    if (!path) return false;
    try {
      await api.saveScript(path, code);
      setSaved(code);
      await refresh();
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
    await refresh();
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
      <div className="editor">
        <Editor
          language="python"
          theme="loci"
          beforeMount={defineTheme}
          value={code}
          onChange={(v) => setCode(v ?? "")}
          options={{
            fontFamily: "Geist Mono, ui-monospace, monospace",
            fontSize: 12.5,
            lineHeight: 21,
            minimap: { enabled: false },
            scrollBeyondLastLine: false,
            padding: { top: 12 },
            renderLineHighlight: "line",
            smoothScrolling: true,
            readOnly: !path,
          }}
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
