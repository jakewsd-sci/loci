import { type FormEvent, type KeyboardEvent, useEffect, useRef, useState } from "react";
import { Icon } from "../icons";
import type { ModelInfo, ProviderInfo } from "../types";

interface Props {
  providers: ProviderInfo[];
  value: string; // "<provider>:<model>"
  error: string | null; // why the models could not be listed
  open: boolean;
  onOpenChange: (open: boolean) => void; // opening reads the list again, so new keys show
  onChange: (model: string) => void;
  onSaveKey: (provider: string, key: string) => Promise<void>;
  onForgetKey: (provider: string) => Promise<void>;
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

const host = (url: string) => url.replace(/^https?:\/\//, "").split("/")[0];

/** One provider's key field: pasted once, kept in the user's settings, never shown again. */
function KeyEntry({ provider, onSave }: { provider: ProviderInfo; onSave: (key: string) => Promise<void> }) {
  const [key, setKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await onSave(key);
      setKey("");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="key-entry" onSubmit={(e) => void save(e)}>
      <div className="key-row">
        <input
          type="password"
          value={key}
          placeholder={`${provider.vendor} API key`}
          aria-label={`${provider.vendor} API key`}
          autoComplete="off"
          spellCheck={false}
          onChange={(e) => setKey(e.target.value)}
        />
        <button type="submit" className="primary" disabled={!key.trim() || saving}>
          {saving ? "Saving" : "Save"}
        </button>
      </div>
      {error && <p className="setup error">{error}</p>}
      <p className="setup">
        Get one at{" "}
        <a href={provider.keys_url} target="_blank" rel="noreferrer">
          {host(provider.keys_url)}
        </a>
        . It is kept in your own settings on this computer, never in the project.
      </p>
    </form>
  );
}

/** The model picker: models grouped by provider; a provider not set up takes its key here. */
export function ModelPicker({
  providers,
  value,
  error,
  open,
  onOpenChange,
  onChange,
  onSaveKey,
  onForgetKey,
}: Props) {
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const current = findModel(providers, value);

  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) onOpenChange(false);
    };
    const escape = (e: globalThis.KeyboardEvent) => {
      if (e.key !== "Escape") return;
      onOpenChange(false);
      button.current?.focus();
    };
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", away);
      document.removeEventListener("keydown", escape);
    };
  }, [open, onOpenChange]);

  useEffect(() => {
    // Start keyboard use from the chosen model, or from the first key field.
    if (open) root.current?.querySelector<HTMLElement>(".model-option[aria-current], .model-option, .key-entry input")?.focus();
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
    onOpenChange(false);
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
        onClick={() => onOpenChange(!open)}
      >
        <span className="provider">{current?.provider.label ?? "Model"}</span>
        {current && <span className="name">{current.model.label}</span>}
        {current && !current.provider.configured && <span className="unset-dot" aria-label="not set up" />}
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
                {p.key_source === "app" && (
                  <button type="button" className="ghost forget" onClick={() => void onForgetKey(p.id)}>
                    Remove key
                  </button>
                )}
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
                <KeyEntry provider={p} onSave={(key) => onSaveKey(p.id, key)} />
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
