// The loci web API's JSON shapes (see src/loci/web in the Python package).

export type CheckStatus = "pass" | "warn" | "fail";
export type ProducedBy = "load" | "stac_search" | "run";
export type Bounds = [number, number, number, number];

export interface LayerInfo {
  data_ref: string;
  kind: "raster" | "vector";
  name: string;
  produced_by: ProducedBy | null;
  created_at: string;
  validation: CheckStatus | null;
  crs: string | null;
  bounds_wgs84: Bounds | null;
  bands: string[];
  times: number;
  drawable: boolean;
  note: string | null;
}

export interface LayerDisplay {
  data_ref: string;
  mode: "tiles" | "image" | "geojson";
  url: string;
  coordinates: [number, number][] | null;
  bands: string[] | null;
  stretch: [number, number] | null;
  colormap: string | null;
  time: string | null;
  note: string | null;
}

export interface PointValues {
  data_ref: string;
  lon: number;
  lat: number;
  x: number;
  y: number;
  crs: string;
  times: string[];
  bands: { name: string; values: (number | null)[] }[];
  features: Record<string, string>[];
}

export interface CheckResult {
  check: string;
  status: CheckStatus;
  data_refs: string[];
  message: string;
  hint: string | null;
}

export interface ValidationReport {
  status: CheckStatus;
  data_refs: string[];
  counts: Record<CheckStatus, number>;
  checks: CheckResult[];
}

export interface LineageStep {
  data_ref: string | null;
  file: string | null;
  produced_by: ProducedBy | null;
  created_at: string;
  description: string;
  inputs: string[];
  sources: string[];
  stac_items: number;
  validation: CheckStatus | null;
}

export interface Provenance {
  call: { tool?: string; arguments?: Record<string, unknown> };
  run_id: string | null;
  script: string | null;
  code_sha256: string | null;
}

export interface LineageResult {
  steps: LineageStep[];
  provenance: Provenance | null;
  original_sources: string[];
  stac_item_count: number;
  stac_items: string[];
  warnings: string[];
  notes: string[];
}

export interface LayerDetail {
  layer: LayerInfo;
  lineage: LineageResult;
  validation: ValidationReport | null;
}

export interface ScriptFile {
  path: string;
  size: number;
  modified: string;
}

export interface RunOutput {
  file: string;
  data_ref: string | null;
  kind: string | null;
  validation: CheckStatus | null;
  problems: string[];
  note: string | null;
}

export interface RunResult {
  run_id: string;
  status: "ok" | "error" | "timeout";
  exit_code: number | null;
  seconds: number;
  stdout: string;
  stderr: string;
  outputs: RunOutput[];
  warnings: string[];
  notes: string[];
}

export interface Model {
  id: string;
  label: string;
  note: string;
}

export type AgentEvent =
  | { type: "session"; session_id: string }
  | { type: "text"; text: string }
  | { type: "tool"; id: string; name: string; input: Record<string, unknown> }
  | { type: "tool_result"; id: string; is_error: boolean; text: string }
  | { type: "check"; tool_use_id: string; data_ref: string; status: CheckStatus }
  | {
      type: "done";
      session_id: string;
      turns: number;
      cost_usd: number | null;
      seconds: number;
      is_error: boolean;
    }
  | { type: "error"; message: string };
