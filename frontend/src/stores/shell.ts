import { create } from "zustand";

type ShellState = {
  /** Desktop: icon-only sidebar (persisted). */
  collapsed: boolean;
  /** Mobile: off-canvas drawer visibility. */
  mobileOpen: boolean;
  toggleCollapsed: () => void;
  setMobileOpen: (open: boolean) => void;
};

const COLLAPSE_KEY = "app-sidebar-collapsed";

function initialCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === "1";
  } catch {
    return false;
  }
}

export const useShell = create<ShellState>((set, get) => ({
  collapsed: initialCollapsed(),
  mobileOpen: false,
  toggleCollapsed: () => {
    const next = !get().collapsed;
    try {
      localStorage.setItem(COLLAPSE_KEY, next ? "1" : "0");
    } catch {
      /* ignore */
    }
    set({ collapsed: next });
  },
  setMobileOpen: (open) => set({ mobileOpen: open }),
}));
