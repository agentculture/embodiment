// components/DockMeta.tsx
//
// The settings row under the dock (gwen-pm t16, mockup v2): Persona, Language and Model,
// each a small button that opens a menu upward. A choice is reported with onChange and
// applies from the next turn. English labels sit OUTSIDE every dir="auto" span: a hidden
// English prefix inside dir=auto once flipped Hebrew to LTR. The Model control turns amber
// while the chosen role cannot answer; one tap on another ready role switches.
import { useEffect, useRef, useState } from "react";
import { voiceLabel, type ConversationSettings, type Language, type ModelsInfo } from "../api/models";
import type { Persona } from "../api/personas";

export interface DockMetaProps {
  personas: Persona[];
  builtinId: string;
  models: ModelsInfo | null;
  settings: ConversationSettings;
  onChange: (patch: ConversationSettings) => void;
  /** The role that could not answer (turns the Model control amber), or null/undefined. */
  unavailable?: string | null;
  onRetry?: () => void;
}

type MenuId = "persona" | "lang" | "model";

const LANGUAGES: { id: Language; label: string; tag: string; ready: boolean; note: string }[] = [
  { id: "he", label: "עברית", tag: "voice", ready: true, note: "spoken and typed replies" },
  { id: "en", label: "English", tag: "text only", ready: false, note: "lobes has no English voice yet" },
  { id: "none", label: "None", tag: "no rule", ready: false, note: "no language line: the model answers in whatever language fits" },
];

interface Opt {
  id: string;
  label: string;
  tag: string;
  tagTone?: "ready";
  detail: string;
  disabled?: boolean;
}

function Menu({
  id,
  title,
  note,
  options,
  selected,
  footer,
  onPick,
  left,
}: {
  id: string;
  title: string;
  note?: string;
  options: Opt[];
  selected: string;
  footer?: string;
  onPick: (id: string) => void;
  left: number;
}) {
  return (
    <div className="pop" role="listbox" aria-label={title} id={id} style={{ insetInlineStart: left }}>
      <header>
        <b>{title}</b>
        {note && <small dir="auto">{note}</small>}
      </header>
      {options.map((o) => (
        <button
          key={o.id}
          type="button"
          role="option"
          className="opt"
          aria-selected={o.id === selected}
          aria-disabled={o.disabled || undefined}
          onClick={() => {
            if (!o.disabled) onPick(o.id);
          }}
        >
          <span className="radio" aria-hidden="true" />
          <b dir="auto">{o.label}</b>
          <span className={`state${o.tagTone ? ` ${o.tagTone}` : ""}`}>{o.tag}</span>
          <span className="model" dir="auto">
            {o.detail}
          </span>
        </button>
      ))}
      {footer && <footer>{footer}</footer>}
    </div>
  );
}

export function DockMeta({ personas, builtinId, models, settings, onChange, unavailable, onRetry }: DockMetaProps) {
  const [open, setOpen] = useState<MenuId | null>(null);
  const [left, setLeft] = useState(0);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(null);
    };
    const onDown = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(null);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  const toggle = (id: MenuId, el: HTMLElement) => {
    setLeft(Math.max(0, el.offsetLeft - 8));
    setOpen((cur) => (cur === id ? null : id));
  };

  const personaId = settings.persona || builtinId;
  const persona = personas.find((p) => p.id === personaId) ?? null;
  const language = settings.language ?? (persona?.language as Language | undefined) ?? "he";
  const langInfo = LANGUAGES.find((l) => l.id === language) ?? LANGUAGES[0];
  const modelRole = settings.model || persona?.default_role || models?.conversationRole || "";

  const pick = (patch: ConversationSettings) => {
    onChange(patch);
    setOpen(null);
  };

  const personaOpts: Opt[] = personas.map((p) => ({
    id: p.id,
    label: p.name,
    tag: p.id === builtinId ? "built in" : "yours",
    detail: [p.length !== "unset" ? p.length : null, p.default_role || null, p.language === "en" ? "text only" : null]
      .filter(Boolean)
      .join(" · ") || "default",
  }));
  const langOpts: Opt[] = LANGUAGES.map((l) => ({
    id: l.id, label: l.label, tag: l.tag, tagTone: l.ready ? "ready" : undefined, detail: l.note,
  }));
  const modelOpts: Opt[] = (models?.roles ?? []).map((r) => ({
    id: r.role,
    label: r.role,
    tag: r.ready ? "ready" : "not ready",
    tagTone: r.ready ? "ready" : undefined,
    detail: [r.model, r.host || null, voiceLabel(r)].filter(Boolean).join(" · "),
    disabled: !r.ready,
  }));

  const sep = <span className="meta-sep" aria-hidden="true" />;
  return (
    <div className="dock-meta" aria-label="Conversation settings" ref={root}>
      <button
        type="button"
        className="meta-btn"
        aria-haspopup="listbox"
        aria-expanded={open === "persona"}
        onClick={(e) => toggle("persona", e.currentTarget)}
      >
        <span className="k">Persona</span> <span dir="auto">{persona?.name ?? "Gwen"}</span>{" "}
        <span className="caret" aria-hidden="true">▾</span>
      </button>
      {sep}
      <button
        type="button"
        className="meta-btn"
        aria-haspopup="listbox"
        aria-expanded={open === "lang"}
        onClick={(e) => toggle("lang", e.currentTarget)}
      >
        <span className="k">Language</span> <span dir="auto">{langInfo.label}</span>{" "}
        <span className="caret" aria-hidden="true">▾</span>
      </button>
      {sep}
      <button
        type="button"
        className={`meta-btn${unavailable ? " warn" : ""}`}
        aria-haspopup="listbox"
        aria-expanded={open === "model"}
        onClick={(e) => toggle("model", e.currentTarget)}
      >
        <span className="k">Model</span> <span dir="auto">{modelRole || "default"}</span>
        {unavailable ? <span> · unavailable</span> : null}{" "}
        <span className="caret" aria-hidden="true">▾</span>
      </button>
      {unavailable && onRetry && (
        <>
          {sep}
          <button type="button" className="meta-btn" onClick={onRetry}>
            Retry
          </button>
        </>
      )}

      {open === "persona" && (
        <Menu
          id="pop-persona"
          title="Persona"
          note="edit in the Personas tab"
          options={personaOpts}
          selected={personaId}
          onPick={(id) => pick({ persona: id })}
          left={left}
        />
      )}
      {open === "lang" && (
        <Menu
          id="pop-lang"
          title="Language"
          note={persona ? `persona default: ${LANGUAGES.find((l) => l.id === persona.language)?.label ?? persona.language}` : undefined}
          options={langOpts}
          selected={language}
          onPick={(id) => pick({ language: id as Language })}
          left={left}
        />
      )}
      {open === "model" && (
        <Menu
          id="pop-model"
          title="Reply model"
          note={models ? `lobes · ${models.stale ? "stale, " : ""}checked ${models.ageS === null ? "just now" : `${Math.round(models.ageS)} s ago`}` : undefined}
          options={modelOpts}
          selected={modelRole}
          footer={models ? "Only what lobes serves. Applies from the next turn." : "The model list is not available right now."}
          onPick={(id) => pick({ model: id })}
          left={left}
        />
      )}
    </div>
  );
}
