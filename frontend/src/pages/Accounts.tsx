import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  getAccounts, getAccountPlatforms, startAccountLogin, refreshAccount, claimAccount,
  deleteKey, getOauthStatus, syncModelGroups, claimAllAccounts,
  getPluginLoginStatus, cancelPluginLogin, refreshPluginAccount, claimPluginAccount, deletePluginAccount,
  getEchoLoginStatus, cancelEchoLogin, refreshEchoAccount, claimEchoAccount, deleteEchoAccount,
  getPlugins, checkPluginUpdates, installPlugin, getPluginTask, enablePlugin, disablePlugin, uninstallPlugin,
  updatePluginSettings,
  getScriptPlugins, runScriptPlugin,
} from "../services/api";
import { useI18n } from "../i18n";
import { toast } from "../stores/toast";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Button } from "../components/ui/button";
import { Badge } from "../components/ui/badge";
import { Switch } from "../components/ui/switch";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "../components/ui/dialog";
import { PageHeader, ConfirmDialog } from "../components/shared";
import {
  Eye, EyeOff, ExternalLink, Gift, Plus, RefreshCw, Trash2, UserRound, X,
  Puzzle, Download, Check, ChevronDown, ChevronRight,
} from "lucide-react";

// 平台图标（来自 EchoBird 的官方产品图标；缺失时回退首字母圆徽）
const PLATFORM_ICONS: Record<string, string> = {
  workbuddy: "/icons/accounts/workbuddy.png",
  "workbuddy-intl": "/icons/accounts/workbuddyai.png",
  "codebuddy-cn": "/icons/accounts/workbuddy.png",
  zcode: "/icons/accounts/zcode.png",
  claude: "/icons/accounts/claudecode.svg",
  codex: "/icons/accounts/codex.svg",
  cline: "/icons/accounts/cline.png",
  antigravity: "/icons/accounts/antigravity.png",
  "github-copilot": "/icons/accounts/copilot.png",
  "kimi-coding": "/icons/accounts/kimicode.png",
  "xai-oauth": "/icons/accounts/grok.png",
  opencode: "/icons/accounts/opencode.svg",
  cursor: "/icons/accounts/cursor.svg",
  claudedesktop: "/icons/accounts/claudedesktop.svg",
  deepseek: "/icons/accounts/deepseek.png",
  doubaowork: "/icons/accounts/doubaowork.png",
  dola: "/icons/accounts/dola.png",
  trae: "/icons/accounts/trae.svg",
  traecn: "/icons/accounts/traecn.svg",
  kilo: "/icons/accounts/kilo.png",
  qoder: "/icons/accounts/qoder.svg",
  qodercn: "/icons/accounts/qodercn.svg",
  qwenwork: "/icons/accounts/qwenwork.svg",
  qwenworkcn: "/icons/accounts/qwenworkcn.svg",
};

const ACCOUNT_NAME_MASK = "******";
const LOGIN_POLL_TIMEOUT_MS = 300000;

type Metric = { key: string; value: string; percent: number | null; reset_at: number | null; remaining?: number | null; total?: number | null; label?: string; pending?: boolean };
type QuotaCache = { plan?: string | null; subscription_end_at?: number | null; identity?: string | null; metrics?: Metric[]; checkin?: { claimed?: boolean; claimed_at?: number } | null; notice?: string | null; fetched_at?: number };
type AccountCard = {
  id: number | string; platform: string; platform_label: string; provider_id: number; provider_name: string;
  identity: string; alias: string; plan: string | null; status: string; token_expires_at: number;
  quota: QuotaCache | null; quota_fetched_at: number; supports_quota: boolean; supports_claim: boolean;
  last_error: string; quota_error?: string; login_required?: boolean; claim?: { claimed: boolean; already: boolean };
  via_plugin?: boolean; via_echo?: boolean; application?: string; account_id?: string;
};
type PlatformEntry = {
  id: string; label: string; domain: string; website: string;
  kind: "browser" | "oauth" | "import" | "plugin" | "echo" | "unsupported";
  quota: boolean; claim: boolean; icon: string; note: string; login_ready: boolean; account_count: number;
  plugin_id?: string;
};
type ClaimGroup = {
  key: string; label: string; platform: string;
  cards: AccountCard[]; kind: "native" | "zcode" | "qoder";
};
type PluginEntry = {
  id: string; name: string; display_name: string; description?: string;
  applications: string[]; version?: string; operations?: string[];
  installed_version?: string | null; enabled?: boolean; update_available?: boolean; latest_version?: string;
};
type PluginSnapshot = {
  platform?: string; region?: string; auto_update?: boolean;
  installed?: { id: string; display_name: string; version: string; enabled: boolean; applications: string[] }[];
  updates?: { id: string; installed_version: string; latest_version: string }[];
  sync?: { checked_at?: number; error?: string; has_index?: boolean };
  task?: { phase: string; plugin_id: string; action: string; message: string; error: string };
  available?: PluginEntry[];
};

function countdown(seconds: number, zh: boolean): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return zh ? `${d}天${h}小时` : `${d}d ${h}h`;
  if (h > 0) return zh ? `${h}小时${m}分` : `${h}h ${m}m`;
  return zh ? `${m}分钟` : `${m}m`;
}

