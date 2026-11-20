import { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  getScriptPlugins, checkScriptCatalog, installScriptPlugin, getScriptPluginTask,
  enableScriptPlugin, disableScriptPlugin, uninstallScriptPlugin, runScriptPlugin,
  getScriptPluginAudit, getScriptPluginSettings, saveScriptPluginSettings,
  getPluginAccounts, runScriptPluginBatch, getScriptRunTask,
  getScheduleTasks, createScheduleTask, updateScheduleTask, deleteScheduleTask,
  toggleScheduleTask, runScheduleTaskNow, getScheduleRuns, getScheduleStats,
  restartPluginService,
} from "../services/api";
import { useI18n } from "../i18n";
import { toast } from "../stores/toast";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import {
  Table, TableHeader, TableBody, TableRow, TableHead, TableCell,
} from "../components/ui/table";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Textarea } from "../components/ui/textarea";
import { Label } from "../components/ui/label";
import { Badge } from "../components/ui/badge";
import { Switch } from "../components/ui/switch";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "../components/ui/tabs";
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from "../components/ui/select";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "../components/ui/dialog";
import { PageHeader, ConfirmDialog } from "../components/shared";
import {
  Puzzle, Download, Trash2, Play, Loader2, RefreshCw, ShieldCheck, Code2,
  CheckCircle2, XCircle, ScrollText, Rocket, Settings2, Save,
  CalendarClock, Plus, Pencil, PlayCircle, Clock, Ban,
} from "lucide-react";

type Perm = string;

type SettingDef = {
  key: string;
  type: "string" | "integer" | "number" | "boolean" | "select" | "textarea" | "password";
  label: string | { zh?: string; en?: string };
  description?: string | { zh?: string; en?: string };
  placeholder?: string | { zh?: string; en?: string };
  default?: any;
  required?: boolean;
  options?: { value: string; label: string | { zh?: string; en?: string } }[];
  min?: number;
  max?: number;
};

type PluginItem = {
  id: string;
  name: string;
  description: string;
  version: string;
  permissions: Perm[];
  enabled?: boolean;
  has_settings?: boolean;
  installed_version?: string | null;
  download_url?: string;
  installed_at?: number;
  type?: string;            // script | apiserver
  platform?: string;        // windows.x64 / linux.arm64 / ...
  service?: { binary?: string; config?: string };
};

type RunResult = {
  ok: boolean;
  plugin_id: string;
  result?: any;
  logs?: string[];
  error?: string;
  elapsed?: number;
};

type WorkbuddyAccount = {
  id: string;
  name: string;
  plan: string;
  expires_at?: number;
  has_token?: boolean;
};

type BatchAccountResult = {
  account_id: string;
  name: string;
  ok: boolean;
  result?: any;
  error?: string;
  logs?: string[];
};

type BatchTask = {
  task_id: string;
  plugin_id: string;
  phase: "idle" | "running" | "success" | "error";
  total: number;
  done: number;
  current: string;
  results: BatchAccountResult[];
  error?: string;
};

type SettingsData = { plugin_id: string; schema: SettingDef[]; values: Record<string, any> };

function localized(v: string | { zh?: string; en?: string } | undefined, zh: boolean): string {
  if (v == null) return "";
  if (typeof v === "str)") return v;
  return v[zh ? "zh" : "en"] || v.zh || v.en || "";
}

function PermissionBadges({ permissions, t }: { permissions: Perm[]; t: (k: string) => string }) {
  if (!permissions || permissions.length === 0) {
    return <Badge variant="outline" className="text-[10px]">{t("scriptPlugins.noPermission")}</Badge>;
  }
  return (
    <div className="flex flex-wrap gap-1">
      {permissions.map((p) => (
        <Badge key={p} variant="secondary" className="text-[10px] font-normal">
          <ShieldCheck className="mr-1 h-3 w-3" />
          {t("scriptPlugins.perm." + p) !== "scriptPlugins.perm." + p ? t("scriptPlugins.perm." + p) : p}
        </Badge>
      ))}
    </div>
  );
}

type SchedTask = {
  id: number; name: string; plugin_id: string; params: string; account_ids: string;
  all_accounts: boolean; schedule_type: "cron" | "interval" | "once";
  cron_expr: string; interval_minutes: number; run_at: string; enabled: boolean;
  last_run_at?: string | null; next_run_at?: string; last_ok?: boolean | null;
};
type SchedRun = {
  id: number; task_id: number; task_name: string; plugin_id: string; trigger: string;
  account_id: string; account_name: string; ok: boolean; result: string;
  error: string; elapsed: number; fired_at?: string | null;
};

const CRON_PRESETS = [
  { value: "*/5 * * * *", label: "每 5 分钟" },
  { value: "*/15 * * * *", label: "每 15 分钟" },
  { value: "0 * * * *", label: "每小时" },
  { value: "0 9 * * *", label: "每天 09:00" },
  { value: "0 9,21 * * *", label: "每天 09:00 / 21:00" },
  { value: "0 9 * * 1-5", label: "工作日 09:00" },
];

