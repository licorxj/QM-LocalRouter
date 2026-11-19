import React, { useEffect, useId, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity, Radio, Wifi, WifiOff, ScrollText } from "lucide-react";
import { useI18n } from "../i18n";
import {
  getMonitorRecent,
  monitorStreamUrl,
  getStrategies,
  getProviders,
} from "../services/api";
import { PageHeader } from "../components/shared";
import { cn } from "@/lib/utils";

type MonitorEvent = {
  id: number;
  rid?: string;
  phase?: "start" | "done";
  client_ip: string;
  strategy_id: number | null;
  provider_id: number | null;
  model: string;
  status_code: number | null;
  latency_ms: number | null;
  total_tokens: number;
  key_alias: string;
  error_message: string | null;
  created_at: string | null;
};

type ModelStat = { model: string; requests: number; tokens: number };

type Target = { name: string; type: "strategy" | "provider" | "none" };

const LANE_H = 78;
const MAX_VIZ = 8;
const PULSE_TIMEOUT_MS = 200000; // 兜底：start 后迟迟没 done 时，最多脉冲这么久就转静态

function evKey(ev: MonitorEvent) {
  return ev.rid ? `r-${ev.rid}` : `l-${ev.id}`;
}

function pad(n: number) {
  return String(n).padStart(2, "0");
}

function fmtTime(s: string | null) {
  if (!s) return "-";
  const withZone = s.includes("Z") || /[+-]\d\d:\d\d$/.test(s) ? s : s + "Z";
  const d = new Date(withZone);
  if (isNaN(d.getTime())) return s.slice(11, 19) || "-";
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function fmtNum(n: number) {
  return (n || 0).toLocaleString("en-US");
}

function NodeChip({
  label,
  sub,
  dot,
  pulse,
  style,
  className,
}: {
  label: string;
  sub?: string;
  dot: string;
  pulse?: boolean;
  style?: React.CSSProperties;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "monitor-node-pop flex max-w-[94%] items-center gap-2 rounded-full border bg-card/95 px-3 py-1.5 shadow-sm backdrop-blur-sm",
        className
      )}
      style={style}
      title={label}
    >
      <span className="relative flex h-2.5 w-2.5 shrink-0">
        {pulse ? (
          <span
            className={cn("absolute inline-flex h-full w-full animate-ping rounded-full opacity-60", dot)}
          />
        ) : null}
        <span className={cn("relative inline-flex h-2.5 w-2.5 rounded-full", dot)} />
      </span>
      <span className="min-w-0 truncate text-xs font-medium text-foreground">{label}</span>
      {sub ? (
        <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10px] font-semibold text-muted-foreground">
          {sub}
        </span>
      ) : null}
    </div>
  );
}

