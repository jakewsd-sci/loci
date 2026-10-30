import { Icon } from "../icons";
import type { CheckResult, LayerInfo, MapFocus, Sandbox } from "../types";
import { StatusBadge } from "./LayersPanel";

const COMPARE = new Set(["crs_consistent", "extents_overlap", "resolution_consistent"]);

/** What of a check can be shown on the map: out-of-range pixels, or the inputs' footprints. */
export function focusFor(check: CheckResult, byRef: Map<string, LayerInfo>): MapFocus | null {
  if (check.status === "pass") return null;
  if (check.check === "value_range") {
    const ref = check.data_refs[0];
    const band = check.details.target;
    const range = check.details.range;
    if (byRef.get(ref)?.kind !== "raster" || typeof band !== "string" || !Array.isArray(range)) return null;
    const [low, high] = range as [number, number | null];
    const span = `[${low}, ${high ?? "∞"}]`;
    return { kind: "outside", ref, band, range: [low, high], label: `${band} outside ${span}` };
  }
  if (COMPARE.has(check.check) && check.data_refs.every((r) => byRef.get(r)?.bounds_wgs84)) {
    const names = check.data_refs.map((r) => byRef.get(r)?.name ?? r);
    return { kind: "footprints", refs: check.data_refs, status: check.status, label: `Footprints of ${names.join(", ")}` };
  }
  return null;
}

export function CheckList({
  checks,
  byRef,
  onFocus,
}: {
  checks: CheckResult[];
  byRef: Map<string, LayerInfo>;
  onFocus: (focus: MapFocus) => void;
}) {
  return (
    <ul className="checks">
      {checks.map((c, i) => {
        const focus = focusFor(c, byRef);
        return (
          <li key={`${c.check}-${i}`}>
            <div className="top">
              <StatusBadge status={c.status} />
              <span className="id">{c.check}</span>
              {focus && (
                <button type="button" className="ghost show" onClick={() => onFocus(focus)}>
                  <Icon name="target" size={13} /> Show on map
                </button>
              )}
            </div>
            <span className="message">{c.message}</span>
            {c.hint && c.status !== "pass" && <span className="hint">{c.hint}</span>}
          </li>
        );
      })}
    </ul>
  );
}

const SANDBOXES: Record<Sandbox, { label: string; title: string; status: string }> = {
  os: {
    label: "Sandboxed",
    title: "Ran in the operating system's sandbox and loci's guard: it could write only in its run folder and reach only its inputs.",
    status: "pass",
  },
  guard: {
    label: "Guarded",
    title: "Ran with loci's guard: it could write only in its run folder, start no programs and reach only its inputs. Install srt for the operating system's sandbox too.",
    status: "pass",
  },
  off: {
    label: "Not sandboxed",
    title: "LOCI_SANDBOX=off: the script could write and reach anywhere.",
    status: "warn",
  },
};

/** How a run's script was kept in, with what that means on hover. */
export function SandboxBadge({ sandbox }: { sandbox: Sandbox | null | undefined }) {
  if (!sandbox) return null;
  const { label, title, status } = SANDBOXES[sandbox];
  return (
    <span className={`badge ${status}`} title={title}>
      {label}
    </span>
  );
}
