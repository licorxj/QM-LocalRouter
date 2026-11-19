import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  getAccounts, getAccountPlatforms, startAccountLogin, refreshAccount, claimAccount,
  deleteKey, getOauthStatus, syncModelGroups, claimAllAccounts,
  getZcodeClaimStatus, claimZcode,
  getPluginLoginStatus, cancelPluginLogin, refreshPluginAccount, claimPluginAccount, deletePluginAccount,
  getEchoLoginStatus, cancelEchoLogin, refreshEchoAccount, claimEchoAccount, deleteEchoAccount,
  getPlugins, checkPluginUpdates, installPlugin, getPluginTask, enablePlugin, disablePlugin, uninstallPlugin,
  updatePluginSettings,
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
type ZcodeClaimStatus = {
  installed: boolean; exe_path: string | null; source: string;
  node_available: boolean; node_version: string; node_ok: boolean;
  script_available: boolean; launch_hint: string;
  debug_port: number; debug_port_open: boolean;
};
type ClaimGroup = {
  key: string; label: string; platform: string;
  cards: AccountCard[]; kind: "native" | "zcode";
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

  const { data: platformsData } = useQuery({
    queryKey: ["accountPlatforms"],
    queryFn: () => getAccountPlatforms().then((r) => r.data),
  });
  const platforms: PlatformEntry[] = platformsData?.platforms || [];

  // ZCode 本地签到前置状态（安装 / Node 运行时），无权限或无客户端时置灰
  const { data: zcodeStatus } = useQuery({
    queryKey: ["zcodeClaimStatus"],
    queryFn: () => getZcodeClaimStatus().then((r) => r.data as ZcodeClaimStatus),
    staleTime: 60_000,
  });
  // zcode 状态：仅「未安装 / Node 不足」为硬阻塞；调试端口未开会由后端在签到时自动拉起
  const zcodeState = useMemo<"ready" | "not_installed" | "node" | "debug_port">(() => {
    if (!zcodeStatus || !zcodeStatus.installed) return "not_installed";
    if (!zcodeStatus.node_ok || !zcodeStatus.script_available) return "node";
    if (!zcodeStatus.debug_port_open) return "debug_port";
    return "ready";
  }, [zcodeStatus]);
  const zcodeReady = zcodeState === "ready" || zcodeState === "debug_port";

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
    mutationFn: (keyId?: number) => claimZcode(keyId),
    onSuccess: (res) => {
      const d = res.data || {};
      invalidate();
      qc.invalidateQueries({ queryKey: ["zcodeClaimStatus"] });
      const who = d.account ? `（${d.account}）` : "";
      if (d.ok) {
        // 只标记本次实际签到的账号，不再把全部 zcode 账号误标为已签
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

  // --- 签到中心：可签到账号（按平台分组）；zcode 账号走本地脚本签到，也作为一组展示 ---
  const claimableAccounts = useMemo(() => accounts.filter((a) => a.supports_claim), [accounts]);
  const zcodeAccounts = useMemo(() => accounts.filter((a) => a.platform === "zcode"), [accounts]);
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
    // zcode：即使未安装也展示账号分组（组内置灰 + 提示），每个账号单独触发本地脚本
    if (zcodeAccounts.length) {
      groups.push({
        key: "zcode",
        platform: "zcode",
        label: zcodeAccounts[0].platform_label || "ZCode",
        cards: zcodeAccounts,
        kind: "zcode",
      });
    } else if (zcodeStatus) {
      // 没有已接入的 zcode 账号时仍保留一个平台级入口
      groups.push({
        key: "zcode",
        platform: "zcode",
        label: t("accounts.zcodeSection"),
        cards: [],
        kind: "zcode",
      });
    }
    return groups;
  }, [claimableAccounts, zcodeAccounts, zcodeStatus, t]);
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
                        {acc.supports_quota && (
                          <Button variant="ghost" size="icon" className="h-7 w-7" aria-label={t("accounts.refresh")}
                            onClick={() => refreshMut.mutate(acc)} disabled={refreshMut.isPending}>
                            <RefreshCw className={"h-3.5 w-3.5" + (refreshMut.isPending && refreshMut.variables?.id === acc.id ? " animate-spin" : "")} />
                          </Button>
                        )}
                        {acc.supports_claim && (
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
                  const zcodeBlocked = isZcode && !zcodeReady;
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
                          {isZcode && (
                            <Badge variant={zcodeReady ? "success" : "secondary"} className="shrink-0 text-[10px]">
                              {zcodeState === "ready" ? t("accounts.zcodeInstalled")
                                : zcodeState === "not_installed" ? t("accounts.zcodeNotInstalled")
                                  : zcodeState === "debug_port" ? t("accounts.zcodeAutoStart")
                                    : t("accounts.zcodeNodeOutdated")}
                            </Badge>
                          )}
                        </button>
                        <Button variant="ghost" size="icon" className="h-6 w-6 shrink-0 text-success"
                          aria-label={t("accounts.claimPlatform", { platform: group.label })}
                          title={t("accounts.claimPlatform", { platform: group.label })}
                          onClick={() => claimAllMut.mutate(group.platform)}
                          disabled={(isZcode && !zcodeReady) || claimAllMut.isPending}>
                          {claimAllMut.isPending && claimAllMut.variables === group.platform
                            ? <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                            : <Gift className="h-3.5 w-3.5" />}
                        </Button>
                        <span className="rounded-full bg-foreground/10 px-1.5 text-[10px] text-muted-foreground">{group.cards.length}</span>
                      </div>
                      {/* 分组内容（展开后显示） */}
                      {expanded && (
                        <div className="space-y-1 border-t px-2 py-2">
                          {isZcode && (
                            <div className="px-0.5 pb-1">
                              <p className="text-[10px] text-muted-foreground">
                                {zcodeState === "ready"
                                  ? (zcodeStatus?.exe_path || t("accounts.zcodeClaimHint"))
                                  : zcodeState === "not_installed" ? t("accounts.zcodeInstallHint")
                                    : zcodeState === "debug_port" ? t("accounts.zcodeAutoStartHint")
                                      : `Node.js ${zcodeStatus.node_version || "—"} · >= 22 · ${t("accounts.zcodeNodeOutdated")}`}
                              </p>
                            </div>
                          )}
                          {group.cards.length === 0 ? (
                            <Button variant="outline" size="sm" className="w-full gap-1.5 text-[11px]"
                              onClick={() => zcodeClaimMut.mutate()} disabled={zcodeBlocked || zcodeClaimMut.isPending}>
                              {zcodeClaimMut.isPending ? <RefreshCw className="h-3 w-3 animate-spin" /> : <Gift className="h-3 w-3" />}
                              {t("accounts.claimNow")}
                            </Button>
                          ) : group.cards.map((acc) => {
                            const done = signedToday(acc);
                            const nativePending = claimMut.isPending && claimMut.variables?.id === acc.id;
                            const pending = isZcode
                              ? (zcodeClaimMut.isPending && zcodeClaimMut.variables === acc.id)
                              : nativePending;
                            const disabled = isZcode
                              ? (zcodeBlocked || zcodeClaimMut.isPending)
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
                                  onClick={() => isZcode ? zcodeClaimMut.mutate(acc.id) : claimMut.mutate(acc)}
                                  disabled={disabled} title={zcodeBlocked ? (
                                    zcodeState === "not_installed" ? t("accounts.zcodeNotInstalled")
                                      : zcodeState === "debug_port" ? t("accounts.zcodeDebugRequired")
                                        : t("accounts.zcodeNodeOutdated")
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