function FlowLane({
  ev,
  target,
  width,
  index,
  pulse,
  t,
}: {
  ev: MonitorEvent;
  target: Target;
  width: number;
  index: number;
  pulse: boolean;
  t: (key: string) => string;
}) {
  const rawId = useId();
  const uid = rawId.replace(/:/g, "");
  const pathAId = `pa-${uid}`;
  const pathBId = `pb-${uid}`;

  const x1 = width / 6;
  const x2 = width / 2;
  const x3 = (5 * width) / 6;
  const yc = LANE_H / 2;
  const arch = Math.min(LANE_H * 0.34, 26);
  const segA = x2 - x1;
  const segB = x3 - x2;
  const pathA = `M ${x1} ${yc} C ${x1 + segA * 0.45} ${yc - arch}, ${x2 - segA * 0.45} ${yc - arch}, ${x2} ${yc}`;
  const pathB = `M ${x2} ${yc} C ${x2 + segB * 0.45} ${yc + arch}, ${x3 - segB * 0.45} ${yc + arch}, ${x3} ${yc}`;

  const ok = ev.status_code != null && ev.status_code >= 200 && ev.status_code < 300;
  const delay = Math.min(index * 70, 420);

  return (
    <div
      className="relative w-full overflow-hidden rounded-lg border border-border/60 bg-muted/20"
      style={{ height: LANE_H }}
    >
      <svg
        className="absolute inset-0 h-full w-full"
        viewBox={`0 0 ${width} ${LANE_H}`}
        aria-hidden="true"
      >
        <defs>
          <linearGradient id={`gradA-${uid}`} x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="hsl(var(--info))" />
            <stop offset="100%" stopColor="hsl(var(--primary))" />
          </linearGradient>
          <linearGradient id={`gradB-${uid}`} x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="hsl(var(--primary))" />
            <stop offset="100%" stopColor="hsl(var(--success))" />
          </linearGradient>
        </defs>
        <path
          id={pathAId}
          d={pathA}
          fill="none"
          stroke={`url(#gradA-${uid})`}
          strokeWidth={1.6}
          strokeOpacity={0.85}
          vectorEffect="non-scaling-stroke"
          strokeDasharray="6 8"
          className={pulse ? "monitor-beam" : undefined}
        />
        <path
          id={pathBId}
          d={pathB}
          fill="none"
          stroke={`url(#gradB-${uid})`}
          strokeWidth={1.6}
          strokeOpacity={0.85}
          vectorEffect="non-scaling-stroke"
          strokeDasharray="6 8"
          className={pulse ? "monitor-beam" : undefined}
        />
        {pulse ? (
          <>
            <circle r={3.2} fill="hsl(var(--info))">
              <animateMotion dur="2.4s" repeatCount="indefinite" begin={`${delay / 1000}s`}>
                <mpath href={`#${pathAId}`} />
              </animateMotion>
            </circle>
            <circle r={3.2} fill="hsl(var(--success))">
              <animateMotion dur="2.4s" repeatCount="indefinite" begin={`${1.2 + delay / 1000}s`}>
                <mpath href={`#${pathBId}`} />
              </animateMotion>
            </circle>
          </>
        ) : null}
      </svg>

      <div className="relative grid h-full grid-cols-3 items-center px-2">
        <div className="flex justify-center">
          <NodeChip
            label={ev.client_ip || "-"}
            dot="bg-info"
            pulse={pulse}
            style={{ animationDelay: `${delay}ms` }}
          />
        </div>
        <div className="flex justify-center">
          <NodeChip
            label={target.name}
            sub={target.type === "strategy" ? t("monitor.strategy") : target.type === "provider" ? t("monitor.platform") : undefined}
            dot={target.type === "strategy" ? "bg-primary" : target.type === "provider" ? "bg-warning" : "bg-muted-foreground"}
            pulse={pulse}
            style={{ animationDelay: `${delay + 90}ms` }}
          />
        </div>
        <div className="flex justify-center">
          <NodeChip
            label={ev.model || "-"}
            dot={ok ? "bg-success" : "bg-destructive"}
            pulse={pulse}
            style={{ animationDelay: `${delay + 180}ms` }}
          />
        </div>
      </div>

      <span
        className={cn(
          "absolute right-2 top-1.5 rounded px-1.5 py-0.5 text-[10px] font-semibold tabular-nums",
          ok ? "bg-success/12 text-success" : "bg-destructive/12 text-destructive"
        )}
      >
        {ev.latency_ms != null ? `${ev.latency_ms}${t("monitor.ms")}` : String(ev.status_code ?? "")}
      </span>
    </div>
  );
}

