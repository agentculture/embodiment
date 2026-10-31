// components/PersonasView.tsx
//
// The Personas tab (gwen-pm t16, mockup v2): the same list-and-detail shape as Library.
// A list of personas (the built-in one read-only, "Duplicate" to change it), an editor for
// every persona field, and "What Gwen will be told". There is NO server endpoint for the
// composed prompt and none is re-implemented here: the persona's own text is shown verbatim
// (green, "yours"), its language and length as setting labels, and the fixed sections by
// name only (grey). Every text node is dir="auto"; English labels stay outside it.
import { useCallback, useEffect, useState } from "react";
import type { ModelsInfo } from "../api/models";
import {
  deletePersona,
  listPersonas,
  savePersona,
  type Persona,
  type PersonaFields,
  type PersonasClientOptions,
} from "../api/personas";

export interface PersonasViewProps {
  secret?: string;
  models: ModelsInfo | null;
  clientOptions?: PersonasClientOptions;
  /** Called after the list changes, so the dock row can show the new names. */
  onChanged?: () => void;
  /** "Start a conversation with it": choose this persona for the live conversation. */
  onUse?: (personaId: string) => void;
}

const NEW = "__new__";
const LANGS: [string, string][] = [["he", "עברית"], ["en", "English"], ["none", "None"]];
const LENGTHS: [string, string][] = [
  ["brief", "Brief"], ["conversational", "Conversational"], ["detailed", "Detailed"], ["unset", "Unset"],
];
const FIXED: { name: string; who: string; note: string; off?: boolean }[] = [
  { name: "Clock", who: "fixed", note: "The time on the rig." },
  { name: "Memory and tool rules", who: "fixed, measured", note: "remember / forget, library, conversations, to-dos." },
  { name: "The conversation so far", who: "fixed", note: "Recent turns." },
  { name: "Recalled memory", who: "fixed, quoted as data", note: "Attributed and fenced, never instructions." },
  { name: "Speakability rules", who: "voice turns only", note: "No lists or markup when the reply is spoken.", off: true },
];

const blank = (role: string): PersonaFields => ({
  name: "", language: "he", length: "unset", allow_formatting: false,
  translate_via: null, mesh_enabled: false, text: "", default_role: role,
});

function fieldsOf(p: Persona): PersonaFields {
  const { id: _id, ...rest } = p;
  return rest;
}

const label = (pairs: [string, string][], id: string) => pairs.find(([k]) => k === id)?.[1] ?? id;

function Switch({ on, name, hint, onToggle, disabled }: {
  on: boolean; name: string; hint: string; onToggle: () => void; disabled: boolean;
}) {
  return (
    <div className="toggle">
      <button type="button" className="sw" role="switch" aria-checked={on} aria-label={name}
        disabled={disabled} onClick={onToggle} />
      <div>
        <b>{name}</b>
        <small>{hint}</small>
      </div>
    </div>
  );
}

function Seg({ labelId, value, options, onPick, disabled }: {
  labelId: string; value: string; options: [string, string][]; onPick: (v: string) => void; disabled: boolean;
}) {
  return (
    <div className="seg" role="group" aria-labelledby={labelId}>
      {options.map(([id, text]) => (
        <button key={id} type="button" aria-pressed={value === id} disabled={disabled} onClick={() => onPick(id)}>
          {text}
        </button>
      ))}
    </div>
  );
}

