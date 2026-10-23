import { useEffect, useState } from "react";
import { fetchTodos, type TodoGroup, type TodosClientOptions } from "../api/todos";

// Ported from pocketsync/web/static/app.js:740-766 (showTodos/todoRow):
// action items grouped by recording, a "Untitled recording" fallback title.
// Read-only: the pocketsync done/undone toggle is not ported (c40).
export const TODOS_PROVENANCE = "Ported from pocketsync/web/static/app.js:740-766";
export const TODOS_EMPTY_MESSAGE = "No action items in your recordings yet.";
export const TODOS_UNAVAILABLE_MESSAGE =
  "To-dos are unavailable right now: the recordings library or memory is not available.";
export const TODOS_ERROR_MESSAGE = "To-dos could not be loaded.";

export interface TodosViewProps {
  /** Install secret the page already holds; sent only when present. */
  secret?: string;
  clientOptions?: TodosClientOptions;
}

type State =
  | { kind: "loading" }
  | { kind: "ready"; groups: TodoGroup[] }
  | { kind: "unavailable" }
  | { kind: "error" };

export function TodosView({ secret = "", clientOptions }: TodosViewProps) {
  const [state, setState] = useState<State>({ kind: "loading" });
  useEffect(() => {
    let live = true;
    void fetchTodos(secret, clientOptions).then((out) => {
      if (!live) return;
      if (out.ok) setState({ kind: "ready", groups: out.groups });
      else setState({ kind: out.status === 503 ? "unavailable" : "error" });
    });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [secret]);

  return (
    <section className="todos" aria-label="to-dos">
      <p className="todos__provenance">{TODOS_PROVENANCE}</p>
      {state.kind === "loading" && <p className="todos__loading">Loading to-dos</p>}
      {state.kind === "unavailable" && <p className="todos__none">{TODOS_UNAVAILABLE_MESSAGE}</p>}
      {state.kind === "error" && <p className="todos__none">{TODOS_ERROR_MESSAGE}</p>}
      {state.kind === "ready" && state.groups.length === 0 && (
        <p className="todos__none">{TODOS_EMPTY_MESSAGE}</p>
      )}
      {state.kind === "ready" &&
        state.groups.map((g, gi) => (
          <section key={`${g.recordingId}-${gi}`} className="todos__group">
            <h3 className="todos__source">
              <strong dir="auto">{g.title || "Untitled recording"}</strong>
              {g.date && <span dir="auto"> {g.date}</span>}
            </h3>
            <ul className="todos__items">
              {g.items.map((it, i) => (
                <li key={i} className="todos__item">
                  <span className="todos__text" dir="auto">
                    {it.text}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        ))}
    </section>
  );
}
