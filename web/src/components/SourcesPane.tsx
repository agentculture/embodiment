// SourcesPane.tsx
//
// The conversation's right pane: every answer's sources, newest answer first,
// and the reader in place of the list once a source opens (pocketchat's side
// reader, culture-library's reader typography). Toggled from the conversation
// bar; a drawer on phones.

import type { ChatSource } from "../api/chat";
import { kindInfo } from "../api/kinds";
import { Reader, type ReaderHandle } from "./Reader";
import type { Ref } from "react";

export interface AnswerSources {
  key: string;
  question: string;
  sources: ChatSource[];
}

export interface SourcesPaneProps {
  answers: AnswerSources[];
  open: { itemId: string; anchor?: string } | null;
  secret?: string;
  readerRef?: Ref<ReaderHandle>;
  onOpen: (itemId: string, anchor: unknown) => void;
  onBack: () => void;
  onClose: () => void;
}

export function SourcesPane({ answers, open, secret, readerRef, onOpen, onBack, onClose }: SourcesPaneProps) {
  if (open) {
    return (
      <div className="side side--reader">
        <div className="side-bar">
          <button type="button" className="pill-btn" onClick={onBack}>
            <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
              <path d="M15 5l-7 7 7 7" />
            </svg>
            All sources
          </button>
        </div>
        <Reader ref={readerRef} id={open.itemId} anchor={open.anchor} secret={secret} onClose={onClose} />
      </div>
    );
  }
  const total = answers.reduce((n, a) => n + a.sources.length, 0);
  return (
    <section className="side" aria-label="Sources">
      <div className="side-bar">
        <h2>Sources</h2>
        <span className="side-count">{total === 0 ? "" : total === 1 ? "1 passage" : `${total} passages`}</span>
        <button type="button" className="icon-btn" aria-label="Hide sources" onClick={onClose}>
          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
            <path d="M9 5l7 7-7 7" />
          </svg>
        </button>
      </div>
      <div className="side-body">
        {total === 0 && (
          <p className="side-empty">
            When Gwen answers from your library, the passages she used appear here. Open one to read it in place.
          </p>
        )}
        {[...answers].reverse().map((a) =>
          a.sources.length === 0 ? null : (
            <section key={a.key} className="side-group">
              <h3 className="side-q" dir="auto">
                {a.question}
              </h3>
              <ol className="side-list">
                {a.sources.map((s, i) => {
                  const k = kindInfo(s.kind || (s.source === "room" ? "diary" : ""));
                  return (
                    <li key={i}>
                      <button
                        type="button"
                        className="side-src"
                        disabled={!s.item_id}
                        onClick={() => s.item_id && onOpen(s.item_id, s.anchor)}
                      >
                        <span className="side-n">{i + 1}</span>
                        <span className="side-title">
                          <span className="dot" style={{ ["--kind" as string]: k.color }} />
                          <span dir="auto">{s.title || (s.source === "room" ? "Gwen's memory" : "Source")}</span>
                        </span>
                        <span className="side-quote" dir="auto">
                          {s.text}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ol>
            </section>
          ),
        )}
      </div>
    </section>
  );
}