/** EchoBird 同款紧凑倒计时（16d19h），用于积分重置时间 */
function countdownCompact(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d${h}h`;
  if (h > 0) return `${h}h${m}m`;
  return `${m}m`;
}

function metricLabel(key: string, t: (k: string) => string): string {
  const label = t("accounts.metric_" + key);
  return label.startsWith("accounts.metric_") ? key : label;
}

function PlatformIcon({ platform, identity, className }: { platform: string; identity?: string; className?: string }) {
  const [failed, setFailed] = useState(false);
  const src = PLATFORM_ICONS[platform];
  if (!src || failed) {
    const letter = (identity || platform || "?").trim().charAt(0).toUpperCase();
    return (
      <div className={"flex flex-none items-center justify-center rounded-md bg-primary/12 text-primary " + (className || "h-8 w-8 text-sm")}>
        <span className="font-bold">{letter}</span>
      </div>
    );
  }
  return <img src={src} alt={platform} onError={() => setFailed(true)} className={className || "h-8 w-8 flex-none rounded-md object-contain"} />;
}

function MetricBar({ metric, t }: { metric: Metric; t: (k: string) => string }) {
  const percent = typeof metric.percent === "number" ? Math.max(0, Math.min(100, metric.percent)) : null;
  const low = percent !== null && percent <= 15;
  const reset = metric.reset_at ? countdownCompact(metric.reset_at - Math.floor(Date.now() / 1000)) : "";
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-sm text-muted-foreground">
          {metricLabel(metric.key, t)}
          {metric.label ? <span className="ml-1.5">{metric.label}</span> : null}
          {metric.pending ? <Badge variant="secondary" className="ml-1.5 text-[10px]">{t("accounts.quotaPending")}</Badge> : null}
          {!metric.pending && reset ? <span className="ml-2 text-xs text-muted-foreground/70">{reset}</span> : null}
        </span>
        <span className={"font-mono text-base font-semibold " + (low ? "text-warning" : "text-success")}>
          {metric.value || "—"}
        </span>
      </div>
      {percent !== null && (
        <div className={"h-1.5 w-full overflow-hidden rounded-full " + (low ? "bg-warning/15" : "bg-success/15")}>
          <div
            className={"h-full rounded-full transition-all " + (low ? "bg-warning" : "bg-success")}
            style={{ width: percent + "%" }}
          />
        </div>
      )}
    </div>
  );
}

export default function Accounts() {
  const { t, locale } = useI18n();
  const zh = locale.startsWith("zh");
  const qc = useQueryClient();
  const [namesHidden, setNamesHidden] = useState(() => {
    try { return localStorage.getItem("account-names-hidden") !== "0"; } catch { return true; }
  });
  useEffect(() => {
    try { localStorage.setItem("account-names-hidden", namesHidden ? "1" : "0"); } catch {}
  }, [namesHidden]);

  const { data: accountsData, isLoading } = useQuery({
    queryKey: ["accounts"],
    queryFn: () => getAccounts().then((r) => r.data),
    // 快照过期时（stale=true 表示后台正在重建）高频轮询，重建完成后自动更新显示
    refetchInterval: (query) => (query.state.data?.stale ? 3000 : 20000),
  });
  const accounts: AccountCard[] = accountsData?.accounts || [];

  // 按平台汇总账号积分（套餐积分 + 奖励积分的剩余值）
  const platformPoints = useMemo(() => {
    const map = new Map<string, { label: string; total: number }>();
    for (const acc of accounts) {
      const metrics: Metric[] = acc.quota?.metrics || [];
      let sum = 0;
      for (const m of metrics) {
        if ((m.key === "base" || m.key === "reward") && typeof m.remaining === "number" && Number.isFinite(m.remaining)) {
          sum += m.remaining;
        }
      }
      if (sum > 0) {
        const label = acc.platform_label || acc.platform;
        const prev = map.get(acc.platform) || { label, total: 0 };
        prev.total += sum;
        map.set(acc.platform, prev);
      }
    }
    return Array.from(map.values()).map((v) => ({
      ...v,
      display: v.total.toLocaleString(zh ? "zh-CN" : "en-US", { maximumFractionDigits: 2 }),
    }));
  }, [accounts, zh]);

  const { data: platformsData } = useQuery({
    queryKey: ["accountPlatforms"],
    queryFn: () => getAccountPlatforms().then((r) => r.data),
  });
  const platforms: PlatformEntry[] = platformsData?.platforms || [];

  // ZCode 领卡已改为脚本插件（纯 HTTP 协议，无需本地 exe/CDP）
  // 唯一门槛：zcode-claim 插件已安装且启用
  const scriptPluginsQ = useQuery({
    queryKey: ["scriptPlugins"],
    queryFn: () => getScriptPlugins().then((r) => r.data),
    staleTime: 30_000,
  });
  const zcodePlugin = useMemo(
    () => (scriptPluginsQ.data?.installed || []).find((p: any) => p.id === "zcode-claim"),
    [scriptPluginsQ.data]
  );
  const zcodeState = useMemo<"ready" | "plugin_missing" | "plugin_disabled">(() => {
    if (!zcodePlugin) return "plugin_missing";
    if (zcodePlugin.enabled === false) return "plugin_disabled";
    return "ready";
  }, [zcodePlugin]);
  const zcodeReady = zcodeState === "ready";
  const qoderPlugin = useMemo(
    () => (scriptPluginsQ.data?.installed || []).find((p: any) => p.id === "qoder-claim"),
    [scriptPluginsQ.data]
  );
  const qoderState = useMemo<"ready" | "plugin_missing" | "plugin_disabled">(() => {
    if (!qoderPlugin) return "plugin_missing";
    if (qoderPlugin.enabled === false) return "plugin_disabled";
    return "ready";
  }, [qoderPlugin]);
  const qoderReady = qoderState === "ready";
  const isQoderCard = (card: AccountCard) => card.platform === "qoder" || card.platform === "qodercn";

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["accounts"] });
    qc.invalidateQueries({ queryKey: ["accountPlatforms"] });
  };

  // --- login dialog ---
  const [login, setLogin] = useState<{ platform: string; label: string; sessionId: string; url: string; userCode: string; waiting: boolean; plugin: boolean; echo: boolean; awaitingClientExit: boolean } | null>(null);
  const loginTimer = useRef<any>(null);
  useEffect(() => () => { if (loginTimer.current) clearTimeout(loginTimer.current); }, []);

  function openAuthUrl(url: string) {
    if (!url) return;
    const win = window.open(url, "_blank");
    if (!win) toast({ title: t("accounts.openAuthPage"), description: url, variant: "default" });
  }

  const startLogin = async (platform: PlatformEntry) => {
    if (platform.kind === "unsupported") return;
    if (platform.kind === "import") {
      toast({ title: t("accounts.importHint"), variant: "default" });
      return;
    }
    try {
      const res = await startAccountLogin(platform.id);
      const d = res.data || {};
      const url = d.verification_uri || d.verificationUri || d.authorize_url || d.authorization_url || d.authorizationUrl || "";
      const isPlugin = platform.kind === "plugin";
      const isEcho = platform.kind === "echo";
      setLogin({
        platform: platform.id, label: d.label || platform.label, sessionId: d.session_id || d.login_id,
        url, userCode: d.user_code || "", waiting: true, plugin: isPlugin, echo: isEcho,
        awaitingClientExit: isPlugin && platform.id === "claudedesktop",
      });
      if (url) openAuthUrl(url);
      const interval = d.flow === "cli-workbuddy" ? 1500 : 2000;
      const startedAt = Date.now();
      const poll = async () => {
        try {
          const sid = d.session_id || d.login_id;
          const st = isPlugin ? await getPluginLoginStatus(sid)
            : isEcho ? await getEchoLoginStatus(sid)
            : await getOauthStatus(d.session_id);
          const s = st.data || {};
          if (s.status === "success") {
            setLogin(null);
            invalidate();
            toast({ title: t("accounts.loginSuccess"), variant: "success" });
            return;
          }
          if (s.status === "failed") {
            setLogin(null);
            toast({ title: t("accounts.loginFailed"), description: s.error, variant: "destructive" });
            return;
          }
          if (s.status === "expired" || Date.now() - startedAt > LOGIN_POLL_TIMEOUT_MS) {
            setLogin(null);
            toast({ title: t("accounts.loginTimeout"), variant: "destructive" });
            return;
          }
          if (s.user_code) setLogin((prev) => (prev ? { ...prev, userCode: s.user_code } : prev));
          if (s.authorize_url && !url) setLogin((prev) => (prev ? { ...prev, url: s.authorize_url } : prev));
          loginTimer.current = setTimeout(poll, interval);
        } catch {
          setLogin(null);
        }
      };
      loginTimer.current = setTimeout(poll, interval);
    } catch (e: any) {
      toast({ title: t("accounts.loginFailedStart"), description: e?.response?.data?.detail, variant: "destructive" });
    }
  };

  const refreshCard = (card: AccountCard) => {
    if (card.via_plugin && card.application && card.account_id) {
      return refreshPluginAccount(card.application, card.account_id);
    }
    if (card.via_echo && card.application && card.account_id) {
      return refreshEchoAccount(card.application, card.account_id);
    }
    return refreshAccount(card.id as number);
  };
  const claimCard = (card: AccountCard) => {
    if (card.via_plugin && card.application && card.account_id) {
      return claimPluginAccount(card.application, card.account_id);
    }
    if (card.via_echo && card.application && card.account_id) {
      return claimEchoAccount(card.application, card.account_id);
    }
    return claimAccount(card.id as number);
  };
  const deleteCard = (card: AccountCard) => {
    if (card.via_plugin && card.application && card.account_id) {
      return deletePluginAccount(card.application, card.account_id);
    }
    if (card.via_echo && card.application && card.account_id) {
      return deleteEchoAccount(card.application, card.account_id);
    }
    return deleteKey(card.id as number);
  };

  const refreshMut = useMutation({
    mutationFn: (card: AccountCard) => refreshCard(card),
    onSuccess: (res) => {
      invalidate();
      const card = res.data || {};
      if (card.quota_error) {
        toast({ title: t("accounts.quotaRefreshFailed"), description: card.quota_error, variant: "destructive" });
      } else {
        toast({ title: t("accounts.quotaRefreshed"), variant: "success" });
      }
    },
    onError: (e: any) => toast({ title: t("accounts.quotaRefreshFailed"), description: e?.response?.data?.detail, variant: "destructive" }),
  });

  // --- 签到中心：本地记录已签账号，签到后立即刷新状态 ---
  const [claimDialogOpen, setClaimDialogOpen] = useState(false);
  const [locallyClaimed, setLocallyClaimed] = useState<Record<string, boolean>>({});
  // 签到中心各平台分组默认折叠，key 为 group.key
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({});
  const toggleGroup = (key: string) => setExpandedGroups((prev) => ({ ...prev, [key]: !prev[key] }));
  const markClaimed = (ids: (string | number)[]) => {
    if (!ids.length) return;
    setLocallyClaimed((prev) => {
      const next = { ...prev };
      for (const id of ids) next[String(id)] = true;
      return next;
    });
  };

  const claimMut = useMutation({
    mutationFn: (card: AccountCard) => claimCard(card),
    onSuccess: (res, card) => {
      invalidate();
      markClaimed([card.id]);
      const claim = res.data?.claim;
      const claimedToday = res.data?.daily_claimed_at
        && new Date(res.data.daily_claimed_at * 1000).toDateString() === new Date().toDateString();
      if (claim?.already || (!claim && claimedToday)) toast({ title: t("accounts.alreadyClaimed"), variant: "default" });
      else toast({ title: t("accounts.claimed"), variant: "success" });
    },
    onError: (e: any) => toast({ title: t("accounts.claimFailed"), description: e?.response?.data?.detail, variant: "destructive" }),
  });

  const [confirmDelete, setConfirmDelete] = useState<AccountCard | null>(null);
  const deleteMut = useMutation({
    mutationFn: (card: AccountCard) => deleteCard(card),
    onSuccess: () => {
      invalidate();
      setConfirmDelete(null);
      toast({ title: t("accounts.accountDeleted"), variant: "success" });
    },
    onError: (e: any) => toast({ title: t("accounts.claimFailed"), description: e?.response?.data?.detail, variant: "destructive" }),
  });

  const claimAllMut = useMutation({
    mutationFn: (platform?: string) => claimAllAccounts(platform),
    onSuccess: (res, platform) => {
      invalidate();
      markClaimed((res.data?.results || []).filter((r: any) => r.ok).map((r: any) => r.id));
      const total = res.data?.total || 0;
      const claimed = res.data?.claimed || 0;
      const failed = total - claimed;
      const already = (res.data?.results || []).filter((r: any) => r.already).length;
      const scope = platform ? t("accounts.claimPlatformDone", { platform }) : undefined;
      toast({
        title: (scope ? scope + " · " : "") + t("accounts.claimAllDone", { claimed, total }),
        description: failed > 0
          ? `${t("accounts.claimAllFailedCount", { failed })}${already > 0 ? ` · ${t("accounts.alreadyCount", { count: already })}` : ""}`
          : already > 0 ? t("accounts.alreadyCount", { count: already }) : undefined,
        variant: claimed > 0 || failed === 0 ? "success" : "destructive",
      });
    },
    onError: (e: any) => toast({ title: t("accounts.claimAllFailed"), description: e?.response?.data?.detail, variant: "destructive" }),
  });

  const zcodeClaimMut = useMutation({
    // 走 zcode-claim 脚本插件（纯 HTTP 领卡），不再用本地 exe/CDP 路由
    mutationFn: async (keyId?: number) => {
      const res = await runScriptPlugin("zcode-claim", {
        action: "claim",
        ...(keyId != null ? { key_id: keyId } : {}),
      });
      const run = res.data || {};
      const result = run.result || {};
      const claimed = Number(result.claimed || 0);
      const already = Number(result.already || 0);
      const needVerify = Number(result.need_verification || 0);
      const report = result.report || "";
      const failed = !run.ok || (result.result === "NO_ACCOUNT" || result.result === "NO_PERMISSION");
      const account = result.results?.[0]?.account || "";
      return {
        ok: !failed && (claimed > 0 || already > 0),
        account,
        key_id: keyId,
        message: report || run.error || result.report || "",
        status: failed ? "error" : (needVerify > 0 ? "captcha" : (claimed > 0 || already > 0 ? "ok" : "no_delivery")),
      };
    },
    onSuccess: (d) => {
      invalidate();
      const who = d.account ? `（${d.account}）` : "";
      if (d.ok) {
        if (d.key_id != null) markClaimed([d.key_id]);
        toast({ title: `${t("accounts.zcodeClaimDone")}${who}`, description: d.message, variant: "success" });
      } else {
        const soft = ["no_delivery", "captcha", "unconfirmed", "timeout"].includes(d.status);
        toast({ title: `${t("accounts.zcodeClaimFailed")}${who}`, description: d.message || d.status,
          variant: soft ? "default" : "destructive" });
      }
    },
    onError: (e: any) => toast({ title: t("accounts.zcodeClaimFailed"),
      description: e?.response?.data?.detail, variant: "destructive" }),
  });

  // Qoder / Qoder CN：走 qoder-claim 插件（每日签到 + Pro 福利 + 签到流水）
  const qoderClaimMut = useMutation({
    mutationFn: async (card?: AccountCard) => {
      const params: Record<string, any> = { action: "all" };
      if (card) {
        params.platform = card.platform;
        params.account_id = card.account_id || (typeof card.id === "string" ? card.id.split(":").pop() : "");
      }
      const res = await runScriptPlugin("qoder-claim", params);
      const run = res.data || {};
      const result = run.result || {};
      const okCount = Number(result.ok_count || 0);
      const already = Number(result.already_count || 0);
      const failed = Number(result.failed_count || 0);
      const report = result.report || "";
      const hardFail = !run.ok || result.result === "NO_ACCOUNT" || result.result === "NO_PERMISSION";
      return {
        ok: !hardFail && failed === 0 && (okCount + already) > 0,
        soft: !hardFail && failed === 0 && okCount === 0 && already === 0,
        account: card?.identity || "",
        message: report || run.error || result.report || "",
      };
    },
    onSuccess: (d, card) => {
      invalidate();
      if (card) markClaimed([card.id]);
      if (d.ok) toast({ title: t("accounts.qoderClaimDone"), description: d.message, variant: "success" });
      else if (d.soft) toast({ title: t("accounts.alreadyClaimed"), description: d.message, variant: "default" });
      else toast({ title: t("accounts.qoderClaimFailed"), description: d.message, variant: "destructive" });
    },
    onError: (e: any) => toast({ title: t("accounts.qoderClaimFailed"),
      description: e?.response?.data?.detail, variant: "destructive" }),
  });

  const syncGroupsMut = useMutation({
    mutationFn: () => syncModelGroups(),
    onSuccess: (res) => {
      const groups = res.data?.groups || [];
      const errors = res.data?.errors || [];
      toast({
        title: t("accounts.syncGroupsDone", { count: groups.length }),
        description: errors.length ? `${t("accounts.syncGroupsFailed")}: ${errors.length}` : undefined,
        variant: errors.length ? "default" : "success",
      });
      invalidate();
    },
    onError: (e: any) => toast({ title: t("accounts.syncGroupsFailed"), description: e?.response?.data?.detail, variant: "destructive" }),
  });

  const refreshAllMut = useMutation({
    mutationFn: async () => {
      const quotaCards = accounts.filter((a) => a.supports_quota);
      const results = await Promise.allSettled(quotaCards.map((c) => refreshCard(c)));
      return results;
    },
    onSuccess: () => {
      invalidate();
      toast({ title: t("accounts.quotaRefreshed"), variant: "success" });
    },
  });

  // --- 插件管理（echobird-plugin 同步） ---
  const { data: pluginsData, refetch: refetchPlugins } = useQuery({
    queryKey: ["plugins"],
    queryFn: () => getPlugins().then((r) => r.data),
  });
  const pluginsSnapshot: PluginSnapshot = pluginsData || {};
  const pluginEntries: PluginEntry[] = pluginsSnapshot.available || [];
  const taskPhase = pluginsSnapshot.task?.phase || "idle";

  // 安装/更新任务进行中时轮询进度，结束后刷新列表
  useEffect(() => {
    if (taskPhase !== "running") return;
    const timer = setInterval(() => { getPluginTask().then((r) => {
      qc.setQueryData(["plugins"], (prev: any) => prev ? { ...prev, task: r.data } : prev);
      if (r.data?.phase === "success") {
        toast({ title: t("accounts.pluginInstallSuccess"), description: r.data.message, variant: "success" });
        refetchPlugins(); invalidate();
      } else if (r.data?.phase === "error") {
        toast({ title: t("accounts.pluginInstallFailed"), description: r.data.error, variant: "destructive" });
        refetchPlugins();
      }
    }).catch(() => {}); }, 1200);
    return () => clearInterval(timer);
  }, [taskPhase]);

  const pluginInstallMut = useMutation({
    mutationFn: (pluginId: string) => installPlugin(pluginId),
    onSuccess: (_res, pluginId) => { qc.setQueryData(["plugins"], (prev: any) => prev ? { ...prev, task: { phase: "running", plugin_id: pluginId } } : prev); refetchPlugins(); },
    onError: (e: any) => toast({ title: t("accounts.pluginInstallFailed"), description: e?.response?.data?.detail, variant: "destructive" }),
  });
  const pluginToggleMut = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => (enabled ? enablePlugin(id) : disablePlugin(id)),
    onSuccess: () => { refetchPlugins(); invalidate(); },
  });
  const [confirmUninstall, setConfirmUninstall] = useState<PluginEntry | null>(null);
  const pluginUninstallMut = useMutation({
    mutationFn: (pluginId: string) => uninstallPlugin(pluginId),
    onSuccess: () => { refetchPlugins(); invalidate(); setConfirmUninstall(null); toast({ title: t("accounts.pluginUninstalled"), variant: "success" }); },
  });
  const checkUpdatesMut = useMutation({
    mutationFn: () => checkPluginUpdates(),
    onSuccess: (res) => {
      qc.setQueryData(["plugins"], res.data);
      const updates = res.data?.updates || [];
      toast({ title: updates.length ? t("accounts.pluginUpdateAvailable") : t("accounts.pluginNoUpdates"),
        description: updates.length ? updates.map((u: any) => u.id.split(".").pop()).join(", ") : undefined,
        variant: updates.length ? "default" : "success" });
    },
    onError: (e: any) => toast({ title: t("accounts.pluginSyncFailed"), description: e?.response?.data?.detail, variant: "destructive" }),
  });

  const statusBadge = (status: string) => {
    if (status === "active") return <Badge variant="success" className="text-[10px]">{t("accounts.statusActive")}</Badge>;
    if (status === "expired") return <Badge variant="destructive" className="text-[10px]">{t("accounts.statusExpired")}</Badge>;
    if (status === "rate_limited") return <Badge variant="warning" className="text-[10px]">{t("accounts.statusRateLimited")}</Badge>;
    if (status === "inactive") return <Badge variant="secondary" className="text-[10px]">{t("accounts.statusInactive")}</Badge>;
    return <Badge variant="outline" className="text-[10px]">{status}</Badge>;
  };

  const supportedPlatformEntries = useMemo(
    () => platforms.filter((p) => p.kind !== "unsupported"),
    [platforms]
  );
  const unsupportedPlatformEntries = useMemo(
    () => platforms.filter((p) => p.kind === "unsupported"),
    [platforms]
  );

  // --- 签到中心：可签到账号（按平台分组）；zcode / qoder 账号走脚本插件签到，也作为一组展示 ---
  const claimableAccounts = useMemo(() => accounts.filter((a) => a.supports_claim && !isQoderCard(a)), [accounts]);
  const zcodeAccounts = useMemo(() => accounts.filter((a) => a.platform === "zcode"), [accounts]);
  const qoderAccounts = useMemo(() => accounts.filter((a) => isQoderCard(a) && a.supports_claim), [accounts]);
  const claimGroups = useMemo<ClaimGroup[]>(() => {
    const groups: ClaimGroup[] = [];
    const map = new Map<string, AccountCard[]>();
    for (const acc of claimableAccounts) {
      const list = map.get(acc.platform) || [];
      list.push(acc);
      map.set(acc.platform, list);
    }
    for (const [platform, cards] of map) {
      groups.push({
        key: platform,
        platform,
        label: cards[0].platform_label || cards[0].platform,
        cards,
        kind: "native",
      });
    }
    // zcode：即使插件未装也展示账号分组（组内置灰 + 提示），由 zcode-claim 插件领卡
    if (zcodeAccounts.length) {
      groups.push({
        key: "zcode",
        platform: "zcode",
        label: zcodeAccounts[0].platform_label || "ZCode",
        cards: zcodeAccounts,
        kind: "zcode",
      });
    } else if (zcodePlugin) {
      // 没有已接入的 zcode 账号时仍保留一个平台级入口
      groups.push({
        key: "zcode",
        platform: "zcode",
        label: t("accounts.zcodeSection"),
        cards: [],
        kind: "zcode",
      });
    }
    // qoder / qodercn：插件未装也展示分组（组内置灰 + 提示），由 qoder-claim 插件签到 + 领 Pro 福利
    if (qoderAccounts.length) {
      for (const platform of ["qoder", "qodercn"]) {
        const cards = qoderAccounts.filter((a) => a.platform === platform);
        if (!cards.length) continue
        groups.push({
          key: `qoder_group_${platform}`,
          platform,
          label: cards[0].platform_label || platform,
          cards,
          kind: "qoder",
        });
      }
    }
    return groups;
  }, [claimableAccounts, zcodeAccounts, qoderAccounts, zcodePlugin, t]);
  const signedToday = (acc: AccountCard): boolean => {
    if (locallyClaimed[String(acc.id)]) return true;
    const today = new Date().toDateString();
    if (acc.daily_claimed_at) {
      const ms = acc.daily_claimed_at > 1e12 ? acc.daily_claimed_at : acc.daily_claimed_at * 1000;
      if (new Date(ms).toDateString() === today) return true;
    }
    const checkin = acc.quota?.checkin;
    if (checkin?.claimed) {
      if (!checkin.claimed_at) return true;
      const ms = checkin.claimed_at > 1e12 ? checkin.claimed_at : checkin.claimed_at * 1000;
      return new Date(ms).toDateString() === today;
    }
    return false;
  };

  return (
    <div className="flex flex-col gap-6 xl:flex-row">
      {/* Main: account cards */}
      <div className="min-w-0 flex-1 space-y-5">
        <PageHeader
          title={t("accounts.title")}
          description={t("accounts.subtitle")}
          actions={
            <>
              <Button variant="outline" size="sm" className="gap-1.5" onClick={() => setNamesHidden((v) => !v)}
                aria-label={namesHidden ? t("accounts.privacyOff") : t("accounts.privacyOn")}>
                {namesHidden ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                <span className="hidden sm:inline">{namesHidden ? t("accounts.privacyOff") : t("accounts.privacyOn")}</span>
              </Button>
              <Button variant="outline" size="sm" className="gap-1.5" onClick={() => setClaimDialogOpen(true)}>
                <Gift className="h-3.5 w-3.5" />
                {t("accounts.claimAll")}
              </Button>
              <Button variant="outline" size="sm" className="gap-1.5" onClick={() => syncGroupsMut.mutate()}
                disabled={syncGroupsMut.isPending}>
                {syncGroupsMut.isPending ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Puzzle className="h-3.5 w-3.5" />}
                {t("accounts.syncGroups")}
              </Button>
              <Button size="sm" className="gap-1.5" onClick={() => refreshAllMut.mutate()} disabled={refreshAllMut.isPending || !accounts.length}>
                {refreshAllMut.isPending ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                {t("accounts.refreshAll")}
              </Button>
            </>
          }
        />

        {platformPoints.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            <span className="font-medium text-muted-foreground">{t("accounts.platformPoints")}</span>
            {platformPoints.map((p) => (
              <Badge key={p.label} variant="outline" className="gap-1.5 font-normal">
                <PlatformIcon platform={p.label} className="h-3.5 w-3.5 rounded-sm" />
                <span>{p.label}</span>
                <span className="font-mono font-semibold text-foreground">{p.display}</span>
              </Badge>
            ))}
          </div>
        )}

        {isLoading ? (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            {[0, 1].map((i) => <div key={i} className="h-44 animate-pulse rounded-xl bg-foreground/5" />)}
          </div>
        ) : accounts.length === 0 ? (
          <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed py-14 text-center">
            <UserRound className="h-8 w-8 text-muted-foreground/50" />
            <p className="text-sm font-medium">{t("accounts.empty")}</p>
            <p className="text-xs text-muted-foreground">{t("accounts.emptyHint")}</p>
          </div>
        ) : (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            {accounts.map((acc) => {
              const quota = acc.quota;
              const metrics: Metric[] = quota?.metrics || [];
              const identity = namesHidden ? ACCOUNT_NAME_MASK : (acc.identity || acc.platform_label);
              return (
                <Card key={String(acc.id)} className="card-hover">
                  <CardHeader className="pb-1.5">
                    <div className="flex items-center gap-2.5">
                      <PlatformIcon platform={acc.platform} identity={acc.identity} />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <p className="truncate text-sm font-bold">{acc.platform_label}</p>
                          {acc.via_plugin && (
                            <Badge variant="info" className="shrink-0 text-[10px]" title={t("accounts.pluginBadgeHint")}>
                              <Puzzle className="mr-0.5 h-2.5 w-2.5" />{t("accounts.pluginBadge")}
                            </Badge>
                          )}
                          {acc.status !== "active" && statusBadge(acc.status)}
                        </div>
                      </div>
                      <div className="flex flex-none items-center gap-0.5">
                        {acc.platform === "zcode" && (
                          <Button variant="ghost" size="icon" className="h-7 w-7 text-success"
                            aria-label={t("accounts.claim")} title={t("accounts.zcodeCardClaimTitle")}
                            onClick={() => zcodeClaimMut.mutate(acc.id as number)}
                            disabled={!zcodeReady || zcodeClaimMut.isPending}>
                            {zcodeClaimMut.isPending && zcodeClaimMut.variables === acc.id
                              ? <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                              : <Gift className="h-3.5 w-3.5" />}
                          </Button>
                        )}
                        {isQoderCard(acc) && (
                          <Button variant="ghost" size="icon" className="h-7 w-7 text-success"
                            aria-label={t("accounts.claim")}
                            title={qoderReady ? t("accounts.qoderCardClaimTitle") : t("accounts.qoderPluginRequired")}
                            onClick={() => qoderClaimMut.mutate(acc)}
                            disabled={!qoderReady || qoderClaimMut.isPending}>
                            {qoderClaimMut.isPending && qoderClaimMut.variables?.id === acc.id
                              ? <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                              : <Gift className="h-3.5 w-3.5" />}
                          </Button>
                        )}
                        {acc.supports_quota && (
                          <Button variant="ghost" size="icon" className="h-7 w-7" aria-label={t("accounts.refresh")}
                            onClick={() => refreshMut.mutate(acc)} disabled={refreshMut.isPending}>
                            <RefreshCw className={"h-3.5 w-3.5" + (refreshMut.isPending && refreshMut.variables?.id === acc.id ? " animate-spin" : "")} />
                          </Button>
                        )}
                        {acc.supports_claim && !isQoderCard(acc) && acc.platform !== "zcode" && (
                          <Button variant="ghost" size="icon" className="h-7 w-7 text-success" aria-label={t("accounts.claim")}
                            onClick={() => claimMut.mutate(acc)} disabled={claimMut.isPending} title={t("accounts.claim")}>
                            <Gift className="h-3.5 w-3.5" />
                          </Button>
                        )}
                        <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive" aria-label={t("accounts.delete")}
                          onClick={() => setConfirmDelete(acc)}>
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    </div>
                    <div className="mt-1 flex items-baseline justify-between gap-2">
                      <p className="truncate text-lg font-bold leading-tight">{identity}</p>
                      {acc.plan && <span className="flex-none text-sm text-muted-foreground">{acc.plan}</span>}
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-3 pt-1">
                    {metrics.length > 0 ? (
                      metrics.map((m, i) => <MetricBar key={i} metric={m} t={t} />)
                    ) : quota?.notice ? (
                      <p className="text-xs text-amber-600 dark:text-amber-400">{quota.notice}</p>
                    ) : acc.supports_quota ? (
                      <p className="text-xs text-muted-foreground">{t("accounts.quotaNeverFetched")}</p>
                    ) : null}
                    {acc.quota_error && (
                      <p className="text-xs text-destructive">{t("accounts.quotaError")}: {acc.quota_error}</p>
                    )}
                  </CardContent>
                </Card>
              );
            })}
          </div>
        )}
      </div>

      {/* Aside: add account panel */}
      <aside className="w-full shrink-0 space-y-3 xl:w-80">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-base">
              <UserRound className="h-4 w-4 text-primary" />
              {t("accounts.addAccount")}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-1 pt-0">
            {supportedPlatformEntries.map((p) => {
              const disabled = p.kind === "import" || (p.kind === "oauth" && !p.login_ready);
              const needsPlugin = p.kind === "plugin" && !p.login_ready;
              return (
                <button
                  key={p.id}
                  type="button"
                  disabled={disabled}
                  onClick={() => startLogin(p)}
                  title={needsPlugin ? (p.note || t("accounts.pluginNotInstalled")) : p.note}
                  className="group flex w-full items-center gap-2.5 rounded-lg px-2 py-2 text-left transition-colors hover:bg-foreground/5 disabled:opacity-50 disabled:hover:bg-transparent"
                >
                  <PlatformIcon platform={p.id} identity={p.label} className="h-6 w-6 flex-none rounded object-contain" />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">{p.label}</p>
                    <p className="truncate text-[11px] text-muted-foreground">{needsPlugin ? t("accounts.pluginNotInstalled") : p.domain}</p>
                  </div>
                  {p.account_count > 0 && (
                    <span className="flex-none rounded-full bg-primary/12 px-1.5 text-[10px] font-semibold text-primary">{p.account_count}</span>
                  )}
                  {p.kind === "import" ? (
                    <ExternalLink className="h-3.5 w-3.5 flex-none text-muted-foreground" />
                  ) : (
                    <Plus className="h-4 w-4 flex-none text-muted-foreground transition-colors group-hover:text-primary" />
                  )}
                </button>
              );
            })}
            {unsupportedPlatformEntries.length > 0 && (
              <>
                <p className="px-2 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70">
                  {t("accounts.unsupported")}
                </p>
                {unsupportedPlatformEntries.map((p) => (
                  <a key={p.id} href={p.website} target="_blank" rel="noreferrer"
                    className="flex w-full items-center gap-2.5 rounded-lg px-2 py-2 opacity-50 transition-colors hover:bg-foreground/5 hover:opacity-80">
                    <PlatformIcon platform={p.id} identity={p.label} className="h-6 w-6 flex-none rounded object-contain" />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">{p.label}</p>
                      <p className="truncate text-[11px] text-muted-foreground">{p.domain}</p>
                    </div>
                    <ExternalLink className="h-3.5 w-3.5 flex-none text-muted-foreground" />
                  </a>
                ))}
              </>
            )}
          </CardContent>
        </Card>

        {/* Plugin card (echobird-plugin 同步) */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-base">
              <Puzzle className="h-4 w-4 text-primary" />
              {t("accounts.plugins")}
              {pluginsSnapshot.updates && pluginsSnapshot.updates.length > 0 && (
                <Badge variant="warning" className="text-[10px]">{pluginsSnapshot.updates.length} {t("accounts.pluginUpdateAvailable")}</Badge>
              )}
            </CardTitle>
            <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">{t("accounts.pluginsHint")}</p>
          </CardHeader>
          <CardContent className="space-y-1 pt-0">
            {pluginEntries.length === 0 && (
              <p className="py-2 text-xs text-muted-foreground">
                {pluginsSnapshot.sync?.error ? `${t("accounts.pluginSyncFailed")}: ${pluginsSnapshot.sync.error}` : t("accounts.pluginSyncNever")}
              </p>
            )}
            {pluginEntries.map((p) => {
              const installed = p.installed_version != null;
              const running = taskPhase === "running" && pluginsSnapshot.task?.plugin_id === p.id;
              return (
                <div key={p.id} className="flex items-center gap-2 rounded-lg px-2 py-1.5">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <p className="truncate text-xs font-medium">{p.display_name || p.name}</p>
                      {installed && p.enabled ? (
                        <Badge variant="success" className="shrink-0 text-[9px]">{t("accounts.pluginEnabled")}</Badge>
                      ) : installed ? (
                        <Badge variant="secondary" className="shrink-0 text-[9px]">{t("accounts.pluginDisabled")}</Badge>
                      ) : null}
                      {p.update_available && !running && (
                        <Badge variant="warning" className="shrink-0 text-[9px]">{t("accounts.pluginUpdateAvailable")}</Badge>
                      )}
                    </div>
                    <p className="truncate text-[10px] text-muted-foreground">
                      {installed ? `v${p.installed_version}${p.update_available ? ` → v${p.latest_version}` : ""}` : t("accounts.pluginNotInstalled")}
                    </p>
                  </div>
                  <div className="flex flex-none items-center gap-1">
                    {installed && (
                      <Switch checked={!!p.enabled} onCheckedChange={(v) => pluginToggleMut.mutate({ id: p.id, enabled: v })}
                        aria-label={t("accounts.pluginEnabled")} className="scale-90" />
                    )}
                    {installed ? (
                      <>
                        {p.update_available && (
                          <Button variant="outline" size="sm" className="h-7 gap-1 px-2 text-[11px]"
                            onClick={() => pluginInstallMut.mutate(p.id)} disabled={taskPhase === "running"}>
                            {running ? <RefreshCw className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />}
                            {t("accounts.pluginUpdate")}
                          </Button>
                        )}
                        <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive" aria-label={t("accounts.pluginUninstall")}
                          onClick={() => setConfirmUninstall(p)}>
                          <Trash2 className="h-3 w-3" />
                        </Button>
                      </>
                    ) : (
                      <Button variant="outline" size="sm" className="h-7 gap-1 px-2 text-[11px]"
                        onClick={() => pluginInstallMut.mutate(p.id)} disabled={taskPhase === "running"}>
                        {running ? <RefreshCw className="h-3 w-3 animate-spin" /> : <Plus className="h-3 w-3" />}
                        {t("accounts.pluginInstall")}
                      </Button>
                    )}
                  </div>
                </div>
              );
            })}
            <div className="flex items-center justify-between pt-2">
              <span className="text-[10px] text-muted-foreground">
                {pluginsSnapshot.sync?.checked_at
                  ? new Date(pluginsSnapshot.sync.checked_at * 1000).toLocaleString(zh ? "zh-CN" : "en-US")
                  : t("accounts.pluginSyncNever")}
              </span>
              <Button variant="outline" size="sm" className="h-7 gap-1 px-2 text-[11px]"
                onClick={() => checkUpdatesMut.mutate()} disabled={checkUpdatesMut.isPending}>
                {checkUpdatesMut.isPending ? <RefreshCw className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
                {t("accounts.pluginCheckUpdates")}
              </Button>
            </div>
            {taskPhase === "running" && (
              <p className="flex items-center gap-1.5 pt-1 text-[11px] text-muted-foreground">
                <RefreshCw className="h-3 w-3 animate-spin" />
                {t("accounts.pluginTaskRunning")} {pluginsSnapshot.task?.plugin_id?.split(".").pop()}
              </p>
            )}
          </CardContent>
        </Card>
      </aside>

      {/* 签到中心弹窗：按平台列出可签到账号，支持单独签到与签到全部 */}
      <Dialog open={claimDialogOpen} onOpenChange={setClaimDialogOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{t("accounts.claimCenter")}</DialogTitle>
            <DialogDescription>{t("accounts.claimCenterDesc")}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <Button className="w-full gap-1.5" onClick={() => claimAllMut.mutate()}
              disabled={claimAllMut.isPending || claimGroups.length === 0}>
              {claimAllMut.isPending ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Gift className="h-3.5 w-3.5" />}
              {t("accounts.claimAll")}
            </Button>
            {claimGroups.length === 0 ? (
              <p className="rounded-lg border border-dashed py-8 text-center text-sm text-muted-foreground">
                {t("accounts.claimEmpty")}
              </p>
            ) : (
              <div className="max-h-[60vh] space-y-2 overflow-y-auto pr-1">
                {claimGroups.map((group) => {
                  const expanded = !!expandedGroups[group.key];
                  const isZcode = group.kind === "zcode";
                  const isQoder = group.kind === "qoder";
                  const pluginKind = isZcode ? "zcode" : (isQoder ? "qoder" : null);
                  const pluginState = isZcode ? zcodeState : qoderState;
                  const pluginReady = isZcode ? zcodeReady : qoderReady;
                  const pluginBlocked = !!pluginKind && !pluginReady;
                  const pluginMut = isZcode ? zcodeClaimMut : qoderClaimMut;
                  const pluginPending = !!pluginMut.isPending;
                  const pluginId = isZcode ? "zcode-claim" : "qoder-claim";
                  const Icon = expanded ? ChevronDown : ChevronRight;
                  return (
                    <div key={group.key} className="overflow-hidden rounded-lg border">
                      {/* 分组头：左侧点击折叠/展开，右侧单独签到该平台全部账号 */}
                      <div className="flex w-full items-center gap-2 px-2.5 py-2 transition-colors hover:bg-foreground/5">
                        <button type="button" onClick={() => toggleGroup(group.key)}
                          className="flex min-w-0 flex-1 items-center gap-2 text-left">
                          <Icon className="h-3.5 w-3.5 flex-none text-muted-foreground" />
                          <PlatformIcon platform={group.platform} identity={group.label} className="h-5 w-5 flex-none rounded object-contain" />
                          <p className="min-w-0 flex-1 truncate text-xs font-semibold">{group.label}</p>
                          {pluginKind && (
                            <Badge variant={pluginReady ? "success" : "secondary"} className="shrink-0 text-[10px]">
                              {pluginState === "ready"
                                ? (zh ? "插件就绪" : "Plugin ready")
                                : pluginState === "plugin_disabled"
                                  ? (zh ? "插件已停用" : "Plugin disabled")
                                  : (zh ? "需安装插件" : "Plugin required")}
                            </Badge>
                          )}
                        </button>
                        <Button variant="ghost" size="icon" className="h-6 w-6 shrink-0 text-success"
                          aria-label={t("accounts.claimPlatform", { platform: group.label })}
                          title={t("accounts.claimPlatform", { platform: group.label })}
                          onClick={() => pluginKind ? pluginMut.mutate() : claimAllMut.mutate(group.platform)}
                          disabled={pluginKind ? (pluginBlocked || pluginPending) : claimAllMut.isPending}>
                          {(pluginKind ? pluginPending : (claimAllMut.isPending && claimAllMut.variables === group.platform))
                            ? <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                            : <Gift className="h-3.5 w-3.5" />}
                        </Button>
                        <span className="rounded-full bg-foreground/10 px-1.5 text-[10px] text-muted-foreground">{group.cards.length}</span>
                      </div>
                      {/* 分组内容（展开后显示） */}
                      {expanded && (
                        <div className="space-y-1 border-t px-2 py-2">
                          {pluginKind && (
                            <div className="px-0.5 pb-1">
                              <p className="text-[10px] text-muted-foreground">
                                {isZcode
                                  ? (pluginState === "ready"
                                      ? (zh ? "由 zcode-claim 插件通过 HTTP 领取免费卡，无需启动本地客户端。" : "Free cards are claimed over HTTP by the zcode-claim plugin; no local client needed.")
                                      : pluginState === "plugin_disabled"
                                        ? (zh ? "zcode-claim 插件已停用，请在「脚本插件」中启用。" : "zcode-claim plugin is disabled. Enable it in Script Plugins.")
                                        : (zh ? "请先安装并启用 zcode-claim 脚本插件后再领卡。" : "Install and enable the zcode-claim script plugin before claiming."))
                                  : (pluginState === "ready"
                                      ? (zh ? "由 qoder-claim 插件每日签到并领取 Pro 福利，签到流水可在插件执行记录中查看。" : "Daily check-in and Pro benefit are handled by the qoder-claim plugin; see its run log for the activity trail.")
                                      : pluginState === "plugin_disabled"
                                        ? (zh ? "qoder-claim 插件已停用，请在「脚本插件」中启用。" : "qoder-claim plugin is disabled. Enable it in Script Plugins.")
                                        : (zh ? "请先安装并启用 qoder-claim 脚本插件后再签到。" : "Install and enable the qoder-claim script plugin before checking in."))}
                              </p>
                            </div>
                          )}
                          {group.cards.length === 0 ? (
                            <Button variant="outline" size="sm" className="w-full gap-1.5 text-[11px]"
                              onClick={() => pluginMut.mutate()} disabled={pluginBlocked || pluginPending}>
                              {pluginPending ? <RefreshCw className="h-3 w-3 animate-spin" /> : <Gift className="h-3 w-3" />}
                              {t("accounts.claimNow")}
                            </Button>
                          ) : group.cards.map((acc) => {
                            const done = signedToday(acc);
                            const nativePending = claimMut.isPending && claimMut.variables?.id === acc.id;
                            const pending = pluginKind
                              ? (pluginMut.isPending && (pluginMut.variables === acc.id || pluginMut.variables?.id === acc.id))
                              : nativePending;
                            const disabled = pluginKind
                              ? (pluginBlocked || pluginPending)
                              : (done || claimMut.isPending);
                            return (
                              <div key={String(acc.id)} className="flex items-center gap-2.5 rounded-lg border px-2.5 py-2">
                                <div className="min-w-0 flex-1">
                                  <p className="truncate text-sm font-medium">{namesHidden ? ACCOUNT_NAME_MASK : acc.identity}</p>
                                  {acc.via_plugin && <p className="text-[10px] text-muted-foreground">{t("accounts.pluginBadge")}</p>}
                                </div>
                                {done && (
                                  <Badge variant="success" className="shrink-0 text-[10px]">
                                    <Check className="mr-0.5 h-2.5 w-2.5" />{t("accounts.alreadyClaimed")}
                                  </Badge>
                                )}
                                <Button variant="outline" size="sm" className="h-7 shrink-0 gap-1 px-2 text-[11px]"
                                  onClick={() => pluginKind
                                    ? (isZcode ? pluginMut.mutate(acc.id as number) : pluginMut.mutate(acc))
                                    : claimMut.mutate(acc)}
                                  disabled={disabled} title={pluginBlocked ? (
                                    pluginState === "plugin_disabled"
                                      ? (zh ? `${pluginId} 插件已停用` : `${pluginId} plugin disabled`)
                                      : (zh ? `请先安装 ${pluginId} 插件` : `Install the ${pluginId} plugin first`)
                                  ) : undefined}>
                                  {pending ? <RefreshCw className="h-3 w-3 animate-spin" /> : <Gift className="h-3 w-3" />}
                                  {t("accounts.claimNow")}
                                </Button>
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* Login dialog */}
      <Dialog open={login !== null} onOpenChange={(o) => {
        if (!o && login) {
          if (loginTimer.current) clearTimeout(loginTimer.current);
          if (login.echo) cancelEchoLogin(login.sessionId).catch(() => {}); else if (login.plugin) cancelPluginLogin(login.sessionId).catch(() => {});
          setLogin(null);
        }
      }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{login ? t("accounts.loginTitle", { platform: login.label }) : ""}</DialogTitle>
            <DialogDescription>{t("accounts.loginStep1")}<br />{t("accounts.loginStep2")}</DialogDescription>
          </DialogHeader>
          {login && (
            <div className="space-y-3">
              {login.awaitingClientExit && (
                <p className="rounded-lg border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
                  {t("accounts.pluginAwaitingClientExit")}
                </p>
              )}
              {login.userCode && (
                <div className="rounded-lg border bg-muted/40 p-3 text-center">
                  <p className="text-xs text-muted-foreground">{t("accounts.userCodeHint")}</p>
                  <p className="mt-1 font-mono text-2xl font-bold tracking-[0.3em]">{login.userCode}</p>
                </div>
              )}
              {login.url ? (
                <Button size="sm" className="w-full gap-1.5" onClick={() => openAuthUrl(login.url)}>
                  <ExternalLink className="h-3.5 w-3.5" />
                  {t("accounts.reopenAuthPage")}
                </Button>
              ) : null}
              <div className="flex gap-2">
                {login.url ? (
                  <Button size="sm" variant="outline" className="flex-1 gap-1.5"
                    onClick={() => { if (loginTimer.current) clearTimeout(loginTimer.current); if (login.echo) cancelEchoLogin(login.sessionId).catch(() => {}); else if (login.plugin) cancelPluginLogin(login.sessionId).catch(() => {}); setLogin(null); }}>
                    <X className="h-3.5 w-3.5" />
                    {t("common.cancel")}
                  </Button>
                ) : (
                  <Button size="sm" variant="outline" className="w-full gap-1.5"
                    onClick={() => { if (loginTimer.current) clearTimeout(loginTimer.current); if (login.echo) cancelEchoLogin(login.sessionId).catch(() => {}); else if (login.plugin) cancelPluginLogin(login.sessionId).catch(() => {}); setLogin(null); }}>
                    <X className="h-3.5 w-3.5" />
                    {t("common.cancel")}
                  </Button>
                )}
              </div>
              <div className="flex items-center justify-center gap-2 text-xs text-muted-foreground">
                <RefreshCw className="h-3 w-3 animate-spin" />
                {t("accounts.waiting")}
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={confirmDelete !== null}
        onOpenChange={(o) => !o && setConfirmDelete(null)}
        title={t("accounts.delete")}
        description={confirmDelete ? `${confirmDelete.platform_label} · ${namesHidden ? ACCOUNT_NAME_MASK : confirmDelete.identity}` : ""}
        confirmLabel={t("common.delete")}
        cancelLabel={t("common.cancel")}
        loading={deleteMut.isPending}
        onConfirm={() => { if (confirmDelete) deleteMut.mutate(confirmDelete); }}
      />

      <ConfirmDialog
        open={confirmUninstall !== null}
        onOpenChange={(o) => !o && setConfirmUninstall(null)}
        title={t("accounts.pluginUninstall")}
        description={confirmUninstall ? (confirmUninstall.display_name || confirmUninstall.id) : ""}
        confirmLabel={t("accounts.pluginUninstall")}
        cancelLabel={t("common.cancel")}
        loading={pluginUninstallMut.isPending}
        onConfirm={() => { if (confirmUninstall) pluginUninstallMut.mutate(confirmUninstall.id); }}
      />
    </div>
  );
}
