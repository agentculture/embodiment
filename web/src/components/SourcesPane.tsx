// SourcesPane.tsx
//
// The right pane of Ask (operator, 2026-10-24: "library and resources on the
// right"): two tabs, the answers' Sources (newest answer first) and the
// Library list, and the reader in place of either once a source or an item
// opens (pocketchat's side reader, culture-library's reader typography), with
// a way back to the tab it came from. A drawer on phones.

import type { ReactNode, Ref } from "react";
import type { ChatSource } from "../api/chat";
import { kindInfo } from "../api/kinds";
import { Reader, type ReaderHandle } from "./Reader";

export interface AnswerSources {
  key: string;
  question: string;
  sources: ChatSource[];
}

export type SideTab = "sources" | "library";

export interface SourcesPaneProps {
  answers: AnswerSources[];
  open: { itemId: string; anchor?: string } | null;
  tab: SideTab;
  onTab: (tab: SideTab) => void;
  /** The Library tab's content (the library list). */
  library: ReactNode;
  secret?: string;
  readerRef?: Ref<ReaderHandle>;
  onOpen: (itemId: string, anchor: unknown) => void;
  onBack: () => void;
  onClose: () => void;
}

/** A page fetch_source read: it opens in a new tab, not in the reader. */
function isWeb(s: ChatSource): boolean {
  return s.source === "web" && /^https?:\/\//i.test(s.link || "");
}

const TAB_LABEL: Record<SideTab, string> = { sources: "Sources", library: "Library" };

function SourceList({ answers, onOpen }: Pick<SourcesPaneProps, "answers" | "onOpen">) {
  const total = answers.reduce((n, a) => n + a.sources.length, 0);
  if (total === 0) {
    return (
      <p className="side-empty">
        When Gwen answers from your library, the passages she used appear here. Open one to read it in place.
      </p>
    );
  }
  return (
    <>
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
                      disabled={!s.item_id && !isWeb(s)}
                      onClick={() =>
                        isWeb(s)
                          ? window.open(s.link, "_blank", "noopener,noreferrer")
                          : s.item_id && onOpen(s.item_id, s.anchor)
                      }
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
    </>
  );
}

export function SourcesPane({
  answers,
  open,
  tab,
  onTab,
  library,
  secret,
  readerRef,
  onOpen,
  onBack,
  onClose,
}: SourcesPaneProps) {
  if (open) {
    return (
      <div className="side side--reader">
        <div className="side-bar">
          <button type="button" className="pill-btn" onClick={onBack}>
            <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
              <path d="M15 5l-7 7 7 7" />
            </svg>
            {tab === "sources" ? "All sources" : "Library"}
          </button>
        </div>
        <Reader ref={readerRef} id={open.itemId} anchor={open.anchor} secret={secret} onClose={onClose} />
      </div>
    );
  }
  const total = answers.reduce((n, a) => n + a.sources.length, 0);
  return (
    <section className="side" aria-label="Library and sources">
      <div className="side-bar">
        <div className="side-tabs" role="tablist" aria-label="Library and sources">
          {(["sources", "library"] as SideTab[]).map((t) => (
            <button
              key={t}
              type="button"
              role="tab"
              aria-selected={tab === t}
              className="side-tab"
              onClick={() => onTab(t)}
            >
              {TAB_LABEL[t]}
              {t === "sources" && total > 0 && <span className="side-tab-count">{total}</span>}
            </button>
          ))}
        </div>
        <button type="button" className="icon-btn" aria-label="Hide the side pane" onClick={onClose}>
          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
            <path d="M9 5l7 7-7 7" />
          </svg>
        </button>
      </div>
      {tab === "sources" ? (
        <div className="side-body" role="tabpanel" aria-label="Sources">
          <SourceList answers={answers} onOpen={onOpen} />
        </div>
      ) : (
        <div className="side-library" role="tabpanel" aria-label="Library">
          {library}
        </div>
      )}
    </section>
  );
}
