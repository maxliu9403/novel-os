import { displayLabel } from "../lib/displayLabels";

export default function StatusPill({ status }: { status: string }) {
  return (
    <span className="status-pill" data-status={status.toLowerCase()}>
      {displayLabel(status)}
    </span>
  );
}
