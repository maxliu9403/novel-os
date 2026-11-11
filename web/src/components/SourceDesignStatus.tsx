import type { SourceDesignState } from "../api/sourceBooks";
import { designStatusLabels, sourceModeLabels } from "../lib/sourceDesignStatus";

export default function SourceDesignStatus({ design }: { design?: SourceDesignState }) {
  if (!design) return null;
  const label = designStatusLabels[design.status];
  return (
    <span className="source-design-state">
      <span className={`source-design-badge is-${design.status}`} aria-label={`整书设计：${label}`}>
        <span aria-hidden="true" className="source-design-dot" />
        {label}
        {design.active_count > 1 && <span> · {design.active_count} 项</span>}
      </span>
      {design.mode && <small className="source-design-progress">
        {sourceModeLabels[design.mode]}
        {design.total > 0 ? ` · 已保存 ${design.completed}/${design.total} 步` : ""}
      </small>}
    </span>
  );
}
