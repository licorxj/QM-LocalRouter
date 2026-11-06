import React, { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { useI18n } from "../../i18n";
import { getVersion } from "../../services/api";
import { useShell } from "../../stores/shell";
import { useNavItems } from "./nav";
import { Tooltip, TooltipContent, TooltipTrigger } from "../ui/tooltip";
import { cn } from "@/lib/utils";

function VersionFooter() {
  const [versionInfo, setVersionInfo] = useState<any>(null);

  useEffect(() => {
    getVersion().then((r) => setVersionInfo(r.data)).catch(() => {});
  }, []);

  return (
    <div className="flex items-center gap-1.5 font-mono text-[11px] tracking-wider text-muted-foreground/70">
      {versionInfo ? (
        <>
          <span>v{versionInfo.version}</span>
          {versionInfo.git_commit && (
            <span className="opacity-70">#{String(versionInfo.git_commit).slice(0, 7)}</span>
          )}
          {versionInfo.build_type === "release" && (
            <span className="rounded bg-primary/12 px-1 text-primary">rel</span>
          )}
        </>
      ) : (
        <span>v1.0.0</span>
      )}
    </div>
  );
}

type SidebarContentProps = {
  collapsed: boolean;
  /** Mobile drawers close after navigation; desktop ignores this. */
  onNavigate?: () => void;
};

function SidebarContent({ collapsed, onNavigate }: SidebarContentProps) {
  const { t } = useI18n();
  const navItems = useNavItems();
  const { toggleCollapsed } = useShell();

  return (
    <>
      {/* Logo */}
      <div
        className={cn(
          "flex h-14 flex-none items-center gap-2.5 border-b border-border px-4",
          collapsed && "justify-center px-0"
        )}
      >
        <img
          src="/logo.png"
          alt="LocalRouter"
          className="h-8 w-8 flex-none object-contain"
        />
        {!collapsed && (
          <div className="min-w-0">
            <h1 className="truncate text-sm font-bold tracking-tight text-foreground">
              LocalRouter
            </h1>
            <p className="truncate text-[10px] font-semibold uppercase tracking-[0.14em] text-primary/80">
              {t("sidebar.tagline")}
            </p>
          </div>
        )}
        {!collapsed && (
          <button
            type="button"
            onClick={toggleCollapsed}
            aria-label={t("sidebar.collapse")}
            title={t("sidebar.collapse")}
            className="ml-auto hidden rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-foreground/5 hover:text-foreground md:block"
          >
            <PanelLeftClose className="h-4 w-4" aria-hidden="true" />
          </button>
        )}
      </div>

      {/* Navigation */}
      <nav className={cn("flex-1 space-y-0.5 overflow-y-auto py-3", collapsed ? "px-2" : "px-3")}>
        {navItems.map((item) => {
          const link = (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              onClick={onNavigate}
              className={({ isActive }) =>
                cn(
                  "group relative flex items-center rounded-lg text-sm transition-colors",
                  collapsed ? "h-10 justify-center px-0" : "gap-3 px-3 py-2 font-medium",
                  isActive
                    ? "bg-primary/10 font-semibold text-primary"
                    : "text-sidebar-foreground hover:bg-foreground/5 hover:text-foreground"
                )
              }
            >
              {({ isActive }) => (
                <>
                  {isActive && (
                    <span
                      aria-hidden="true"
                      className={cn(
                        "absolute top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r-full bg-primary",
                        collapsed ? "left-0" : "-left-3"
                      )}
                    />
                  )}
                  <item.icon
                    className={cn("h-4 w-4 flex-none", !isActive && "text-muted-foreground group-hover:text-foreground")}
                    strokeWidth={isActive ? 2.4 : 2}
                    aria-hidden="true"
                  />
                  {!collapsed && <span className="truncate">{item.label}</span>}
                </>
              )}
            </NavLink>
          );

          return collapsed ? (
            <Tooltip key={item.to}>
              <TooltipTrigger asChild>{link}</TooltipTrigger>
              <TooltipContent side="right">{item.label}</TooltipContent>
            </Tooltip>
          ) : (
            link
          );
        })}
      </nav>

      {/* Footer */}
      <div
        className={cn(
          "flex flex-none items-center gap-2 border-t border-border px-4 py-3",
          collapsed && "justify-center px-0"
        )}
      >
        {!collapsed && <VersionFooter />}
        <button
          type="button"
          onClick={() => (collapsed ? toggleCollapsed() : useShell.getState().setMobileOpen(false))}
          aria-label={collapsed ? t("sidebar.expand") : t("sidebar.closeMenu")}
          className={cn(
            "rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-foreground/5 hover:text-foreground",
            collapsed ? "md:block" : "md:hidden"
          )}
        >
          <PanelLeftOpen className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
    </>
  );
}

export default function Sidebar() {
  const { collapsed, mobileOpen, setMobileOpen } = useShell();

  return (
    <>
      {/* Desktop rail */}
      <aside
        className={cn(
          "sidebar-gradient relative z-40 hidden h-full flex-none flex-col transition-[width] duration-200 md:flex",
          collapsed ? "w-[68px]" : "w-60"
        )}
      >
        <SidebarContent collapsed={collapsed} />
      </aside>

      {/* Mobile drawer */}
      <div
        className={cn(
          "fixed inset-0 z-40 bg-black/60 backdrop-blur-sm transition-opacity md:hidden",
          mobileOpen ? "opacity-100" : "pointer-events-none opacity-0"
        )}
        onClick={() => setMobileOpen(false)}
        aria-hidden="true"
      />
      <aside
        className={cn(
          "sidebar-gradient fixed inset-y-0 left-0 z-50 flex w-60 flex-col transition-transform duration-200 md:hidden",
          mobileOpen ? "translate-x-0" : "-translate-x-full"
        )}
        aria-hidden={!mobileOpen}
      >
        <SidebarContent collapsed={false} onNavigate={() => setMobileOpen(false)} />
      </aside>
    </>
  );
}
