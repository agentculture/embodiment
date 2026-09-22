import type { ConnectionStatus as Status } from "../hooks/useEventStream";

const LABEL: Record<Status, string> = {
  connecting: "connecting",
  connected: "connected",
  disconnected: "disconnected",
};

export function ConnectionStatus({ status }: { status: Status }) {
  return (
    <span className="status-pill" data-status={status} role="status">
      <span className="status-pill__dot" aria-hidden="true" />
      {LABEL[status]}
    </span>
  );
}
