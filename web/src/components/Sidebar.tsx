import { useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { motion } from "motion/react";
import BrandMark, { BrandLogo } from "./BrandMark";
import Icon from "./Icon";
import type { IconName } from "./Icon";
import { useMediaQuery } from "../hooks/useMediaQuery";

const STORAGE_KEY = "novelos-sidebar-collapsed";

function loadCollapsed(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export default function Sidebar() {
  const [collapsed, setCollapsed] = useState(loadCollapsed);
  const narrow = useMediaQuery("(max-width: 639px)");
  const compact = collapsed || narrow;
  const location = useLocation();
  const projectMatch = location.pathname.match(/^\/projects\/([^/]+)/);
  const projectPath = projectMatch ? `/projects/${projectMatch[1]}` : "";

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, collapsed ? "1" : "0");
    } catch { /* ignore */ }
  }, [collapsed]);

  return (
    <aside
      className={`glass-rail flex shrink-0 flex-col text-ink-text transition-[width] duration-300 ease-[cubic-bezier(0.2,0.8,0.2,1)] ${
        compact ? "w-[72px]" : "w-[240px]"
      }`}
      data-collapsed={compact ? "true" : "false"}
    >
      <div className={`pt-4 pb-3 ${compact ? "px-2" : "px-4"}`}>
        <div className={`mb-2 flex ${compact ? "justify-center" : "justify-end"}`}>
          {!narrow && (
          <button
            type="button"
            onClick={() => setCollapsed((c) => !c)}
            aria-label={collapsed ? "展开侧边栏" : "收起侧边栏"}
            aria-expanded={!collapsed}
            title={collapsed ? "展开侧边栏" : "收起侧边栏"}
            className="flex h-8 w-8 items-center justify-center rounded-xl text-ink-muted transition-colors hover:bg-white/70 hover:text-ink-text"
          >
            <Icon
              name="chevron-right"
              className={`h-4 w-4 transition-transform duration-300 ${collapsed ? "" : "rotate-180"}`}
            />
          </button>
          )}
        </div>
        {compact ? (
          <div className="flex justify-center">
            <BrandMark className="h-11 w-11" />
          </div>
        ) : (
          <BrandLogo className="h-24 w-auto max-w-[208px] object-contain object-left" />
        )}
      </div>

      {!compact && <div className="mx-4 mb-3 h-px bg-[rgba(74,91,133,0.12)]" />}
      {compact && <div className="mx-3 mb-2 h-px bg-[rgba(74,91,133,0.12)]" />}

      <nav className={`flex flex-col gap-1.5 ${compact ? "px-2" : "px-3"}`}>
        <button
          type="button"
          title="搜索"
          aria-label="搜索"
          onClick={() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", metaKey: true }))}
          className={`flex h-11 items-center rounded-2xl text-[13px] font-medium text-ink-muted transition-colors duration-150 hover:bg-white/70 hover:text-ink-text ${
            compact ? "justify-center px-0" : "justify-between gap-3 px-3.5"
          }`}
        >
          <span className={`flex items-center ${compact ? "" : "gap-2.5"}`}>
            <Icon name="search" className="h-4 w-4" />
            {!compact && "搜索"}
          </span>
          {!compact && (
            <kbd className="rounded-md border border-[rgba(74,91,133,0.14)] bg-white/80 px-1.5 py-0.5 text-[9px] font-medium tracking-wide text-ink-muted shadow-sm">
              ⌘K
            </kbd>
          )}
        </button>
        <SideLink to="/" label="作品库" hint="全部作品" icon="library" end collapsed={compact} />
        <SideLink to="/settings" label="设置" hint="模型配置" icon="sparkles" collapsed={compact} />
        {projectPath && (
          <>
            <div className={`my-1 h-px bg-[rgba(74,91,133,0.12)] ${compact ? "mx-1" : "mx-2"}`} />
            <SideLink to={projectPath} label="作品概览" hint="工作台" icon="layout-dashboard" end collapsed={compact} />
            <SideLink to={`${projectPath}/covers`} label="封面" hint="2K 工作室" icon="image" end collapsed={compact} />
          </>
        )}
      </nav>

      {!compact && (
        <div className="mt-auto px-4 pb-5 pt-6">
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.12, duration: 0.4, ease: [0.2, 0.8, 0.2, 1] }}
            className="glass-card rounded-[20px] px-3.5 py-3.5"
          >
            <p className="text-[12px] leading-relaxed text-ink-muted">
              结构先行，文字随后，连续性贯穿始终。
            </p>
          </motion.div>
        </div>
      )}
      {compact && <div className="mt-auto" />}
    </aside>
  );
}

function SideLink({
  to, label, hint, icon, end, collapsed,
}: {
  to: string; label: string; hint: string; icon: IconName;
  end?: boolean; collapsed?: boolean;
}) {
  return (
    <NavLink
      to={to}
      end={end}
      title={label}
      aria-label={label}
      className={({ isActive }) =>
        `group flex h-11 items-center rounded-2xl transition-all duration-200 ${
          collapsed ? "justify-center px-0" : "justify-between gap-3 px-3.5"
        } ${
          isActive
            ? "bg-ink text-on-ink shadow-[0_10px_24px_rgba(23,33,63,0.18)]"
            : "text-ink-muted hover:bg-white/70 hover:text-ink-text"
        }`
      }
    >
      <span className={`flex items-center ${collapsed ? "" : "gap-2.5"}`}>
        <Icon name={icon} className="h-4 w-4" />
        {!collapsed && (
          <span className="text-[13px] font-semibold tracking-[-0.01em] leading-none">{label}</span>
        )}
      </span>
      {!collapsed && <span className="text-[11px] leading-none opacity-55">{hint}</span>}
    </NavLink>
  );
}
