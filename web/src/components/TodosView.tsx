import { useEffect, useState } from "react";
import { fetchTodos, setTodoDone, type TodoGroup, type TodosClientOptions } from "../api/todos";

// Ported from pocketsync/web/static/app.js:740-766 (showTodos/todoRow):
// action items grouped by recording, a "Untitled recording" fallback title.
// Checkable (d16): the done state is Gwen's, kept by the daemon; culture-rules still
// writes the to-dos. Gwen can mark them too when asked in chat.
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
  const [failed, setFailed] = useState<string | null>(null);

  async function toggle(id: string, done: boolean) {
    if (state.kind !== "ready" || !id) return;
    const before = state.groups;
    const flip = (groups: TodoGroup[]) =>
      groups.map((g) => ({ ...g, items: g.items.map((it) => (it.id === id ? { ...it, done, doneBy: "operator" } : it)) }));
    setState({ kind: "ready", groups: flip(before) });
    const out = await setTodoDone(id, done, secret, clientOptions);
    if (out.ok) {
      setFailed(null);
    } else {
      setState({ kind: "ready", groups: before });
      setFailed(out.code);
    }
  }
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
      {failed && (
        <p className="library-error" role="alert">
          That to-do could not be marked ({failed}).
        </p>
      )}
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
                <li key={i} className="todos__item" data-done={it.done ? "true" : undefined}>
                  <label className="todos__check">
                    <input
                      type="checkbox"
                      checked={it.done}
                      disabled={!it.id}
                      onChange={(e) => void toggle(it.id, e.target.checked)}
                    />
                    <span className="todos__text" dir="auto">
                      {it.text}
                    </span>
                  </label>
                  {it.done && it.doneBy === "gwen" && <span className="todos__by">marked by Gwen</span>}
                </li>
              ))}
            </ul>
          </section>
        ))}
    </section>
  );
}