function SchedulePanel({ installed, t }: { installed: PluginItem[]; t: (k: string, o?: any) => string }) {
  const qc = useQueryClient();
  // apiserver 服务型插件是常驻进程，不支持定时任务，从下拉中排除
  const schedPlugins = installed.filter((p) => p.type !== "apiserver");
  const tasksQ = useQuery({ queryKey: ["schedTasks"], queryFn: () => getScheduleTasks().then((r) => r.data) });
  const statsQ = useQuery({ queryKey: ["schedStats"], queryFn: () => getScheduleStats().then((r) => r.data), refetchInterval: 10000 });
  const tasks: SchedTask[] = tasksQ.data?.tasks || [];

  const emptyForm = () => ({
    id: 0 as number,
    name: "",
    plugin_id: schedPlugins[0]?.id || "",
    schedule_type: "cron" as "cron" | "interval" | "once",
    cron_expr: "0 9 * * *",
    interval_minutes: 60,
    run_at: "",
    params: "{}",
    account_ids: [] as string[],
    all_accounts: false,
    enabled: true,
  });
  const [form, setForm] = useState(emptyForm);
  const [editing, setEditing] = useState(false);
  const [formError, setFormError] = useState("");

  // 表单所选插件的账号（多选）
  const accQ = useQuery({
    queryKey: ["schedAccounts", form.plugin_id],
    queryFn: () => getPluginAccounts(form.plugin_id).then((r) => r.data),
    enabled: !!form.plugin_id,
  });
  const accs: WorkbuddyAccount[] = accQ.data?.accounts || [];

  const invalidate = () => { tasksQ.refetch(); statsQ.refetch(); qc.invalidateQueries({ queryKey: ["schedRuns"] }); };

  const saveMut = useMutation({
    mutationFn: () => {
      let params = {};
      try { params = JSON.parse(form.params || "{}"); } catch { throw new Error(t("scriptPlugins.schedInvalidParams")); }
      if (!form.plugin_id) throw new Error(t("scriptPlugins.schedNeedPlugin"));
      const payload = {
        name: form.name || form.plugin_id, plugin_id: form.plugin_id, params,
        account_ids: form.account_ids, all_accounts: form.all_accounts,
        schedule_type: form.schedule_type, cron_expr: form.cron_expr,
        interval_minutes: Number(form.interval_minutes) || 60, run_at: form.run_at,
        enabled: form.enabled,
      };
      return editing ? updateScheduleTask(form.id, payload) : createScheduleTask(payload);
    },
    onSuccess: () => {
      toast({ title: editing ? t("scriptPlugins.schedUpdated") : t("scriptPlugins.schedCreated"), variant: "success" });
      setForm(emptyForm()); setEditing(false); setFormError(""); invalidate();
    },
    onError: (e: any) => setFormError(e?.response?.data?.detail || e?.message || String(e)),
  });

  const toggleMut = useMutation({
    mutationFn: (id: number) => toggleScheduleTask(id),
    onSuccess: invalidate,
  });
  const delMut = useMutation({
    mutationFn: (id: number) => deleteScheduleTask(id),
    onSuccess: invalidate,
  });
  const runMut = useMutation({
    mutationFn: (id: number) => runScheduleTaskNow(id, { all_accounts: true }),
    onSuccess: () => { toast({ title: t("scriptPlugins.schedTriggered"), variant: "success" }); },
    onError: (e: any) => toast({ title: t("scriptPlugins.schedTriggerFailed"), description: e?.response?.data?.detail, variant: "destructive" }),
  });

  const editTask = (x: SchedTask) => {
    setEditing(true);
    setForm({
      id: x.id, name: x.name, plugin_id: x.plugin_id, schedule_type: x.schedule_type,
      cron_expr: x.cron_expr || "0 9 * * *", interval_minutes: x.interval_minutes || 60,
      run_at: x.run_at || "", params: x.params || "{}",
      account_ids: (() => { try { return JSON.parse(x.account_ids || "[]"); } catch { return []; } })(),
      all_accounts: x.all_accounts, enabled: x.enabled,
    });
    setFormError("");
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  const toggleAcc = (id: string) =>
    setForm((f) => ({ ...f, account_ids: f.account_ids.includes(id) ? f.account_ids.filter((x) => x !== id) : [...f.account_ids, id] }));

  // 执行日志：可按任务筛选，运行中轮询
  const [logTask, setLogTask] = useState<number | null>(null);
  const runsQ = useQuery({
    queryKey: ["schedRuns", logTask],
    queryFn: () => getScheduleRuns(logTask, 100).then((r) => r.data?.runs || []),
    refetchInterval: 5000,
  });
  const runs: SchedRun[] = runsQ.data || [];

  const scheduleLabel = (x: SchedTask) => {
    if (x.schedule_type === "cron") return x.cron_expr;
    if (x.schedule_type === "interval") return t("scriptPlugins.schedEveryMinutes", { n: x.interval_minutes });
    return x.run_at;
  };
  const fmtTime = (s?: string | null) => s ? s.replace("T", " ").slice(0, 19) : "-";

  return (
    <div className="space-y-4">
      {/* 1) 定时任务设置 */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-base">
            <CalendarClock className="h-4 w-4" />
            {t("scriptPlugins.schedSettingsTitle")}
            <Badge variant="outline" className="text-[10px] font-normal">{t("scriptPlugins.schedSettingsHint")}</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid gap-3 md:grid-cols-2">
            <div className="space-y-1.5">
              <Label className="text-xs">{t("scriptPlugins.schedPlugin")}</Label>
              <Select value={form.plugin_id} onValueChange={(v) => setForm((f) => ({ ...f, plugin_id: v, account_ids: [] }))}>
                <SelectTrigger><SelectValue placeholder={t("scriptPlugins.schedSelectPlugin")} /></SelectTrigger>
                <SelectContent>
                  {schedPlugins.map((p) => <SelectItem key={p.id} value={p.id}>{p.name} ({p.version})</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">{t("scriptPlugins.schedName")}</Label>
              <Input value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                placeholder={t("scriptPlugins.schedNamePlaceholder")} />
            </div>
          </div>

          <div className="grid gap-3 md:grid-cols-3">
            <div className="space-y-1.5">
              <Label className="text-xs">{t("scriptPlugins.schedType")}</Label>
              <Select value={form.schedule_type} onValueChange={(v: any) => setForm((f) => ({ ...f, schedule_type: v }))}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="cron">{t("scriptPlugins.schedTypeCron")}</SelectItem>
                  <SelectItem value="interval">{t("scriptPlugins.schedTypeInterval")}</SelectItem>
                  <SelectItem value="once">{t("scriptPlugins.schedTypeOnce")}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {form.schedule_type === "cron" && (
              <div className="space-y-1.5 md:col-span-2">
                <Label className="text-xs">{t("scriptPlugins.schedCron")}</Label>
                <div className="flex gap-2">
                  <Select value={CRON_PRESETS.some((p) => p.value === form.cron_expr) ? form.cron_expr : "__custom"}
                    onValueChange={(v) => { if (v !== "__custom") setForm((f) => ({ ...f, cron_expr: v })); }}>
                    <SelectTrigger className="w-44"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {CRON_PRESETS.map((p) => <SelectItem key={p.value} value={p.value}>{p.label}</SelectItem>)}
                      <SelectItem value="__custom">{t("scriptPlugins.schedCustom")}</SelectItem>
                    </SelectContent>
                  </Select>
                  <Input value={form.cron_expr} onChange={(e) => setForm((f) => ({ ...f, cron_expr: e.target.value }))}
                    placeholder="*/5 * * * *" className="font-mono" />
                </div>
              </div>
            )}
            {form.schedule_type === "interval" && (
              <div className="space-y-1.5 md:col-span-2">
                <Label className="text-xs">{t("scriptPlugins.schedInterval")}</Label>
                <Input type="number" min={1} value={form.interval_minutes}
                  onChange={(e) => setForm((f) => ({ ...f, interval_minutes: Number(e.target.value) }))} />
              </div>
            )}
            {form.schedule_type === "once" && (
              <div className="space-y-1.5 md:col-span-2">
                <Label className="text-xs">{t("scriptPlugins.schedRunAt")}</Label>
                <Input type="datetime-local" value={form.run_at ? form.run_at.replace(" ", "T") : ""}
                  onChange={(e) => setForm((f) => ({ ...f, run_at: e.target.value.replace("T", " ") }))} />
              </div>
            )}
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs">{t("scriptPlugins.params")} (JSON)</Label>
            <Textarea value={form.params} onChange={(e) => setForm((f) => ({ ...f, params: e.target.value }))}
              placeholder='{"action":"all"}' rows={2} className="font-mono text-xs" />
          </div>

          <div className="rounded-lg border p-3 space-y-2">
            <div className="flex items-center justify-between">
              <Label className="text-xs font-medium">{t("scriptPlugins.schedAccounts")}</Label>
              <label className="flex items-center gap-2 text-xs text-muted-foreground">
                <input type="checkbox" checked={form.all_accounts}
                  onChange={(e) => setForm((f) => ({ ...f, all_accounts: e.target.checked, account_ids: e.target.checked ? [] : f.account_ids }))} />
                {t("scriptPlugins.schedAllAccounts")}
              </label>
            </div>
            {!form.all_accounts && (
              <div className="grid grid-cols-2 gap-2 max-h-40 overflow-auto">
                {accs.length === 0 && <div className="col-span-2 text-xs text-muted-foreground">{t("scriptPlugins.schedNoAccounts")}</div>}
                {accs.map((a) => (
                  <label key={a.id} className="flex items-center gap-2 rounded border px-2 py-1.5 text-xs cursor-pointer">
                    <input type="checkbox" checked={form.account_ids.includes(a.id)} onChange={() => toggleAcc(a.id)} />
                    <span className="truncate">{a.name}</span>
                  </label>
                ))}
              </div>
            )}
          </div>

          <div className="flex items-center gap-4">
            <label className="flex items-center gap-2 text-xs">
              <Switch checked={form.enabled} onCheckedChange={(v) => setForm((f) => ({ ...f, enabled: v }))} />
              {t("scriptPlugins.schedEnabled")}
            </label>
            <div className="flex gap-2 ml-auto">
              {editing && <Button variant="ghost" size="sm" onClick={() => { setForm(emptyForm()); setEditing(false); setFormError(""); }}>{t("scriptPlugins.schedCancel")}</Button>}
              <Button size="sm" className="gap-1.5" onClick={() => saveMut.mutate()} disabled={saveMut.isPending}>
                {saveMut.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : (editing ? <Save className="h-3.5 w-3.5" /> : <Plus className="h-3.5 w-3.5" />)}
                {editing ? t("scriptPlugins.schedSave") : t("scriptPlugins.schedCreate")}
              </Button>
            </div>
          </div>
          {formError && <p className="text-xs text-destructive">{formError}</p>}
        </CardContent>
      </Card>

      {/* 2) 定时任务列表 */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-base">
            <Clock className="h-4 w-4" />
            {t("scriptPlugins.schedListTitle")}
            {statsQ.data && <Badge variant="secondary" className="text-[10px] font-normal">
              {statsQ.data.enabled}/{statsQ.data.total} {t("scriptPlugins.schedEnabledShort")}
            </Badge>}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {tasks.length === 0 ? (
            <div className="py-6 text-center text-xs text-muted-foreground">{t("scriptPlugins.schedEmpty")}</div>
          ) : (
            <div className="overflow-auto rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("scriptPlugins.schedName")}</TableHead>
                    <TableHead>{t("scriptPlugins.schedPlugin")}</TableHead>
                    <TableHead>{t("scriptPlugins.schedSchedule")}</TableHead>
                    <TableHead>{t("scriptPlugins.schedAccounts")}</TableHead>
                    <TableHead>{t("scriptPlugins.schedNextRun")}</TableHead>
                    <TableHead>{t("scriptPlugins.schedStatus")}</TableHead>
                    <TableHead className="text-right">{t("scriptPlugins.schedActions")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {tasks.map((x) => (
                    <TableRow key={x.id}>
                      <TableCell className="text-xs font-medium">{x.name}</TableCell>
                      <TableCell className="text-xs font-mono">{x.plugin_id}</TableCell>
                      <TableCell className="text-xs font-mono">{scheduleLabel(x)}</TableCell>
                      <TableCell className="text-xs">{x.all_accounts ? t("scriptPlugins.schedAllAccounts") : (() => { try { return JSON.parse(x.account_ids || "[]").length; } catch { return 0; } })()}</TableCell>
                      <TableCell className="text-xs">{fmtTime(x.next_run_at)}</TableCell>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <Switch checked={x.enabled} onCheckedChange={() => toggleMut.mutate(x.id)} />
                          {x.last_ok === true ? <CheckCircle2 className="h-3.5 w-3.5 text-success" />
                            : x.last_ok === false ? <XCircle className="h-3.5 w-3.5 text-destructive" /> : null}
                        </div>
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-1">
                          <Button variant="ghost" size="icon" className="h-7 w-7" title={t("scriptPlugins.schedRunNow")}
                            onClick={() => runMut.mutate(x.id)} disabled={runMut.isPending}>
                            <PlayCircle className="h-3.5 w-3.5" />
                          </Button>
                          <Button variant="ghost" size="icon" className="h-7 w-7" title={t("scriptPlugins.schedEdit")} onClick={() => editTask(x)}>
                            <Pencil className="h-3.5 w-3.5" />
                          </Button>
                          <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive" title={t("scriptPlugins.schedDelete")}
                            onClick={() => { if (window.confirm(t("scriptPlugins.schedDeleteConfirm"))) delMut.mutate(x.id); }}>
                            <Trash2 className="h-3.5 w-3.5" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* 3) 定时任务执行日志 */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-base">
            <ScrollText className="h-4 w-4" />
            {t("scriptPlugins.schedLogsTitle")}
            <div className="ml-auto w-56">
              <Select value={String(logTask ?? "__all")} onValueChange={(v) => setLogTask(v === "__all" ? null : Number(v))}>
                <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="__all">{t("scriptPlugins.schedAllTasks")}</SelectItem>
                  {tasks.map((x) => <SelectItem key={x.id} value={String(x.id)}>{x.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {runs.length === 0 ? (
            <div className="py-6 text-center text-xs text-muted-foreground">{t("scriptPlugins.schedNoRuns")}</div>
          ) : (
            <div className="overflow-auto rounded-lg border max-h-80">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("scriptPlugins.schedTime")}</TableHead>
                    <TableHead>{t("scriptPlugins.schedTask")}</TableHead>
                    <TableHead>{t("scriptPlugins.schedTrigger")}</TableHead>
                    <TableHead>{t("scriptPlugins.schedAccount")}</TableHead>
                    <TableHead>{t("scriptPlugins.schedResult")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {runs.map((r) => (
                    <TableRow key={r.id}>
                      <TableCell className="text-xs whitespace-nowrap">{fmtTime(r.fired_at)}</TableCell>
                      <TableCell className="text-xs">{r.task_name}</TableCell>
                      <TableCell className="text-xs"><Badge variant="outline" className="text-[10px]">{r.trigger}</Badge></TableCell>
                      <TableCell className="text-xs">{r.account_name || r.account_id || "-"}</TableCell>
                      <TableCell className="text-xs">
                        {r.ok ? <CheckCircle2 className="inline h-3.5 w-3.5 text-success mr-1" />
                          : <XCircle className="inline h-3.5 w-3.5 text-destructive mr-1" />}
                        <span className="font-mono text-[11px]">{(r.error || r.result || "").slice(0, 120)}</span>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

export default function ScriptPlugins() {
  const { t, locale } = useI18n();
  const zh = locale.startsWith("zh");
  const qc = useQueryClient();
  const [tab, setTab] = useState("local");

  const q = useQuery({
    queryKey: ["scriptPlugins"],
    queryFn: () => getScriptPlugins().then((r) => r.data),
    refetchInterval: 5000,
  });
  const data = q.data || {};
  const installed: PluginItem[] = data.installed || [];
  const onlineAvailable: PluginItem[] = data.online_available || [];
  const taskPhase = data.task?.phase || "idle";

  // 安装任务轮询
  useEffect(() => {
    if (taskPhase !== "running") return;
    const timer = setInterval(() => {
      getScriptPluginTask().then((r) => {
        qc.setQueryData(["scriptPlugins"], (prev: any) => prev ? { ...prev, task: r.data } : prev);
        if (r.data?.phase === "success") {
          toast({ title: t("scriptPlugins.installSuccess"), description: r.data.message, variant: "success" });
          q.refetch();
        } else if (r.data?.phase === "error") {
          toast({ title: t("scriptPlugins.installFailed"), description: r.data.error, variant: "destructive" });
          q.refetch();
        }
      }).catch(() => {});
    }, 1200);
    return () => clearInterval(timer);
  }, [taskPhase]);

  const checkMut = useMutation({
    mutationFn: () => checkScriptCatalog(),
    onSuccess: (res) => {
      qc.setQueryData(["scriptPlugins"], (prev: any) => ({ ...(prev || {}), online: res.data?.plugins || [] }));
      toast({
        title: res.data?.plugins?.length ? t("scriptPlugins.catalogLoaded") : t("scriptPlugins.catalogEmpty"),
        variant: res.data?.plugins?.length ? "success" : "default",
      });
      q.refetch();
    },
    onError: (e: any) => toast({ title: t("scriptPlugins.catalogFailed"), description: e?.response?.data?.detail, variant: "destructive" }),
  });

  const installMut = useMutation({
    mutationFn: (pluginId: string) => installScriptPlugin({ plugin_id: pluginId }),
    onSuccess: () => {
      qc.setQueryData(["scriptPlugins"], (prev: any) => prev ? { ...prev, task: { phase: "running" } } : prev);
    },
    onError: (e: any) => toast({ title: t("scriptPlugins.installFailed"), description: e?.response?.data?.detail, variant: "destructive" }),
  });

  const toggleMut = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      enabled ? enableScriptPlugin(id) : disableScriptPlugin(id),
    onSuccess: () => q.refetch(),
  });

  // apiserver 型插件：手动重启服务
  const restartSvcMut = useMutation({
    mutationFn: ({ id }: { id: string }) => restartPluginService(id),
    onSuccess: () => { toast({ title: t("scriptPlugins.svcRestarted"), variant: "success" }); q.refetch(); },
    onError: (e: any) => toast({ title: t("scriptPlugins.svcRestartFailed"), description: e?.response?.data?.detail, variant: "destructive" }),
  });

  const [uninstallTarget, setUninstallTarget] = useState<PluginItem | null>(null);
  const uninstallMut = useMutation({
    mutationFn: (id: string) => uninstallScriptPlugin(id),
    onSuccess: () => { setUninstallTarget(null); q.refetch(); toast({ title: t("scriptPlugins.uninstalled"), variant: "success" }); },
  });

  // 执行弹窗
  const [runTarget, setRunTarget] = useState<PluginItem | null>(null);
  const [runParams, setRunParams] = useState("{}");
  const [runResult, setRunResult] = useState<RunResult | null>(null);
  const [runError, setRunError] = useState("");
  const runMut = useMutation({
    mutationFn: () => {
      let params = {};
      try { params = JSON.parse(runParams || "{}"); } catch { throw new Error(t("scriptPlugins.invalidParams")); }
      return runScriptPlugin(runTarget!.id, params).then((r) => r.data as RunResult);
    },
    onSuccess: (res) => setRunResult(res),
    onError: (e: any) => {
      setRunError(e?.response?.data?.detail || e?.message || String(e));
      setRunResult(null);
    },
  });

  // 批量执行：项目内已登录账号选择（多选/全选），后台串行执行 + 轮询进度
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [batchTask, setBatchTask] = useState<BatchTask | null>(null);
  const [batchError, setBatchError] = useState("");
  const accountsQ = useQuery({
    queryKey: ["pluginAccounts", runTarget?.id],
    queryFn: () => getPluginAccounts(runTarget!.id).then((r) => r.data),
    enabled: !!runTarget,
  });
  const accountSource: string = accountsQ.data?.source || "workbuddy";
  const accounts: WorkbuddyAccount[] = accountsQ.data?.accounts || [];
  const allSelected = accounts.length > 0 && selectedIds.length === accounts.length;
  const batchMut = useMutation({
    mutationFn: () => {
      let params = {};
      try { params = JSON.parse(runParams || "{}"); } catch { throw new Error(t("scriptPlugins.invalidParams")); }
      return runScriptPluginBatch(runTarget!.id, { account_ids: selectedIds, params }).then((r) => r.data);
    },
    onSuccess: (data: any) => {
      setBatchTask({
        task_id: data.task_id, plugin_id: runTarget!.id, phase: "running",
        total: data.total || selectedIds.length, done: 0, current: "", results: [],
      });
      setBatchError("");
    },
    onError: (e: any) => setBatchError(e?.response?.data?.detail || e?.message || String(e)),
  });
  useEffect(() => {
    if (!batchTask?.task_id || batchTask.phase !== "running") return;
    const timer = setInterval(async () => {
      try {
        const r = await getScriptRunTask(batchTask.task_id);
        const d = r.data as BatchTask;
        setBatchTask(d);
        if (d.phase !== "running") clearInterval(timer);
      } catch { /* 轮询失败保持原状态，下次继续 */ }
    }, 1500);
    return () => clearInterval(timer);
  }, [batchTask?.task_id, batchTask?.phase]);

  const openRun = (p: PluginItem) => {
    setRunTarget(p); setRunParams("{}"); setRunResult(null); setRunError("");
    setSelectedIds([]); setBatchTask(null); setBatchError("");
  };
  const toggleAccount = (id: string) =>
    setSelectedIds((prev) => prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]);
  const toggleAll = () => setSelectedIds(allSelected ? [] : accounts.map((a) => a.id));

  // 设置弹窗
  const [settingsTarget, setSettingsTarget] = useState<PluginItem | null>(null);
  const [settingsDraft, setSettingsDraft] = useState<Record<string, any>>({});
  const settingsQ = useQuery({
    queryKey: ["scriptPluginSettings", settingsTarget?.id],
    queryFn: () => getScriptPluginSettings(settingsTarget!.id).then((r) => r.data as SettingsData),
    enabled: !!settingsTarget,
  });
  useEffect(() => {
    if (settingsQ.data?.values) setSettingsDraft({ ...settingsQ.data.values });
  }, [settingsQ.data]);
  const settingsMut = useMutation({
    mutationFn: () => saveScriptPluginSettings(settingsTarget!.id, settingsDraft),
    onSuccess: () => {
      toast({ title: t("scriptPlugins.settingsSaved"), variant: "success" });
      setSettingsTarget(null);
      q.refetch();
    },
    onError: (e: any) => toast({ title: t("scriptPlugins.settingsSaveFailed"), description: e?.response?.data?.detail, variant: "destructive" }),
  });
  const setSetting = (key: string, value: any) => setSettingsDraft((d) => ({ ...d, [key]: value }));

  // 审计
  const [auditOpen, setAuditOpen] = useState(false);
  const auditQ = useQuery({
    queryKey: ["scriptPluginAudit"],
    queryFn: () => getScriptPluginAudit(100).then((r) => r.data?.records || []),
    enabled: auditOpen,
  });

  const renderSettingField = (s: SettingDef) => {
    const label = localized(s.label, zh) || s.key;
    const desc = localized(s.description, zh);
    const ph = localized(s.placeholder, zh);
    const value = settingsDraft[s.key];
    return (
      <div key={s.key} className="space-y-1.5 rounded-lg border p-3">
        <div className="flex items-center justify-between gap-2">
          <Label className="text-xs font-medium">
            {label}{s.required && <span className="ml-0.5 text-destructive">*</span>}
          </Label>
          <span className="text-[10px] text-muted-foreground">{s.type}</span>
        </div>
        {desc && <p className="text-[11px] text-muted-foreground">{desc}</p>}
        {s.type === "boolean" ? (
          <div className="flex items-center gap-2">
            <Switch checked={!!value} onCheckedChange={(v) => setSetting(s.key, v)} />
            <span className="text-xs text-muted-foreground">{value ? t("scriptPlugins.settingsOn") : t("scriptPlugins.settingsOff")}</span>
          </div>
        ) : s.type === "select" ? (
          <Select value={value == null ? "" : String(value)} onValueChange={(v) => setSetting(s.key, v)}>
            <SelectTrigger className="h-8 text-xs"><SelectValue placeholder={ph || t("scriptPlugins.settingsSelect")} /></SelectTrigger>
            <SelectContent>
              {(s.options || []).map((o) => (
                <SelectItem key={o.value} value={o.value}>{localized(o.label, zh) || o.value}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : s.type === "textarea" ? (
          <Textarea value={value == null ? "" : String(value)} onChange={(e) => setSetting(s.key, e.target.value)}
            placeholder={ph} className="min-h-20 text-xs font-mono" />
        ) : (
          <Input type={s.type === "password" ? "password" : (s.type === "integer" || s.type === "number") ? "number" : "str)"}
            value={value == null ? "" : String(value)}
            onChange={(e) => {
              const raw = e.target.value;
              if (s.type === "integer" || s.type === "number") setSetting(s.key, raw === "" ? "" : Number(raw));
              else setSetting(s.key, raw);
            }}
            placeholder={ph} min={s.min} max={s.max} className="h-8 text-xs" />
        )}
      </div>
    );
  };

  const renderPlugin = (p: PluginItem, online: boolean) => {
    const running = taskPhase === "running" && data.task?.plugin_id === p.id;
    return (
      <div key={p.id} className="rounded-lg border bg-card p-4">
        <div className="flex items-start gap-3">
          <div className="flex h-9 w-9 flex-none items-center justify-center rounded-md bg-primary/10 text-primary">
            {online ? <Download className="h-4 w-4" /> : <Code2 className="h-4 w-4" />}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <p className="truncate text-sm font-semibold">{p.name}</p>
              <Badge variant="outline" className="text-[10px]">v{p.version || p.installed_version || "-"}</Badge>
              {p.type === "apiserver" && (
                <Badge variant="success" className="text-[10px]">apiserver{p.platform ? ` · ${p.platform}` : ""}</Badge>
              )}
              {!online && p.type === "apiserver" && (
                <Badge variant={data.service_status?.[p.id]?.running ? "success" : "secondary"} className="text-[10px]">
                  {data.service_status?.[p.id]?.running ? t("scriptPlugins.svcRunning") : t("scriptPlugins.svcStopped")}
                </Badge>
              )}
              {!online && p.enabled === false && (
                <Badge variant="secondary" className="text-[10px]">{t("scriptPlugins.disabled")}</Badge>
              )}
              {online && p.installed_version && (
                <Badge variant="success" className="text-[10px]">{t("scriptPlugins.installed")}</Badge>
              )}
            </div>
            <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{p.description || t("scriptPlugins.noDescription")}</p>
            {!online && p.type === "apiserver" && (() => {
              const ss = data.service_status?.[p.id];
              if (!ss?.url) return null;
              return (
                <p className="mt-1.5 font-mono text-[11px] text-primary">
                  {ss.url} <span className="text-muted-foreground">· key: {ss.key ? "••••" : "—"}</span>
                </p>
              );
            })()}
            <div className="mt-2">
              <PermissionBadges permissions={p.permissions || []} t={t} />
            </div>
          </div>
        </div>
        <div className="mt-3 flex items-center gap-2 border-t pt-3">
          {online ? (
            <Button size="sm" variant="outline" className="gap-1" onClick={() => installMut.mutate(p.id)}
              disabled={taskPhase === "running" || !!p.installed_version}>
              {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
              {p.installed_version ? t("scriptPlugins.installed") : t("scriptPlugins.install")}
            </Button>
          ) : (
            <>
              {p.type === "apiserver" ? (
                <Button size="sm" variant="outline" className="gap-1" onClick={() => restartSvcMut.mutate({ id: p.id })}
                  disabled={p.enabled === false || restartSvcMut.isPending}>
                  {restartSvcMut.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                  {t("scriptPlugins.svcRestart")}
                </Button>
              ) : (
                <Button size="sm" className="gap-1" onClick={() => openRun(p)} disabled={p.enabled === false}>
                  <Play className="h-3.5 w-3.5" />{t("scriptPlugins.run")}
                </Button>
              )}
              {p.has_settings && (
                <Button size="sm" variant="outline" className="gap-1" onClick={() => setSettingsTarget(p)}>
                  <Settings2 className="h-3.5 w-3.5" />{t("scriptPlugins.settings")}
                </Button>
              )}
              <div className="ml-auto flex items-center gap-2">
                <Switch checked={p.enabled !== false}
                  onCheckedChange={(v) => toggleMut.mutate({ id: p.id, enabled: v })}
                  aria-label={t("scriptPlugins.enable")} />
                <Button size="icon" variant="ghost" className="h-8 w-8 text-destructive"
                  onClick={() => setUninstallTarget(p)} aria-label={t("scriptPlugins.uninstall")}>
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>
            </>
          )}
          {online && <span className="ml-auto text-[10px] text-muted-foreground">ID: {p.id}</span>}
        </div>
      </div>
    );
  };

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <PageHeader title={t("scriptPlugins.title")} description={t("scriptPlugins.subtitle")} />

      <Tabs value={tab} onValueChange={setTab}>
        <div className="flex items-center justify-between gap-3">
          <TabsList>
            <TabsTrigger value="local" className="gap-1.5">
              <Puzzle className="h-3.5 w-3.5" />{t("scriptPlugins.tabLocal")}
              <span className="ml-1 rounded bg-muted px-1.5 text-[10px]">{installed.length}</span>
            </TabsTrigger>
            <TabsTrigger value="online" className="gap-1.5">
              <Rocket className="h-3.5 w-3.5" />{t("scriptPlugins.tabOnline")}
              <span className="ml-1 rounded bg-muted px-1.5 text-[10px]">{onlineAvailable.length}</span>
            </TabsTrigger>
          </TabsList>
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" className="gap-1" onClick={() => setAuditOpen(true)}>
              <ScrollText className="h-3.5 w-3.5" />{t("scriptPlugins.audit")}
            </Button>
            <Button variant="outline" size="sm" className="gap-1" onClick={() => checkMut.mutate()}
              disabled={checkMut.isPending}>
              {checkMut.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
              {t("scriptPlugins.refreshCatalog")}
            </Button>
          </div>
        </div>

        <TabsContent value="local" className="mt-4">
          {installed.length === 0 ? (
            <Card><CardContent className="py-12 text-center text-sm text-muted-foreground">
              {t("scriptPlugins.noLocal")}
            </CardContent></Card>
          ) : (
            <div className="grid gap-3 md:grid-cols-2">{installed.map((p) => renderPlugin(p, false))}</div>
          )}
        </TabsContent>

        <TabsContent value="online" className="mt-4">
          <p className="mb-3 text-xs text-muted-foreground">{t("scriptPlugins.catalogHint")}</p>
          {onlineAvailable.length === 0 ? (
            <Card><CardContent className="py-12 text-center text-sm text-muted-foreground">
              {data.catalog?.error ? `${t("scriptPlugins.catalogFailed")}: ${data.catalog.error}` : t("scriptPlugins.noOnline")}
            </CardContent></Card>
          ) : (
            <div className="grid gap-3 md:grid-cols-2">{onlineAvailable.map((p) => renderPlugin(p, true))}</div>
          )}
        </TabsContent>
      </Tabs>

      {/* 定时任务：设置 / 列表 / 执行日志 */}
      <SchedulePanel installed={installed} t={t} />

      {/* 执行弹窗 */}
      <Dialog open={!!runTarget} onOpenChange={(o) => !o && setRunTarget(null)}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Play className="h-4 w-4 text-primary" />{t("scriptPlugins.run")} · {runTarget?.name}
            </DialogTitle>
            <DialogDescription>{t("scriptPlugins.runHint")}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            {/* 项目内已登录账号选择（多选/全选） */}
            {accountsQ.data && accounts.length > 0 && (
              <div className="rounded-lg border p-3">
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-xs font-medium">
                    {accountSource === "zcode"
                      ? (zh ? "选择 ZCode 账号（可多选）" : "Select ZCode accounts (multi-select)")
                      : (zh ? "选择已登录账号（可多选）" : "Select signed-in accounts (multi-select)")}
                  </span>
                  <label className="flex cursor-pointer items-center gap-1.5 text-xs text-primary">
                    <input type="checkbox" checked={allSelected} onChange={toggleAll} />
                    {zh ? "全选" : "Select all"}（{selectedIds.length}/{accounts.length}）
                  </label>
                </div>
                <div className="grid max-h-48 grid-cols-2 gap-1.5 overflow-auto">
                  {accounts.map((a) => {
                    const checked = selectedIds.includes(a.id);
                    return (
                      <label key={a.id}
                        className={`flex cursor-pointer items-center gap-2 rounded-md border px-2 py-1.5 text-xs ${checked ? "border-primary bg-primary/10" : ""}`}>
                        <input type="checkbox" checked={checked} onChange={() => toggleAccount(a.id)} />
                        <span className="flex-1 truncate">{a.name}</span>
                        <span className="text-[10px] text-muted-foreground">{a.plan}</span>
                      </label>
                    );
                  })}
                </div>
                <p className="mt-2 text-[11px] text-muted-foreground">
                  {zh ? "选中账号后将串行逐个执行（后台运行，可关闭弹窗）。" : "Selected accounts run one by one in the background; you may close this dialog."}
                </p>
              </div>
            )}

            {/* 高级参数（action / budget_seconds 等公共参数） */}
            <div>
              <label className="mb-1 block text-xs font-medium">
                {t("scriptPlugins.params")} <span className="font-normal text-muted-foreground">({zh ? "高级，可选" : "advanced, optional"})</span>
              </label>
              <Input value={runParams} onChange={(e) => setRunParams(e.target.value)} className="font-mono text-xs"
                placeholder={accountSource === "zcode" ? '{"action": "status"}' : '{"action": "daily"}'}
                disabled={batchMut.isPending || runMut.isPending} />
            </div>

            {/* 按钮区：有选中账号走批量；否则用默认登录态单次执行 */}
            <div className="flex gap-2">
              <Button className="flex-1 gap-1"
                onClick={() => { setBatchError(""); setBatchTask(null); batchMut.mutate(); }}
                disabled={batchMut.isPending || selectedIds.length === 0 || batchTask?.phase === "running"}>
                {batchMut.isPending || batchTask?.phase === "running" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                {zh ? `执行选中账号（${selectedIds.length}）` : `Run selected (${selectedIds.length})`}
              </Button>
              <Button variant="outline" className="gap-1"
                onClick={() => { setRunError(""); setRunResult(null); runMut.mutate(); }}
                disabled={runMut.isPending || batchMut.isPending || batchTask?.phase === "running"}>
                {runMut.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                {zh ? "默认登录态执行" : "Run (default session)"}
              </Button>
            </div>

            {/* 错误 */}
            {(runError || batchError) && (
              <div className="flex items-start gap-2 rounded-md border border-destructive/50 bg-destructive/10 p-3 text-xs text-destructive">
                <XCircle className="mt-0.5 h-4 w-4 flex-none" />
                <pre className="whitespace-pre-wrap">{runError || batchError}</pre>
              </div>
            )}

            {/* 单次执行结果 */}
            {runResult && (
              <div className="space-y-2">
                <div className="flex items-center gap-2 text-xs">
                  {runResult.ok ? <CheckCircle2 className="h-4 w-4 text-emerald-500" /> : <XCircle className="h-4 w-4 text-destructive" />}
                  <span className="font-medium">{runResult.ok ? t("scriptPlugins.runSuccess") : t("scriptPlugins.runFailed")}</span>
                  {runResult.elapsed != null && <span className="text-muted-foreground">({runResult.elapsed}s)</span>}
                </div>
                {runResult.error && <pre className="whitespace-pre-wrap rounded-md bg-destructive/10 p-3 text-xs text-destructive">{runResult.error}</pre>}
                {runResult.logs && runResult.logs.length > 0 && (
                  <div className="rounded-md bg-muted p-3 font-mono text-[11px] leading-relaxed">
                    {runResult.logs.map((l, i) => <div key={i}>{l}</div>)}
                  </div>
                )}
                <pre className="max-h-64 overflow-auto rounded-md bg-muted p-3 text-[11px]">
                  {JSON.stringify(runResult.result ?? null, null, 2)}
                </pre>
              </div>
            )}

            {/* 批量执行进度 */}
            {batchTask && (
              <div className="space-y-2 rounded-lg border p-3">
                <div className="flex items-center justify-between text-xs">
                  <span className="font-medium">
                    {batchTask.phase === "running"
                      ? (zh ? `执行中… ${batchTask.done}/${batchTask.total}` : `Running… ${batchTask.done}/${batchTask.total}`)
                      : (zh ? `完成 ${batchTask.done}/${batchTask.total}` : `Done ${batchTask.done}/${batchTask.total}`)}
                  </span>
                  {batchTask.phase === "running" && <Loader2 className="h-4 w-4 animate-spin text-primary" />}
                  {batchTask.phase === "success" && <CheckCircle2 className="h-4 w-4 text-emerald-500" />}
                </div>
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                  <div className="h-full bg-primary transition-all"
                    style={{ width: `${batchTask.total ? Math.round(batchTask.done / batchTask.total * 100) : 0}%` }} />
                </div>
                {batchTask.current && batchTask.phase === "running" && (
                  <p className="text-[11px] text-muted-foreground">
                    {zh ? "当前账号" : "Current"}: {batchTask.current}
                  </p>
                )}
                <div className="max-h-64 space-y-1.5 overflow-auto">
                  {batchTask.results.map((r, i) => (
                    <div key={i} className="flex items-start gap-2 rounded-md bg-muted/60 p-2 text-xs">
                      {r.ok ? <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 flex-none text-emerald-500" />
                             : <XCircle className="mt-0.5 h-3.5 w-3.5 flex-none text-destructive" />}
                      <div className="min-w-0 flex-1">
                        <div className="font-medium">{r.name} <span className="font-normal text-muted-foreground">({r.account_id})</span></div>
                        <div className="text-[11px] text-muted-foreground">
                          {r.error || (r.result && (r.result.report || r.result.result)) || (r.ok ? "OK" : "Failed")}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* 设置弹窗 */}
      <Dialog open={!!settingsTarget} onOpenChange={(o) => !o && setSettingsTarget(null)}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Settings2 className="h-4 w-4 text-primary" />{t("scriptPlugins.settings")} · {settingsTarget?.name}
            </DialogTitle>
            <DialogDescription>{t("scriptPlugins.settingsHint")}</DialogDescription>
          </DialogHeader>
          <div className="max-h-[60vh] space-y-3 overflow-auto py-1">
            {settingsQ.isLoading && (
              <div className="flex items-center justify-center gap-2 py-8 text-xs text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />{t("scriptPlugins.settingsLoading")}
              </div>
            )}
            {settingsQ.data?.schema?.length ? settingsQ.data.schema.map(renderSettingField) : (
              !settingsQ.isLoading && <div className="py-8 text-center text-xs text-muted-foreground">{t("scriptPlugins.settingsEmpty")}</div>
            )}
          </div>
          <DialogFooter className="gap-2">
            <Button variant="outline" onClick={() => setSettingsTarget(null)}>{t("common.cancel")}</Button>
            <Button className="gap-1" onClick={() => settingsMut.mutate()}
              disabled={settingsMut.isPending || !settingsQ.data?.schema?.length}>
              {settingsMut.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              {t("scriptPlugins.settingsSave")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 卸载确认 */}
      <ConfirmDialog open={!!uninstallTarget} onOpenChange={(o) => !o && setUninstallTarget(null)}
        title={t("scriptPlugins.uninstallTitle")}
        description={t("scriptPlugins.uninstallDesc").replace("{name}", uninstallTarget?.name || "")}
        confirmLabel={t("scriptPlugins.uninstall")} loading={uninstallMut.isPending}
        onConfirm={() => uninstallTarget && uninstallMut.mutate(uninstallTarget.id)} />

      {/* 审计日志 */}
      <Dialog open={auditOpen} onOpenChange={setAuditOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2"><ScrollText className="h-4 w-4 text-primary" />{t("scriptPlugins.audit")}</DialogTitle>
            <DialogDescription>{t("scriptPlugins.auditHint")}</DialogDescription>
          </DialogHeader>
          <div className="max-h-[60vh] space-y-1 overflow-auto rounded-md bg-muted p-3 font-mono text-[11px]">
            {auditQ.data?.length ? auditQ.data.map((r: any, i: number) => (
              <div key={i} className="whitespace-pre-wrap">
                {new Date(r.ts * 1000).toLocaleString(zh ? "zh-CN" : "en-US")} [{r.plugin}] {r.action} {JSON.stringify(r)}
              </div>
            )) : <div className="text-muted-foreground">{t("scriptPlugins.noAudit")}</div>}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
