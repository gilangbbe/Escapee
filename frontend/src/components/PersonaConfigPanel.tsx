import { useEffect, useState } from "react";
import type {
  PersonaCatalogEntry,
  PersonaDraft,
  PersonasBootstrap,
} from "../types";

interface Props {
  httpBase: string;
  defaultModel: string;
  disabled: boolean;
  personas: PersonaDraft[];
  onChange: (personas: PersonaDraft[]) => void;
}

let draftSeq = 0;
function newId(): string {
  draftSeq += 1;
  return `draft_${Date.now()}_${draftSeq}`;
}

function blankPersona(model: string): PersonaDraft {
  return {
    id: newId(),
    name: "New crew member",
    role: "Generalist",
    skills: [],
    backstory: "",
    personality: "",
    model,
    temperature: null,
  };
}

function fromCatalog(entry: PersonaCatalogEntry, model: string): PersonaDraft {
  return {
    id: newId(),
    name: entry.name,
    role: entry.role,
    skills: [...entry.skills],
    backstory: entry.backstory,
    personality: entry.personality,
    model: entry.model ?? model,
    temperature: entry.temperature,
  };
}

/**
 * PersonaConfigPanel — pre-game crew editor. Lets the user add, edit, remove
 * personas and assign a per-player model before starting a round.
 */
export function PersonaConfigPanel({
  httpBase,
  defaultModel,
  disabled,
  personas,
  onChange,
}: Props) {
  const [open, setOpen] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [catalog, setCatalog] = useState<PersonaCatalogEntry[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const res = await fetch(`${httpBase}/api/personas`);
        if (!res.ok) throw new Error(`personas request failed: ${res.status}`);
        const data: PersonasBootstrap = await res.json();
        if (cancelled) return;
        setModels(data.models ?? []);
        setCatalog(data.catalog ?? []);
        // Seed the editor from the current default roster the first time.
        if (personas.length === 0 && (data.roster?.length ?? 0) > 0) {
          onChange(
            data.roster.map((p) => ({
              id: newId(),
              name: p.name,
              role: p.role,
              skills: [...(p.skills ?? [])],
              backstory: p.backstory ?? "",
              personality: p.personality ?? "",
              model: p.model ?? data.default_model ?? defaultModel,
              temperature: null,
            }))
          );
        }
      } catch {
        // Leave models/catalog empty; the editor still works with free text.
      } finally {
        if (!cancelled) setLoaded(true);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [httpBase]);

  function update(id: string, patch: Partial<PersonaDraft>) {
    onChange(personas.map((p) => (p.id === id ? { ...p, ...patch } : p)));
  }

  function remove(id: string) {
    onChange(personas.filter((p) => p.id !== id));
  }

  function addBlank() {
    onChange([...personas, blankPersona(defaultModel)]);
  }

  function addFromCatalog(key: string) {
    const entry = catalog.find((c) => c.key === key);
    if (!entry) return;
    onChange([...personas, fromCatalog(entry, defaultModel)]);
  }

  return (
    <section className="persona-config">
      <button
        type="button"
        className="persona-config-toggle"
        onClick={() => setOpen((o) => !o)}
      >
        {open ? "▾" : "▸"} Configure crew ({personas.length})
      </button>

      {open && (
        <div className="persona-config-body">
          <div className="persona-config-actions">
            <button type="button" onClick={addBlank} disabled={disabled}>
              + Add blank
            </button>
            {catalog.length > 0 && (
              <select
                value=""
                disabled={disabled}
                onChange={(e) => {
                  if (e.target.value) addFromCatalog(e.target.value);
                  e.target.value = "";
                }}
              >
                <option value="">+ Add from template…</option>
                {catalog.map((c) => (
                  <option key={c.key} value={c.key}>
                    {c.name} — {c.role}
                  </option>
                ))}
              </select>
            )}
          </div>

          {personas.length === 0 && (
            <p className="muted">
              {loaded
                ? "No personas configured — the backend default roster will be used."
                : "Loading crew…"}
            </p>
          )}

          <ul className="persona-editor-list">
            {personas.map((p, i) => (
              <li key={p.id} className="persona-editor">
                <div className="persona-editor-row">
                  <span className="persona-editor-tag">P{i + 1}</span>
                  <input
                    className="grow"
                    value={p.name}
                    placeholder="name"
                    disabled={disabled}
                    onChange={(e) => update(p.id, { name: e.target.value })}
                  />
                  <button
                    type="button"
                    className="persona-remove"
                    title="Remove"
                    disabled={disabled}
                    onClick={() => remove(p.id)}
                  >
                    ✕
                  </button>
                </div>

                <div className="persona-editor-row">
                  <input
                    className="grow"
                    value={p.role}
                    placeholder="role"
                    disabled={disabled}
                    onChange={(e) => update(p.id, { role: e.target.value })}
                  />
                </div>

                <div className="persona-editor-row">
                  <input
                    className="grow"
                    value={p.skills.join(", ")}
                    placeholder="skills (comma separated)"
                    disabled={disabled}
                    onChange={(e) =>
                      update(p.id, {
                        skills: e.target.value
                          .split(",")
                          .map((s) => s.trim())
                          .filter(Boolean),
                      })
                    }
                  />
                </div>

                <div className="persona-editor-row">
                  <label className="persona-field">
                    model
                    <input
                      list="persona-model-options"
                      value={p.model}
                      placeholder={defaultModel}
                      disabled={disabled}
                      onChange={(e) => update(p.id, { model: e.target.value })}
                    />
                  </label>
                  <label className="persona-field temp">
                    temp
                    <input
                      type="number"
                      step={0.1}
                      min={0}
                      max={2}
                      value={p.temperature ?? ""}
                      placeholder="def"
                      disabled={disabled}
                      onChange={(e) =>
                        update(p.id, {
                          temperature:
                            e.target.value === ""
                              ? null
                              : Number(e.target.value),
                        })
                      }
                    />
                  </label>
                </div>

                <textarea
                  className="persona-editor-text"
                  rows={2}
                  value={p.personality}
                  placeholder="personality / approach"
                  disabled={disabled}
                  onChange={(e) => update(p.id, { personality: e.target.value })}
                />
              </li>
            ))}
          </ul>

          <datalist id="persona-model-options">
            {models.map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
        </div>
      )}
    </section>
  );
}
