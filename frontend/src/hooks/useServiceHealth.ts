import { useCallback, useEffect, useState } from "react";

const MANAGER_PORT = 12003;

type ServiceHealth = {
  /** null = still checking */
  running: boolean | null;
  starting: boolean;
  check: () => Promise<boolean>;
  start: () => Promise<boolean>;
  /** Error dialog state surfaced by start() when every path failed. */
  startError: boolean;
  dismissStartError: () => void;
};

/**
 * Backend liveness probe + best-effort start (manager port first, then the
 * backend's own endpoint). Extracted from the old Sidebar footer so both the
 * sidebar and the topbar can share it.
 */
export function useServiceHealth(): ServiceHealth {
  const [running, setRunning] = useState<boolean | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState(false);

  const check = useCallback(async (): Promise<boolean> => {
    try {
      const res = await fetch("/api/health", { method: "GET", signal: AbortSignal.timeout(3000) });
      setRunning(res.ok);
      return res.ok;
    } catch {
      setRunning(false);
      return false;
    }
  }, []);

  useEffect(() => {
    check();
    const interval = setInterval(check, 8000);
    return () => clearInterval(interval);
  }, [check]);

  const start = useCallback(async (): Promise<boolean> => {
    setStarting(true);
    // Preferred: standalone service manager (always running alongside the UI).
    try {
      const mgrRes = await fetch(`http://127.0.0.1:${MANAGER_PORT}/start`, {
        method: "POST",
        signal: AbortSignal.timeout(8000),
      });
      if (mgrRes.ok) {
        let retries = 0;
        const poll = async () => {
          retries += 1;
          const ok = await check();
          if (ok) {
            setStarting(false);
            return;
          }
          if (retries < 10) setTimeout(poll, 1000);
          else setStarting(false);
        };
        setTimeout(poll, 1500);
        return true;
      }
    } catch {
      /* manager not reachable — fall through */
    }
    // Fallback: backend's own start endpoint.
    try {
      const res = await fetch("/api/service/start", { method: "POST", signal: AbortSignal.timeout(5000) });
      if (res.ok) {
        setTimeout(async () => {
          await check();
          setStarting(false);
        }, 3000);
        return true;
      }
    } catch {
      /* fall through */
    }
    setStarting(false);
    setStartError(true);
    return false;
  }, [check]);

  return {
    running,
    starting,
    check,
    start,
    startError,
    dismissStartError: () => setStartError(false),
  };
}
