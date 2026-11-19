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
    <div className="flex min-w-0 items-center gap-2 rounded-lg border border-border/60 bg-foreground/[0.025] px-2 py-1 font-mono text-[11px] tracking-wide text-muted-foreground/80">
      <span className="status-led status-led-orange pulse-dot" aria-hidden="true" />
      <span className="truncate">
        {versionInfo ? (
          <>
            v{versionInfo.version}
            {versionInfo.git_commit && (
              <span className="opacity-70">#{String(versionInfo.git_commit).slice(0, 7)}</span>
            )}
            {versionInfo.build_type === "release" && (
              <span className="ml-1 rounded bg-primary/12 px-1 text-primary">rel</span>
            )}
          </>
        ) : (
          "v1.0.0"
        )}
      </span>
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

  const groups = navItems.reduce<{ section: string; items: typeof navItems }[]>((acc, item) => {
    let group = acc.find((g) => g.section === item.section);
    if (!group) {
      group = { section: item.section, items: [] };
      acc.push(group);
    }
    group.items.push(item);
    return acc;
  }, []);

  return (
    <>
      {/* Brand */}
      <div
        className={cn(
          "relative z-10 flex h-16 flex-none items-center gap-2.5 border-b border-border/70 px-4",
          collapsed && "justify-center px-0"
        )}
      >
        <span className="relative flex h-9 w-9 flex-none items-center justify-center">
          <span className="nav-logo-halo" aria-hidden="true" />
          <img
            src="/logo.png"
            alt="LocalRouter"
            className="nav-logo relative h-9 w-9 object-contain"
          />
        </span>
        {!collapsed && (
          <div className="min-w-0">
            <h1 className="truncate text-lg font-bold tracking-tight">
              <span className="gradient-text">Local</span>
              <span className="text-route-blue">Router</span>
            </h1>
            <p className="mt-0.5 truncate text-[10px] font-semibold tracking-[0.16em] text-primary/75">
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
            className="ml-auto hidden rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-foreground/[0.06] hover:text-foreground md:block"
          >
            <PanelLeftClose className="h-4 w-4" aria-hidden="true" />
          </button>
        )}
      </div>

      {/* Navigation */}
      <nav
        className={cn(
          "relative z-10 flex-1 overflow-y-auto py-2",
          collapsed ? "px-2" : "px-3"
        )}
      >
        {groups.map((group, gi) => (
          <div key={group.section} className={gi > 0 ? "mt-4" : ""}>
            {collapsed ? (
              <div className="mx-auto mb-2 h-px w-6 bg-border/70" aria-hidden="true" />
            ) : (
              <div className="nav-section-label">{t(`sidebar.section.${group.section}`)}</div>
            )}
            <div className="space-y-0.5">
              {group.items.map((item, ii) => {
                const link = (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.to === "/"}
                    onClick={onNavigate}
                    style={{ animationDelay: `${gi * 70 + ii * 45}ms` }}
                    className={({ isActive }) =>
                      cn(
                        "group animate-in relative flex items-center rounded-xl text-sm transition-all duration-200",
                        collapsed ? "h-11 justify-center px-0" : "h-11 gap-3 px-2.5 font-medium",
                        isActive
                          ? "nav-link-active font-semibold"
                          : "text-sidebar-foreground hover:bg-foreground/[0.045] hover:text-foreground"
                      )
                    }
                  >
                    {({ isActive }) => (
                      <>
                        {isActive && (
                          <span
                            aria-hidden="true"
                            className={cn("nav-rail", collapsed && "nav-rail-collapsed")}
                          >
                            <span className="nav-rail-node" />
                          </span>
                        )}
                        <span
                          className={cn("nav-icon-tile", isActive && "nav-icon-tile-active")}
                        >
                          <item.icon
                            className="h-[17px] w-[17px]"
                            strokeWidth={isActive ? 2.3 : 1.9}
                            aria-hidden="true"
                          />
                        </span>
                        {!collapsed && <span className="truncate">{item.label}</span>}
                        {!collapsed && isActive && (
                          <span className="nav-active-dot ml-auto" aria-hidden="true" />
                        )}
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
            </div>
          </div>
        ))}
      </nav>

      {/* Footer */}
      <div
        className={cn(
          "relative z-10 flex flex-none items-center gap-2 border-t border-border/70 px-4 py-3",
          collapsed && "justify-center px-0"
        )}
      >
        {!collapsed && <VersionFooter />}
        <button
          type="button"
          onClick={() => (collapsed ? toggleCollapsed() : useShell.getState().setMobileOpen(false))}
          aria-label={collapsed ? t("sidebar.expand") : t("sidebar.closeMenu")}
          className={cn(
            "rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-foreground/[0.06] hover:text-foreground",
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
          "sidebar-gradient relative z-40 hidden h-full flex-none flex-col transition-[width] duration-300 md:flex",
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
          "sidebar-gradient fixed inset-y-0 left-0 z-50 flex w-60 flex-col transition-transform duration-300 md:hidden",
          mobileOpen ? "translate-x-0" : "-translate-x-full"
        )}
        aria-hidden={!mobileOpen}
      >
        <SidebarContent collapsed={false} onNavigate={() => setMobileOpen(false)} />
      </aside>
    </>
  );
}
