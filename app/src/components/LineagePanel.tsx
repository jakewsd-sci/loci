import { useEffect, useState } from "react";
import { api } from "../api";
import { Icon } from "../icons";
import type { LayerDetail, LayerInfo, MapFocus } from "../types";
import { CheckList } from "./Checks";
import { StatusBadge } from "./LayersPanel";

interface Props {
  open: boolean;
  selected: string | null;
  version: number; // changes when the workspace changes, so the detail is reloaded
  byRef: Map<string, LayerInfo>;
  onRef: (ref: string) => void;
  onOpenScript: (script: { path?: string; runId?: string }) => void;
  onFocus: (focus: MapFocus) => void;
}

export function LineagePanel({ open, selected, version, byRef, onRef, onOpenScript, onFocus }: Props) {
  const [detail, setDetail] = useState<LayerDetail | null>(null);
  const [checking, setChecking] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    if (!selected) {
      setDetail(null);
      return;
    }
    let live = true;
    api.layer(selected).then((d) => live && setDetail(d), () => live && setDetail(null));
    return () => {
      live = false;
    };
  }, [selected, version, reload]);

  async function check() {
    if (!selected) return;
    setChecking(true);
    try {
      await api.validate(selected);
      setReload((n) => n + 1);
    } finally {
      setChecking(false);
    }
  }

  const script = detail?.script ?? null;
  return (
    <div className={`panel${open ? " open" : ""}`} role="tabpanel" aria-label="Lineage">
      {!detail ? (
        <p className="placeholder">Select a layer to see its checks and how it was made.</p>
      ) : (
        <div className="lineage">
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <h3>{detail.layer.name}</h3>
            <span className="ref">
              {detail.layer.data_ref} · {detail.layer.crs ?? "no CRS"}
            </span>
          </div>

          <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <span className="label">Checks</span>
              <button type="button" className="secondary" disabled={checking} onClick={() => void check()}>
                {checking ? <span className="spinner" /> : <Icon name="shield" size={14} />}
                {detail.validation ? "Check again" : "Run checks"}
              </button>
            </div>
            {detail.validation ? (
              <CheckList checks={detail.validation.checks} byRef={byRef} onFocus={onFocus} />
            ) : (
              <span style={{ color: "var(--muted)", fontSize: 12 }}>Not checked yet.</span>
            )}
          </section>

          <section style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <span className="label">How it was made, newest first</span>
            <ol className="timeline">
              {detail.lineage.steps.map((step, i) => (
                <li key={`${step.data_ref}-${i}`}>
                  <span className="rail-line" aria-hidden="true">
                    <span className="dot" />
                    <span className="line" />
                  </span>
                  <div className="body">
                    <span style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                      <strong style={{ fontWeight: 500 }}>{step.produced_by ?? "registered"}</strong>
                      {step.data_ref && (
                        <button type="button" className="ref-link" onClick={() => onRef(step.data_ref as string)}>
                          {step.data_ref}
                        </button>
                      )}
                      {step.validation && <StatusBadge status={step.validation} />}
                    </span>
                    <span className="what">{step.description}</span>
                    {i === 0 && script && (
                      <div className="script-link">
                        {script.path && (
                          <button type="button" className="secondary" onClick={() => onOpenScript({ path: script.path! })}>
                            <Icon name="file" size={14} /> Open {script.path}
                          </button>
                        )}
                        {(script.changed || !script.path) && (
                          <button
                            type="button"
                            className="secondary"
                            onClick={() => onOpenScript({ runId: script.run_id, path: script.path ?? undefined })}
                          >
                            <Icon name="code" size={14} /> The script as it ran
                          </button>
                        )}
                        {script.changed && (
                          <span className="changed">
                            {script.path} has changed since {script.run_id} made this layer.
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                </li>
              ))}
            </ol>
          </section>

          {(detail.lineage.original_sources.length > 0 || detail.lineage.stac_item_count > 0) && (
            <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <span className="label">Original sources</span>
              <ul className="sources">
                {detail.lineage.original_sources.map((s) => (
                  <li key={s}>{s}</li>
                ))}
                {detail.lineage.stac_items.map((s) => (
                  <li key={s}>{s}</li>
                ))}
                {detail.lineage.stac_item_count > detail.lineage.stac_items.length && (
                  <li>and {detail.lineage.stac_item_count - detail.lineage.stac_items.length} more items</li>
                )}
              </ul>
            </section>
          )}
          {[...detail.lineage.warnings, ...detail.lineage.notes].map((w) => (
            <span key={w} style={{ fontSize: 12, color: "var(--warn)" }}>{w}</span>
          ))}
        </div>
      )}
    </div>
  );
}