export default function Monitor() {
  const { t } = useI18n();

  const [events, setEvents] = useState<MonitorEvent[]>([]);
  const [modelStats, setModelStats] = useState<Record<string, ModelStat>>({});
  const [conn, setConn] = useState<"connecting" | "open" | "error">("connecting");
  const [lanesWidth, setLanesWidth] = useState(900);
  const lanesRef = useRef<HTMLDivElement>(null);
  const startTimers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  const { data: strategies = [] } = useQuery({
    queryKey: ["strategies"],
    queryFn: () => getStrategies().then((r) => r.data),
  });
  const { data: providers = [] } = useQuery({
    queryKey: ["providers"],
    queryFn: () => getProviders().then((r) => r.data),
  });

  useEffect(() => {
    getMonitorRecent()
      .then((r) => {
        const logs: MonitorEvent[] = r.data?.logs || [];
        setEvents((prev) => {
          const seen = new Set(prev.map((e) => e.id));
          return [...logs.filter((e) => !seen.has(e.id)), ...prev].slice(0, 200);
        });
        const stats: Record<string, ModelStat> = {};
        for (const s of r.data?.model_stats || []) stats[s.model] = s;
        setModelStats((prev) => {
          const merged = { ...stats, ...prev };
          return merged;
        });
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    const es = new EventSource(monitorStreamUrl());
    es.onopen = () => setConn("open");
    es.onerror = () => setConn("error");
    es.onmessage = (msg) => {
      let ev: MonitorEvent;
      try {
        ev = JSON.parse(msg.data);
      } catch {
        return;
      }
      if (!ev) return;

      if (ev.phase === "start") {
        if (!ev.rid) return;
        const inflight: MonitorEvent = {
          id: -1,
          rid: ev.rid,
          phase: "start",
          client_ip: ev.client_ip || "-",
          strategy_id: ev.strategy_id ?? null,
          provider_id: ev.provider_id ?? null,
          model: ev.model || "-",
          status_code: null,
          latency_ms: null,
          total_tokens: 0,
          key_alias: "",
          error_message: null,
          created_at: ev.created_at || new Date().toISOString(),
        };
        setEvents((prev) => {
          if (prev.some((e) => e.rid === ev.rid)) return prev;
          return [inflight, ...prev].slice(0, 200);
        });
        const prevTimer = startTimers.current.get(ev.rid);
        if (prevTimer) clearTimeout(prevTimer);
        startTimers.current.set(
          ev.rid,
          setTimeout(() => {
            setEvents((prev) =>
              prev.map((e) => (e.rid === ev.rid ? { ...e, phase: "done" } : e))
            );
            startTimers.current.delete(ev.rid!);
          }, PULSE_TIMEOUT_MS)
        );
        return;
      }

      const done: MonitorEvent = { ...ev, phase: "done" };
      if (ev.id == null) return;
      setEvents((prev) => {
        if (ev.rid) {
          const idx = prev.findIndex((e) => e.rid === ev.rid);
          if (idx >= 0) {
            const next = [...prev];
            next[idx] = done;
            return next;
          }
        }
        if (prev.some((e) => e.id === ev.id)) return prev;
        return [done, ...prev].slice(0, 200);
      });
      if (ev.rid) {
        const timer = startTimers.current.get(ev.rid);
        if (timer) clearTimeout(timer);
        startTimers.current.delete(ev.rid);
      }
      if (ev.model) {
        setModelStats((prev) => {
          const cur = prev[ev.model] || { model: ev.model, requests: 0, tokens: 0 };
          return {
            ...prev,
            [ev.model]: {
              model: ev.model,
              requests: cur.requests + 1,
              tokens: cur.tokens + (ev.total_tokens || 0),
            },
          };
        });
      }
    };
    return () => es.close();
  }, []);

  useEffect(() => {
    const timers = startTimers.current;
    return () => {
      timers.forEach((timer) => clearTimeout(timer));
      timers.clear();
    };
  }, []);

  useEffect(() => {
    const el = lanesRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect?.width;
      if (w && w > 0) setLanesWidth(w);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const strategyMap = useMemo(
    () => new Map<number, string>((strategies as any[]).map((s) => [s.id, s.name])),
    [strategies]
  );
  const providerMap = useMemo(
    () => new Map<number, string>((providers as any[]).map((p) => [p.id, p.name])),
    [providers]
  );

  const targetOf = (ev: MonitorEvent): Target => {
    if (ev.strategy_id) {
      const name = strategyMap.get(ev.strategy_id);
      return { name: name || t("monitor.unknownTarget"), type: "strategy" };
    }
    if (ev.provider_id) {
      const name = providerMap.get(ev.provider_id);
      return { name: name || t("monitor.unknownTarget"), type: "provider" };
    }
    return { name: "-", type: "none" };
  };

  const tokenOf = (model: string) => modelStats[model]?.tokens || 0;
  const vizEvents = events.slice(0, MAX_VIZ);

  // 历史日志按模型去重：events 已是最新在前，同一模型只保留最新（最后一次）状态
  const historyEvents = useMemo(() => {
    const seen = new Set<string>();
    const out: MonitorEvent[] = [];
    for (const ev of events) {
      const key = ev.model || "-";
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(ev);
    }
    return out;
  }, [events]);

  const connLabel =
    conn === "open"
      ? t("monitor.live")
      : conn === "connecting"
      ? t("monitor.connecting")
      : t("monitor.reconnecting");

  return (
    <div className="flex h-full min-h-[520px] flex-col gap-4">
      <PageHeader
        title={t("monitor.title")}
        description={t("monitor.subtitle")}
        actions={
          <span
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium",
              conn === "open"
                ? "border-success/30 bg-success/10 text-success"
                : conn === "connecting"
                ? "border-info/30 bg-info/10 text-info"
                : "border-warning/30 bg-warning/10 text-warning"
            )}
          >
            {conn === "open" ? (
              <Wifi className="h-3.5 w-3.5 animate-pulse" aria-hidden="true" />
            ) : (
              <WifiOff className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            {connLabel}
          </span>
        }
      />

      {/* 顶部 30%：滚动历史请求日志 */}
      <section className="flex min-h-0 flex-[3] flex-col overflow-hidden glass-card rounded-xl">
        <div className="flex flex-none items-center gap-2 border-b px-4 py-2.5">
          <ScrollText className="h-4 w-4 text-primary" aria-hidden="true" />
          <h2 className="text-sm font-semibold">{t("monitor.historyTitle")}</h2>
          <span className="text-xs text-muted-foreground">
            {t("monitor.historyCount", { count: historyEvents.length })}
          </span>
          <span className="ml-auto text-[11px] text-muted-foreground">
            {t("monitor.windowHint", { hours: 5 })}
          </span>
        </div>
        <div className="grid flex-none grid-cols-[90px_minmax(110px,1fr)_minmax(120px,1.2fr)_minmax(140px,1.4fr)_80px_120px] gap-2 border-b bg-muted/40 px-4 py-1.5 text-[11px] font-semibold text-muted-foreground">
          <span>{t("monitor.colTime")}</span>
          <span>{t("monitor.colRequester")}</span>
          <span>{t("monitor.colTarget")}</span>
          <span>{t("monitor.colModel")}</span>
          <span className="text-right">{t("monitor.colLatency")}</span>
          <span className="text-right">{t("monitor.colToken5h")}</span>
        </div>
        <div className="monitor-log-scroll min-h-0 flex-1 overflow-y-auto px-2 py-1">
          {historyEvents.length === 0 ? (
            <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
              {t("monitor.emptyTitle")}
            </div>
          ) : (
            historyEvents.map((ev) => {
              const tg = targetOf(ev);
              return (
                <div
                  key={evKey(ev)}
                  className={cn(
                    "monitor-log-in grid grid-cols-[90px_minmax(110px,1fr)_minmax(120px,1.2fr)_minmax(140px,1.4fr)_80px_120px] items-center gap-2 rounded px-2 py-1.5 text-xs hover:bg-muted/50",
                    ev.phase === "start" && "bg-primary/5"
                  )}
                >
                  <span className="tabular-nums text-muted-foreground">{fmtTime(ev.created_at)}</span>
                  <span className="truncate font-mono text-muted-foreground" title={ev.client_ip}>
                    {ev.client_ip || "-"}
                  </span>
                  <span className="truncate" title={tg.name}>
                    <span
                      className={cn(
                        "mr-1 inline-block rounded px-1 py-0.5 text-[10px] font-semibold",
                        tg.type === "strategy"
                          ? "bg-primary/12 text-primary"
                          : tg.type === "provider"
                          ? "bg-warning/12 text-warning"
                          : "bg-muted text-muted-foreground"
                      )}
                    >
                      {tg.type === "strategy"
                        ? t("monitor.strategy")
                        : tg.type === "provider"
                        ? t("monitor.platform")
                        : "-"}
                    </span>
                    {tg.name}
                  </span>
                  <span className="truncate font-mono text-muted-foreground" title={ev.model}>
                    {ev.model || "-"}
                  </span>
                  <span className="text-right tabular-nums">
                    {ev.phase === "start" ? (
                      <span className="inline-flex items-center gap-1 text-primary">
                        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-primary" />
                        {t("monitor.inFlight")}
                      </span>
                    ) : ev.latency_ms != null ? (
                      `${ev.latency_ms}${t("monitor.ms")}`
                    ) : (
                      "-"
                    )}
                  </span>
                  <span className="text-right font-semibold tabular-nums text-primary">
                    {fmtNum(tokenOf(ev.model))}
                  </span>
                </div>
              );
            })
          )}
        </div>
      </section>

      {/* 底部 70%：三列实时路由可视化 */}
      <section className="flex min-h-0 flex-[7] flex-col overflow-hidden glass-card rounded-xl">
        <div className="flex flex-none items-center gap-2 border-b px-4 py-2.5">
          <Radio className="h-4 w-4 text-primary" aria-hidden="true" />
          <h2 className="text-sm font-semibold">{t("monitor.vizTitle")}</h2>
          <div className="ml-auto flex items-center gap-3 text-[11px] text-muted-foreground">
            <span className="flex items-center gap-1">
              <span className="h-2 w-2 rounded-full bg-info" /> {t("monitor.colIp")}
            </span>
            <span className="flex items-center gap-1">
              <span className="h-2 w-2 rounded-full bg-primary" /> {t("monitor.colHit")}
            </span>
            <span className="flex items-center gap-1">
              <span className="h-2 w-2 rounded-full bg-success" /> {t("monitor.colHitModel")}
            </span>
          </div>
        </div>
        <div className="grid flex-none grid-cols-3 border-b bg-muted/30 py-1.5 text-center text-[11px] font-semibold text-muted-foreground">
          <span>{t("monitor.colIp")}</span>
          <span>{t("monitor.colHit")}</span>
          <span>{t("monitor.colHitModel")}</span>
        </div>
        <div ref={lanesRef} className="monitor-log-scroll min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
          {vizEvents.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
              <Activity className="h-8 w-8 text-muted-foreground/40" aria-hidden="true" />
              <p className="text-sm font-medium text-muted-foreground">{t("monitor.emptyTitle")}</p>
              <p className="max-w-md text-xs text-muted-foreground/70">{t("monitor.emptyHint")}</p>
            </div>
          ) : (
            vizEvents.map((ev, index) => (
              <FlowLane
                key={evKey(ev)}
                ev={ev}
                target={targetOf(ev)}
                width={Math.max(lanesWidth - 24, 320)}
                index={index}
                pulse={ev.phase === "start"}
                t={t}
              />
            ))
          )}
        </div>
      </section>
    </div>
  );
}
