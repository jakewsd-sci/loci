import { type KeyboardEvent, useEffect, useRef, useState } from "react";
import { Icon } from "../icons";
import type { ModelInfo, ProviderInfo } from "../types";

interface Props {
  providers: ProviderInfo[];
  value: string; // "<provider>:<model>"
  error: string | null; // why the models could not be listed
  onOpen: () => void; // the list is read again, so a newly set up provider appears
  onChange: (model: string) => void;
}

/** A model and its provider, from a "<provider>:<model>" id. */
export function findModel(
  providers: ProviderInfo[],
  id: string,
): { provider: ProviderInfo; model: ModelInfo } | null {
  for (const provider of providers) {
    const model = provider.models.find((m) => m.id === id);
    if (model) return { provider, model };
  }
  return null;
}

/** The model picker: models grouped by provider; a provider not set up says how to set it up. */
export function ModelPicker({ providers, value, error, onOpen, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const current = findModel(providers, value);

  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    const escape = (e: globalThis.KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setOpen(false);
      button.current?.focus();
    };
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", away);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  useEffect(() => {
    // Start keyboard use from the chosen model.
    if (open) root.current?.querySelector<HTMLButtonElement>(".model-option[aria-current], .model-option")?.focus();
  }, [open]);

  function move(e: KeyboardEvent) {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const options = [...(root.current?.querySelectorAll<HTMLButtonElement>(".model-option") ?? [])];
    const at = options.indexOf(document.activeElement as HTMLButtonElement);
    options[(at + (e.key === "ArrowDown" ? 1 : -1) + options.length) % options.length]?.focus();
  }

  function choose(id: string) {
    onChange(id);
    setOpen(false);
    button.current?.focus();
  }

  return (
    <div className="model-picker" ref={root}>
      <button
        ref={button}
        type="button"
        className="model-button"
        aria-expanded={open}
        aria-label={current ? `Model: ${current.provider.label} ${current.model.label}` : "Model"}
        title={current ? `${current.provider.label} · ${current.model.label}` : undefined}
        onClick={() => {
          if (!open) onOpen();
          setOpen(!open);
        }}
      >
        <span className="provider">{current?.provider.label ?? "Model"}</span>
        {current && <span className="name">{current.model.label}</span>}
        <Icon name="chevron" size={12} />
      </button>
      {open && (
        <div className="model-menu glass" onKeyDown={move}>
          {error && <p className="setup error">{error}</p>}
          {providers.map((p) => (
            <div key={p.id} className="model-group" role="group" aria-label={p.label}>
              <div className="group-label">
                {p.label}
                {p.route && p.route !== "Anthropic" && <span> · via {p.route}</span>}
                {!p.configured && <span className="badge none">Not set up</span>}
              </div>
              {p.configured ? (
                p.models.map((m) => (
                  <button
                    key={m.id}
                    type="button"
                    className="model-option"
                    aria-current={m.id === value ? "true" : undefined}
                    onClick={() => choose(m.id)}
                  >
                    <span className="name">{m.label}</span>
                    <span className="note">{m.note}</span>
                    {m.id === value && <Icon name="check" size={13} />}
                  </button>
                ))
              ) : (
                <p className="setup">{p.setup}</p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
