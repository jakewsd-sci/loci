import type {
  AgentEvent,
  LayerDetail,
  LayerDisplay,
  LayerInfo,
  ModelList,
  PointValues,
  Review,
  RunResult,
  RunScript,
  ScriptFile,
  ValidationReport,
} from "./types";

export class ApiError extends Error {
  readonly hint: string | null;

  constructor(message: string, hint: string | null) {
    super(message);
    this.hint = hint;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(body?.error ?? response.statusText, body?.hint ?? null);
  }
  return body as T;
}

function send<T>(path: string, method: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

const enc = encodeURIComponent;

export const api = {
  layers: () => request<LayerInfo[]>("/api/layers"),
  layer: (ref: string) => request<LayerDetail>(`/api/layers/${ref}`),
  display: (ref: string, bands?: string[]) =>
    request<LayerDisplay>(
      `/api/layers/${ref}/display${bands?.length ? `?bands=${enc(bands.join(","))}` : ""}`,
    ),
  point: (ref: string, lon: number, lat: number) =>
    request<PointValues>(`/api/layers/${ref}/point?lon=${lon}&lat=${lat}`),
  validate: (ref: string) => send<ValidationReport>(`/api/layers/${ref}/validate`, "POST", {}),
  scripts: () => request<ScriptFile[]>("/api/scripts"),
  script: (path: string) => request<{ path: string; content: string }>(`/api/scripts/${path}`),
  saveScript: (path: string, content: string) =>
    send<{ path: string; content: string }>(`/api/scripts/${path}`, "PUT", { content }),
  run: (path: string, inputs: Record<string, string>) =>
    send<RunResult>("/api/run", "POST", { path, inputs }),
  models: () => request<ModelList>("/api/models"),
  saveKey: (provider: string, apiKey: string) =>
    send<ModelList>(`/api/providers/${provider}/key`, "PUT", { api_key: apiKey }),
  forgetKey: (provider: string) =>
    request<ModelList>(`/api/providers/${provider}/key`, { method: "DELETE" }),
  runScript: (runId: string) => request<RunScript>(`/api/runs/${runId}/script`),
  decide: (id: string, accepted: boolean, note = "") =>
    send<{ id: string; accepted: boolean }>("/api/agent/decide", "POST", { id, accepted, note }),
};

/** Send a chat message and yield the agent's events as they stream in. */
export async function* streamAgent(
  body: { message: string; model: string; session_id: string | null; review: Review },
  signal?: AbortSignal,
): AsyncGenerator<AgentEvent> {
  const response = await fetch("/api/agent", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok || !response.body) {
    const error = await response.json().catch(() => null);
    yield { type: "error", message: error?.error ?? response.statusText };
    return;
  }
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += value;
    let end = buffer.indexOf("\n\n");
    while (end >= 0) {
      const chunk = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      for (const line of chunk.split("\n")) {
        if (line.startsWith("data: ")) yield JSON.parse(line.slice(6)) as AgentEvent;
      }
      end = buffer.indexOf("\n\n");
    }
  }
}

/** Call `onLayers` when the workspace's layers change and `onScripts` when a script under
 * analysis/ is added, removed or saved; returns a function that stops. */
export function watchWorkspace(onLayers: () => void, onScripts: () => void): () => void {
  const source = new EventSource("/api/events");
  source.addEventListener("layers", onLayers);
  source.addEventListener("scripts", onScripts);
  return () => source.close();
}

/** data_refs mentioned in a piece of text, in order, each once. */
export function refsIn(text: string): string[] {
  return [...new Set(text.match(/\b(?:raster|vector)-[0-9a-f]{8}\b/g) ?? [])];
}