export function PersonasView({ secret = "", models, clientOptions, onChanged, onUse }: PersonasViewProps) {
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [builtinId, setBuiltinId] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string>("");
  const [draft, setDraft] = useState<PersonaFields | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    const out = await listPersonas(secret, clientOptions);
    if (!out.ok) {
      setLoadError(out.code);
      return;
    }
    setLoadError(null);
    setPersonas(out.personas);
    setBuiltinId(out.builtinId);
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [secret, clientOptions?.fetchFn, clientOptions?.basePath]);

  useEffect(() => {
    void load().then((out) => {
      if (out && out.personas.length) {
        const first = out.personas.find((p) => p.id === out.builtinId) ?? out.personas[0];
        setSelected((cur) => cur || first.id);
        setDraft((cur) => cur ?? fieldsOf(first));
      }
    });
  }, [load]);

  const roles = models?.roles ?? [];
  const defaultRole = models?.conversationRole || roles[0]?.role || "";
  const isNew = selected === NEW;
  const readOnly = !isNew && selected === builtinId;
  const set = <K extends keyof PersonaFields>(k: K, v: PersonaFields[K]) =>
    setDraft((d) => (d ? { ...d, [k]: v } : d));

  function choose(p: Persona) {
    setSelected(p.id);
    setDraft(fieldsOf(p));
    setError(null);
    setNote(null);
  }

  function startNew(from?: PersonaFields) {
    setSelected(NEW);
    setDraft(from ? { ...from, name: `${from.name} copy` } : blank(defaultRole));
    setError(null);
    setNote(null);
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!draft || readOnly) return;
    setError(null);
    setNote(null);
    const out = await savePersona(isNew ? undefined : selected, draft, secret, clientOptions);
    if (!out.ok) {
      setError(out.code);
      return;
    }
    setNote("Saved.");
    setSelected(out.persona.id);
    setDraft(fieldsOf(out.persona));
    await load();
    onChanged?.();
  }

  async function remove() {
    if (isNew || readOnly) return;
    const out = await deletePersona(selected, secret, clientOptions);
    if (!out.ok) {
      setError(out.code);
      return;
    }
    const keep = personas.find((p) => p.id === builtinId) ?? personas[0];
    if (keep) choose(keep);
    await load();
    onChanged?.();
  }

  const sel = personas.find((p) => p.id === selected);
  const showBuiltinNote = readOnly;

  return (
    <div className="editor">
      {loadError && (
        <p className="library-error" role="alert">
          The personas could not be read ({loadError}).
        </p>
      )}
      <nav className="list" aria-label="Personas">
        <button type="button" className="pill-btn conv-new" onClick={() => startNew()}>
          New persona
        </button>
        {personas.map((p) => (
          <button
            key={p.id}
            type="button"
            className="row"
            aria-current={p.id === selected ? "true" : undefined}
            onClick={() => choose(p)}
          >
            <span className={`conv-dot${p.id === builtinId ? "" : " conv-dot--past"}`} aria-hidden="true" />
            <span className="row-title" dir="auto">{p.name}</span>
            <span className="row-side">{p.id === builtinId ? "built in" : "yours"}</span>
          </button>
        ))}
      </nav>

      {draft ? (
        <>
          <form className="panel" onSubmit={save}>
            <div className="panel__head">
              <h3 dir="auto">{draft.name || "New persona"}</h3>
              <span className="hint">{readOnly ? "built in, read-only" : "saved on the rig, private"}</span>
            </div>
            <div className="row2">
              <div className="field">
                <label htmlFor="p-name">Name</label>
                <input id="p-name" type="text" dir="auto" value={draft.name} disabled={readOnly}
                  onChange={(e) => set("name", e.target.value)} />
              </div>
              <div className="field">
                <label htmlFor="p-model">Default model</label>
                <select id="p-model" value={draft.default_role} disabled={readOnly}
                  onChange={(e) => set("default_role", e.target.value)}>
                  {!roles.some((r) => r.role === draft.default_role) && (
                    <option value={draft.default_role}>{draft.default_role || "default"}</option>
                  )}
                  {roles.map((r) => (
                    <option key={r.role} value={r.role}>{`${r.role} · ${r.model}${r.ready ? "" : " (not ready)"}`}</option>
                  ))}
                </select>
              </div>
            </div>
            <div className="field">
              <span className="lbl" id="lang-l">Default language</span>
              <Seg labelId="lang-l" value={draft.language} options={LANGS} disabled={readOnly}
                onPick={(v) => set("language", v)} />
              <small>None sends no language line. English is text only: lobes has no English voice yet.</small>
            </div>
            <div className="field">
              <span className="lbl" id="len-l">Length</span>
              <Seg labelId="len-l" value={draft.length} options={LENGTHS} disabled={readOnly}
                onPick={(v) => set("length", v)} />
              <small>One line of guidance; Unset adds none. A spoken reply still stops at the voice cap (4,000 characters).</small>
            </div>
            <div className="field">
              <span className="lbl">Options</span>
              <Switch on={draft.allow_formatting} disabled={readOnly} name="Lists and formatting in typed replies"
                hint="Spoken replies stay plain speech." onToggle={() => set("allow_formatting", !draft.allow_formatting)} />
              <Switch on={draft.translate_via === "senses"} disabled={readOnly} name="Translate through senses (Gemma)"
                hint="Hebrew to English for the model and back. Two extra senses calls a turn; latency not yet measured."
                onToggle={() => set("translate_via", draft.translate_via === "senses" ? null : "senses")} />
              <Switch on={draft.mesh_enabled} disabled={readOnly} name="May ask Culture chat on her own judgement"
                hint="Saved with the persona; the mesh itself is a separate change." onToggle={() => set("mesh_enabled", !draft.mesh_enabled)} />
            </div>
            <div className="field">
              <label htmlFor="p-text">How this persona talks</label>
              <textarea id="p-text" dir="auto" value={draft.text} disabled={readOnly}
                onChange={(e) => set("text", e.target.value)} />
              <small>Sent as written.</small>
            </div>
            {error && <p className="chat__degraded" role="alert">Could not save ({error}).</p>}
            {note && !error && <p className="hint" role="status">{note}</p>}
            <div className="panel__actions">
              {readOnly ? (
                <button type="button" className="btn" onClick={() => startNew(draft)}>Duplicate to change it</button>
              ) : (
                <button type="submit" className="btn primary">Save persona</button>
              )}
              {!isNew && sel && onUse && (
                <button type="button" className="btn" onClick={() => onUse(sel.id)}>Start a conversation with it</button>
              )}
              {!isNew && !readOnly && (
                <button type="button" className="btn" onClick={() => void remove()}>Delete</button>
              )}
            </div>
          </form>

          <div className="panel" aria-label="Composed prompt preview">
            <div className="panel__head">
              <h3>What Gwen will be told</h3>
              <span className="hint">typed turn, in order</span>
            </div>
            <div className="prompt">
              {draft.text && (
                <div className="sec mine">
                  <header>How this persona talks<span className="who">yours</span></header>
                  <p dir="auto">{draft.text}</p>
                </div>
              )}
              <div className="sec mine">
                <header>Language and length<span className="who">yours</span></header>
                <p>
                  <span dir="auto">{label(LANGS, draft.language)}</span>
                  {" · "}
                  <span>{label(LENGTHS, draft.length)}</span>
                </p>
              </div>
              {FIXED.map((f) => (
                <div key={f.name} className={`sec ${f.off ? "off" : "fixed"}`}>
                  <header>{f.name}<span className="who">{f.who}</span></header>
                  <p>{f.note}</p>
                </div>
              ))}
            </div>
            <small className="hint">
              {showBuiltinNote
                ? "Built-in Gwen's spoken prompt is today's prompt byte for byte. Duplicate it to change it."
                : "Built-in Gwen's spoken prompt is today's prompt byte for byte; a persona changes only the green parts."}
            </small>
          </div>
        </>
      ) : (
        !loadError && <p className="hint">Loading personas.</p>
      )}
    </div>
  );
}
