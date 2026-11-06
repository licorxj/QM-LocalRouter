import React from "react";
import { useLocation } from "react-router-dom";
import { Loader2, Menu, Moon, Play, Sun } from "lucide-react";
import { useI18n } from "../../i18n";
import { useTheme } from "../../providers/theme";
import { useNavItems } from "./nav";
import { useShell } from "../../stores/shell";
import { useServiceHealth } from "../../hooks/useServiceHealth";
import { Button } from "../ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "../ui/dialog";
import { StatusDot } from "../shared";

function ThemeToggle() {
  const { resolved, setTheme } = useTheme();
  const isDark = resolved === "dark";
  return (
    <Button
      variant="ghost"
      size="icon"
      className="h-9 w-9"
      aria-label={isDark ? "Switch to light theme" : "Switch to dark theme"}
      onClick={() => setTheme(isDark ? "light" : "dark")}
    >
      {isDark ? (
        <Sun className="h-4 w-4" aria-hidden="true" />
      ) : (
        <Moon className="h-4 w-4" aria-hidden="true" />
      )}
    </Button>
  );
}

function LocaleToggle() {
  const { locale, setLocale } = useI18n();
  return (
    <Button
      variant="ghost"
      size="sm"
      className="h-9 min-w-9 px-2 font-mono text-xs font-semibold"
      aria-label="Switch language"
      onClick={() => setLocale(locale === "zh-CN" ? "en-US" : "zh-CN")}
    >
      {locale === "zh-CN" ? "EN" : "中"}
    </Button>
  );
}

function HealthPill() {
  const { t } = useI18n();
  const { running, starting, start, startError, dismissStartError } = useServiceHealth();

  if (running === null) {
    return (
      <div className="flex h-9 items-center gap-2 rounded-full border border-border px-3 text-xs text-muted-foreground">
        <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
        <span className="hidden sm:inline">{t("common.loading")}</span>
      </div>
    );
  }

  if (running) {
    return (
      <div
        className="flex h-9 items-center gap-2 rounded-full border border-border bg-success/8 px-3 text-xs font-medium text-success"
        title={t("sidebar.serviceRunning")}
      >
        <StatusDot tone="success" />
        <span className="hidden sm:inline">{t("sidebar.serviceRunning")}</span>
      </div>
    );
  }

  return (
    <>
      <button
        type="button"
        onClick={start}
        disabled={starting}
        className="flex h-9 items-center gap-2 rounded-full border border-destructive/30 bg-destructive/10 px-3 text-xs font-medium text-destructive transition-colors hover:bg-destructive/20 disabled:opacity-60"
        title={t("sidebar.serviceStopped")}
      >
        {starting ? (
          <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
        ) : (
          <StatusDot tone="destructive" />
        )}
        <span className="hidden sm:inline">
          {starting ? t("sidebar.starting") : t("sidebar.serviceStopped")}
        </span>
        {!starting && <Play className="h-3 w-3" aria-hidden="true" />}
      </button>

      <Dialog open={startError} onOpenChange={(o) => !o && dismissStartError()}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{t("sidebar.startService")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3 text-sm text-muted-foreground">
            <p>{t("sidebar.startServiceDesc")}</p>
            <div className="space-y-1 rounded-lg bg-muted p-3 font-mono text-xs">
              <p className="font-semibold text-foreground">cd backend</p>
              <p className="font-semibold text-foreground">
                python -m uvicorn app.main:app --host 0.0.0.0 --port {window.location.port || 12002} --reload
              </p>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}

export default function Topbar() {
  const location = useLocation();
  const navItems = useNavItems();
  const { setMobileOpen } = useShell();

  const current = navItems.find((n) =>
    n.to === "/" ? location.pathname === "/" : location.pathname.startsWith(n.to)
  );

  return (
    <header className="sticky top-0 z-30 flex h-14 flex-none items-center gap-2 border-b border-border bg-background/85 px-4 backdrop-blur lg:px-6">
      <Button
        variant="ghost"
        size="icon"
        className="h-9 w-9 md:hidden"
        aria-label="Open menu"
        onClick={() => setMobileOpen(true)}
      >
        <Menu className="h-5 w-5" aria-hidden="true" />
      </Button>
      <h2 className="truncate text-sm font-semibold tracking-tight text-foreground/90">
        {current?.label ?? ""}
      </h2>
      <div className="ml-auto flex items-center gap-1">
        <div className="mr-1">
          <HealthPill />
        </div>
        <LocaleToggle />
        <ThemeToggle />
      </div>
    </header>
  );
}
