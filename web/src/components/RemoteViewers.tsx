import type { EventEnvelope } from "../api/events";

export interface RemoteViewersProps {
  clients: EventEnvelope<"clients"> | null;
}

export function RemoteViewers({ clients }: RemoteViewersProps) {
  if (!clients) {
    return <span data-remote-viewers="unknown">viewers: unknown</span>;
  }
  const count = Number(clients.data.count ?? 0);
  const remote = Number(clients.data.remote ?? 0);
  return (
    <span data-remote-viewers={remote > 0 ? "present" : "none"}>
      {count} viewer{count === 1 ? "" : "s"}
      {remote > 0 ? ` (${remote} remote)` : ""}
    </span>
  );
}
