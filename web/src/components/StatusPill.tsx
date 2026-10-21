import { displayLabel } from "../lib/displayLabels";

export default function StatusPill({ status, label }: { status: string; label?: string }) {
  return (
    <span className="status-pill" data-status={status.toLowerCase()}>
      {label ?? displayLabel(status)}
    </span>
  );
}
