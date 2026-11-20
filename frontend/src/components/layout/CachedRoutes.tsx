import React, { Children, isValidElement, useMemo, useRef } from "react";
import { useLocation } from "react-router-dom";
import type { ReactNode } from "react";

interface RouteEntry {
  path: string;
  element: ReactNode;
}

/**
 * Keep-alive router: pages visited once stay mounted and are only hidden, so
 * local component state (forms, chat history, expanded rows, SSE streams, ...)
 * survives navigation. Switching back never remounts or re-fetches a page.
 *
 * Works by reading the `path`/`element` props off the <Route> children (which
 * are used purely as configuration here and never rendered by React Router).
 */
export default function CachedRoutes({ children }: { children?: ReactNode }) {
  const { pathname } = useLocation();

  const routes = useMemo<RouteEntry[]>(() => {
    const list: RouteEntry[] = [];
    Children.forEach(children, (child) => {
      if (!isValidElement(child)) return;
      const props = child.props as { path?: string; element?: ReactNode };
      if (typeof props.path === "string" && props.element !== undefined) {
        list.push({ path: props.path, element: props.element });
      }
    });
    return list;
  }, [children]);

  const active = routes.find((r) => r.path === pathname);

  // Stable cache keyed by path. Mount once, never unmount.
  const cacheRef = useRef<Map<string, ReactNode>>(new Map());
  const cache = cacheRef.current;
  if (active && !cache.has(active.path)) {
    cache.set(active.path, active.element);
  }

  return (
    <>
      {Array.from(cache.entries()).map(([path, element]) => {
        const visible = path === pathname;
        return (
          <div
            key={path}
            // `contents` keeps the page laid out exactly as a direct child of
            // <main> (important for pages using negative margins / full height);
            // `none` hides inactive pages without unmounting them.
            style={{ display: visible ? "contents" : "none" }}
            aria-hidden={!visible}
          >
            {element}
          </div>
        );
      })}
    </>
  );
}
