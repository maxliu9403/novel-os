import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

export default function PosterDialog({title, onClose, children, wide = false}: {
  title: string; onClose: () => void; children: ReactNode; wide?: boolean;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  useEffect(() => { close.current = onClose; }, [onClose]);
  useEffect(() => {
    const original = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const focusable = () => Array.from(panel.current?.querySelectorAll<HTMLElement>(
      'button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex="0"]',
    ) || []).filter(element => !element.hidden);
    focusable()[0]?.focus();
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); close.current(); }
      if (event.key !== "Tab") return;
      const elements = focusable();
      if (!elements.length) { event.preventDefault(); panel.current?.focus(); return; }
      if (event.shiftKey && (document.activeElement === elements[0] || document.activeElement === panel.current)) {
        event.preventDefault(); elements.at(-1)?.focus();
      } else if (!event.shiftKey && document.activeElement === elements.at(-1)) {
        event.preventDefault(); elements[0].focus();
      }
    };
    document.addEventListener("keydown", keyboard);
    return () => { document.body.style.overflow = overflow; document.removeEventListener("keydown", keyboard); original?.focus(); };
  }, []);
  return createPortal(<div className="poster-modal" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <div className={`poster-dialog${wide ? " poster-dialog--wide" : ""}`} ref={panel} tabIndex={-1} role="dialog" aria-modal="true" aria-label={title}>
      <header className="poster-dialog__header"><h2>{title}</h2><button type="button" className="poster-close" aria-label="关闭面板" onClick={onClose}>×</button></header>
      {children}
    </div>
  </div>, document.body);
}
