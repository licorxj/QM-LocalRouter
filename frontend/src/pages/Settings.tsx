import React, { useState, useEffect } from "react";
import { useI18n, LOCALES } from "../i18n";
import { useTheme } from "../providers/theme";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  getBackupConfig, updateBackupConfig, createBackup,
  listBackups, restoreLocalBackup, deleteBackup, downloadBackupUrl,
  checkAppUpdate, applyAppUpdate, getUpdateStatus,
  getAutoStart, setAutoStart,
} from "../services/api";
import { toast } from "../stores/toast";
import { cn } from "../lib/utils";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Switch } from "../components/ui/switch";
import { Badge } from "../components/ui/badge";
import { Separator } from "../components/ui/separator";
import { Palette, Database, Download, Upload, Trash2, Loader2, HardDrive, Clock, FolderOpen, RefreshCw, ShieldCheck, Sun, Moon, MonitorCog, Github, CheckCircle2, Power } from "lucide-react";

/** Compact segmented control for mutually-exclusive small choices. */
function Segmented<T extends string>({ value, onChange, options, ariaLabel }: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: React.ReactNode; icon?: React.ReactNode }[];
  ariaLabel?: string;
}) {
  return (
    <div className="inline-flex rounded-lg border bg-muted/60 p-0.5" role="radiogroup" aria-label={ariaLabel}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={cn(
            "inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-xs font-medium transition-colors",
            value === o.value
              ? "bg-background text-foreground shadow-sm"
              : "text-muted-foreground hover:text-foreground"
          )}
        >
          {o.icon}
          {o.label}
        </button>
      ))}
    </div>
  );
}

export default function SettingsPage() {
  const { t, locale, setLocale } = useI18n();
  const { theme, setTheme } = useTheme();
  const [backing, setBacking] = useState(false);
  const [restoring, setRestoring] = useState<string | null>(null);
  const [deletingBackup, setDeletingBackup] = useState<string | null>(null);
  const [updateInfo, setUpdateInfo] = useState<any>(null);
  const [checkingUpdate, setCheckingUpdate] = useState(false);
  const [applying, setApplying] = useState(false);
  const [progress, setProgress] = useState<any>(null);

  const runCheck = async (fetchRemote = true) => {
    setCheckingUpdate(true);
    try {
      const r = await checkAppUpdate(fetchRemote);
      setUpdateInfo(r.data);
    } catch (e: any) {
      setUpdateInfo({ check_failed: true, error: e.response?.data?.detail || e.message });
    } finally {
      setCheckingUpdate(false);
    }
  };

  useEffect(() => { runCheck(true); }, []);

  const waitBackendRestart = () => {
    toast({ title: t("settings.updateRestarting"), variant: "success" });
    let waited = 0;
    const poll = async () => {
      try {
        const hr = await fetch("/api/health", { signal: AbortSignal.timeout(2000) });
        if (hr.ok) {
          toast({ title: t("settings.updateRestarted"), variant: "success" });
          setTimeout(() => window.location.reload(), 600);
          return;
        }
      } catch { /* not back yet */ }
      waited += 1;
      if (waited < 40) setTimeout(poll, 1000);
    };
    setTimeout(poll, 2500);
  };

  const runInstall = async () => {
    setApplying(true);
    const asset = updateInfo?.mode === "release" ? updateInfo?.asset?.name : undefined;
    try {
      const r = await applyAppUpdate(asset);
      const d = r.data || {};
      if (updateInfo?.mode === "release") {
        // release mode: poll the backend's download/apply progress
        const timer = setInterval(async () => {
          try {
            const s = await getUpdateStatus();
            setProgress(s.data);
            if (["installer_launched", "exiting", "ready"].includes(s.data.phase)) {
              clearInterval(timer);
              toast({ title: t("settings.installerLaunched"), variant: "success" });
              waitBackendRestart();
            } else if (s.data.phase === "error") {
              clearInterval(timer);
              setApplying(false);
              setProgress(null);
              toast({ title: t("settings.updateFailed"), description: s.data.error, variant: "destructive" });
            }
          } catch { /* transient poll failure */ }
        }, 800);
      } else if (d.restarting) {
        waitBackendRestart();
      } else if (d.updated) {
        toast({ title: t("settings.updateComplete"), variant: "success" });
        setApplying(false);
        runCheck(true);
      } else {
        toast({ title: t("settings.upToDate"), variant: "success" });
        setApplying(false);
      }
    } catch (e: any) {
      toast({ title: t("settings.updateFailed"), description: e.response?.data?.detail, variant: "destructive" });
      setApplying(false);
    }
  };

  const { data: backupConfig, refetch: refetchConfig } = useQuery({
    queryKey: ["backupConfig"],
    queryFn: () => getBackupConfig().then((r) => r.data),
  });

  const { data: backups = [], refetch: refetchBackups } = useQuery({
    queryKey: ["backups"],
    queryFn: () => listBackups().then((r) => r.data),
  });

  const [configForm, setConfigForm] = useState({
    backup_dir: "", auto_backup_enabled: false, auto_backup_interval_hours: 24, max_backups: 30,
  });

  useEffect(() => {
    if (backupConfig) {
      setConfigForm({
        backup_dir: backupConfig.backup_dir || "",
        auto_backup_enabled: backupConfig.auto_backup_enabled || false,
        auto_backup_interval_hours: backupConfig.auto_backup_interval_hours || 24,
        max_backups: backupConfig.max_backups || 30,
      });
    }
  }, [backupConfig]);

  const saveConfigMut = useMutation({
    mutationFn: () => updateBackupConfig(configForm),
    onSuccess: () => { refetchConfig(); toast({ title: t("settings.configSaved"), variant: "success" }); },
    onError: () => toast({ title: t("settings.configSaveFailed"), variant: "destructive" }),
  });

  // 开机自启动：以系统启动目录中的脚本是否存在为真实状态，切换即时生效
  const { data: autoStart, refetch: refetchAutoStart } = useQuery({
    queryKey: ["autoStart"],
    queryFn: () => getAutoStart().then((r) => r.data),
  });
  const autoStartMut = useMutation({
    mutationFn: (enabled: boolean) => setAutoStart(enabled),
    onSuccess: (res) => {
      refetchAutoStart();
      toast({
        title: res.data?.enabled ? t("settings.autoStartOn") : t("settings.autoStartOff"),
        variant: "success",
      });
    },
    onError: (e: any) => toast({
      title: t("settings.autoStartFailed"),
      description: e?.response?.data?.detail || e?.message,
      variant: "destructive",
    }),
  });

  const createBackupMut = useMutation({
    mutationFn: () => { setBacking(true); return createBackup(); },
    onSuccess: (res) => {
      setBacking(false); refetchBackups();
      toast({ title: t("settings.backupCreated"), description: res.data.filename + " (" + res.data.size_mb + "MB)", variant: "success" });
    },
    onError: () => { setBacking(false); toast({ title: t("settings.backupFailed"), variant: "destructive" }); },
  });

  const restoreMut = useMutation({
    mutationFn: (filename: string) => { setRestoring(filename); return restoreLocalBackup(filename); },
    onSuccess: () => { setRestoring(null); toast({ title: t("settings.restored"), description: t("settings.restoreHint"), variant: "success" }); },
    onError: () => { setRestoring(null); toast({ title: t("settings.restoreFailed"), variant: "destructive" }); },
  });

  const deleteBackupMut = useMutation({
    mutationFn: (filename: string) => { setDeletingBackup(filename); return deleteBackup(filename); },
    onSuccess: () => { setDeletingBackup(null); refetchBackups(); toast({ title: t("settings.deleted"), variant: "success" }); },
    onError: () => { setDeletingBackup(null); toast({ title: t("settings.deleteFailed"), variant: "destructive" }); },
  });

  const handleFileRestore = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!file.name.endsWith(".db")) { toast({ title: t("settings.selectDbFile"), variant: "destructive" }); return; }
    const formData = new FormData();
    formData.append("file", file);
    try {
      const resp = await fetch("/api/backup/restore", { method: "POST", body: formData });
      const data = await resp.json();
      if (data.success) { toast({ title: t("settings.restored"), description: t("settings.restoreHint"), variant: "success" }); }
      else { toast({ title: t("settings.restoreFailed"), variant: "destructive" }); }
    } catch { toast({ title: t("settings.restoreFailed"), variant: "destructive" }); }
    e.target.value = "";
  };

  const formatDate = (iso: string) => { try { return new Date(iso).toLocaleString(); } catch { return iso; } };

  return (
    <div className="space-y-6 max-w-3xl">

      {/* Appearance */}
      <Card className="card-hover">
        <CardHeader className="pb-3">
          <div className="flex items-center gap-2"><Palette className="h-4 w-4 text-primary" aria-hidden="true" /><CardTitle className="text-base">{t("settings.appearance")}</CardTitle></div>
        </CardHeader>
        <CardContent>
          <div className="flex items-center justify-between gap-3">
            <div>
              <Label className="text-sm font-medium">{t("settings.themeMode")}</Label>
              <p className="text-xs text-muted-foreground mt-0.5">{t("settings.themeDesc")}</p>
            </div>
            <Segmented
              ariaLabel={t("common.theme")}
              value={theme}
              onChange={setTheme}
              options={[
                { value: "light", label: t("common.themeLight"), icon: <Sun className="h-3.5 w-3.5" aria-hidden="true" /> },
                { value: "dark", label: t("common.themeDark"), icon: <Moon className="h-3.5 w-3.5" aria-hidden="true" /> },
                { value: "system", label: t("common.themeSystem"), icon: <MonitorCog className="h-3.5 w-3.5" aria-hidden="true" /> },
              ]}
            />
          </div>
          <div className="flex items-center justify-between mt-4 pt-4 border-t gap-3">
            <div>
              <Label className="text-sm font-medium">{t("settings.language")}</Label>
              <p className="text-xs text-muted-foreground mt-0.5">{t("settings.languageDesc")}</p>
            </div>
            <Segmented
              ariaLabel={t("common.language")}
              value={locale}
              onChange={setLocale}
              options={LOCALES.map((l) => ({ value: l.value, label: l.label }))}
            />
          </div>

        </CardContent>
      </Card>
      <Card className="card-hover">
        <CardHeader className="pb-3">
          <div className="flex items-center gap-2"><Github className="h-4 w-4 text-foreground" /><CardTitle className="text-base">{t("settings.projectUpdates")}</CardTitle></div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <Label className="text-sm font-medium">{t("settings.sourceCode")}</Label>
              <p className="text-xs text-muted-foreground mt-0.5">{t("settings.sourceCodeDesc")}</p>
            </div>
            <Button variant="outline" size="sm" className="gap-1.5 shrink-0" onClick={() => window.open("https://github.com/licorxj/QM-LocalRouter", "_blank", "noopener,noreferrer")}>
              <Github className="h-3.5 w-3.5" />{t("settings.visitGithub")}
            </Button>
          </div>
          <div className="flex flex-col gap-3 border-t pt-4">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div className="min-w-0">
                <Label className="text-sm font-medium">{t("settings.versionUpdates")}</Label>
                <div className="flex items-center gap-2 mt-1 flex-wrap">
                  <span className="text-xs text-muted-foreground">{t("settings.currentVersion")}</span>
                  <span className="text-sm font-semibold tabular-nums">v{updateInfo?.current_version ?? "…"}</span>
                  {updateInfo?.build_type === "release" && <Badge variant="success" className="text-[10px]">release</Badge>}
                  {updateInfo?.latest_version && (
                    <>
                      <span className="text-xs text-muted-foreground ml-2">{t("settings.latestVersion")}</span>
                      <span className="text-sm font-semibold tabular-nums">v{updateInfo.latest_version}</span>
                    </>
                  )}
                </div>
                <div className="mt-1">
                  {updateInfo?.check_failed ? (
                    <p className="text-xs text-destructive break-all">
                      {t("settings.updateCheckFailed")}{updateInfo.error ? `: ${updateInfo.error}` : ""}
                    </p>
                  ) : updateInfo?.update_available ? (
                    <p className="text-xs text-warning">
                      {updateInfo.mode === "git" && (updateInfo.git?.behind_count ?? 0) > 0
                        ? t("settings.updateAvailableDesc", { count: updateInfo.git.behind_count })
                        : t("settings.releaseAvailableDesc", { version: updateInfo.latest_version })}
                      {updateInfo.release_url && (
                        <a href={updateInfo.release_url} target="_blank" rel="noopener noreferrer"
                           className="ml-1.5 text-info hover:underline">{t("settings.releaseNotes")}</a>
                      )}
                    </p>
                  ) : updateInfo ? (
                    <p className="text-xs text-success">{t("settings.upToDate")}</p>
                  ) : (
                    <p className="text-xs text-muted-foreground">{t("settings.versionUpdatesDesc")}</p>
                  )}
                  {updateInfo?.mode === "git" && updateInfo.git && !updateInfo.git.working_tree_clean && (
                    <p className="text-xs text-warning mt-1">{t("settings.localChangesWarning")}</p>
                  )}
                </div>
              </div>
              <div className="flex gap-2 shrink-0">
                <Button variant="outline" size="sm" className="gap-1.5" onClick={() => runCheck(true)} disabled={checkingUpdate || applying}>
                  {checkingUpdate ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                  {t("settings.checkUpdates")}
                </Button>
                {updateInfo?.update_available && !applying && (
                  <Button size="sm" className="gap-1.5" onClick={runInstall}
                          disabled={checkingUpdate
                            || (updateInfo.mode === "git" && updateInfo.git && !updateInfo.git.working_tree_clean)}>
                    <Download className="h-3.5 w-3.5" />
                    {t("settings.installUpdate")}
                  </Button>
                )}
                {updateInfo && !updateInfo.update_available && !updateInfo.check_failed && (
                  <CheckCircle2 className="h-5 w-5 self-center text-success" aria-label={t("settings.upToDate")} />
                )}
              </div>
            </div>
            {applying && progress && progress.phase !== "idle" && (
              <div className="border-t pt-3 space-y-2">
                <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
                  <span>
                    {progress.phase === "downloading" ? t("settings.downloading")
                      : progress.phase === "applying" ? t("settings.applyingUpdate")
                      : ["installer_launched", "exiting"].includes(progress.phase) ? t("settings.installerLaunched")
                      : progress.message || t("settings.applyingUpdate")}
                  </span>
                  {progress.phase === "downloading" && <span className="tabular-nums">{progress.progress}%</span>}
                </div>
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted" role="progressbar" aria-valuenow={progress.phase === "downloading" ? progress.progress : undefined} aria-valuemin={0} aria-valuemax={100}>
                  <div className="h-full rounded-full bg-primary transition-all"
                       style={{ width: `${progress.phase === "downloading" ? Math.max(progress.progress, 4) : 100}%` }} />
                </div>
                {progress.phase === "downloading" && (
                  <p className="text-[11px] tabular-nums text-muted-foreground">
                    {(progress.downloaded / 1048576).toFixed(1)} / {((progress.total || 0) / 1048576).toFixed(1)} MB
                  </p>
                )}
              </div>
            )}
          </div>
        </CardContent>
      </Card>
      {/* Auto Start */}
      <Card className="card-hover">
        <CardHeader className="pb-3">
          <div className="flex items-center gap-2"><Power className="h-4 w-4 text-success" aria-hidden="true" /><CardTitle className="text-base">{t("settings.autoStart")}</CardTitle></div>
        </CardHeader>
        <CardContent>
          <div className="flex items-center justify-between gap-4">
            <div className="space-y-1">
              <Label className="text-sm">{t("settings.autoStartTitle")}</Label>
              <p className="text-xs text-muted-foreground">{t("settings.autoStartDesc")}</p>
              {autoStart && !autoStart.supported && (
                <p className="text-xs text-amber-500">{t("settings.autoStartUnsupported")}</p>
              )}
              {autoStart?.enabled && (
                <p className="text-[11px] font-mono text-muted-foreground break-all">{autoStart.script_path}</p>
              )}
            </div>
            <Switch
              checked={!!autoStart?.enabled}
              disabled={!autoStart?.supported || autoStartMut.isPending}
              onCheckedChange={(v) => autoStartMut.mutate(v)}
            />
          </div>
        </CardContent>
      </Card>
      {/* Backup and Restore */}
      <Card className="card-hover">
        <CardHeader className="pb-3">
          <div className="flex items-center gap-2"><Database className="h-4 w-4 text-info" aria-hidden="true" /><CardTitle className="text-base">{t("settings.backupRestore")}</CardTitle></div>
        </CardHeader>
        <CardContent className="space-y-5">

          {/* Backup Config */}
          <div>
            <Label className="text-sm font-medium">{t("settings.backupConfig")}</Label>
            <div className="mt-3 space-y-4">
              <div>
                <Label className="text-xs text-muted-foreground">{t("settings.backupFolder")}</Label>
                <div className="flex gap-2 mt-1.5">
                  <Input value={configForm.backup_dir} onChange={(e) => setConfigForm({ ...configForm, backup_dir: e.target.value })} placeholder="backup folder path" className="font-mono text-xs" />
                  <Button variant="outline" size="icon" className="shrink-0"><FolderOpen className="h-4 w-4" /></Button>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-4">
                <div className="flex items-center gap-3 col-span-1">
                  <Switch checked={configForm.auto_backup_enabled} onCheckedChange={(v) => setConfigForm({ ...configForm, auto_backup_enabled: v })} />
                  <Label className="text-sm">{t("settings.autoBackup")}</Label>
                </div>
                <div>
                  <Label className="text-xs text-muted-foreground">{t("settings.intervalHours")}</Label>
                  <Input type="number" min="1" value={configForm.auto_backup_interval_hours} onChange={(e) => setConfigForm({ ...configForm, auto_backup_interval_hours: Number(e.target.value) })} className="mt-1.5" />
                </div>
                <div>
                  <Label className="text-xs text-muted-foreground">{t("settings.maxBackups")}</Label>
                  <Input type="number" min="1" value={configForm.max_backups} onChange={(e) => setConfigForm({ ...configForm, max_backups: Number(e.target.value) })} className="mt-1.5" />
                </div>
              </div>
              <div className="flex justify-end">
                <Button size="sm" onClick={() => saveConfigMut.mutate()} disabled={saveConfigMut.isPending}>
                  {saveConfigMut.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" /> : <ShieldCheck className="h-3.5 w-3.5 mr-1" />}
                  {t("settings.saveConfig")}
                </Button>
              </div>
            </div>
          </div>

          <Separator />

          {/* Backup List */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <Label className="text-sm font-medium">{t("settings.backupList")}</Label>
                <Badge variant="secondary" className="text-xs">{backups.length}</Badge>
              </div>
              <div className="flex gap-2">
                <Button variant="ghost" size="sm" className="h-7 gap-1" onClick={() => refetchBackups()}>
                  <RefreshCw className="h-3 w-3" />{t("common.refresh")}
                </Button>
                <Button variant="outline" size="sm" className="h-7 gap-1" onClick={() => createBackupMut.mutate()} disabled={backing}>
                  {backing ? <Loader2 className="h-3 w-3 animate-spin" /> : <HardDrive className="h-3 w-3" />}
                  {t("settings.createBackup")}
                </Button>
              </div>
            </div>
            {backups.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-8 text-muted-foreground border border-dashed rounded-lg">
                <Database className="h-8 w-8 mb-2 opacity-20" />
                <p className="text-sm">{t("settings.noBackups")}</p>
                <p className="text-xs opacity-60 mt-1">{t("settings.noBackupsHint")}</p>
              </div>
            ) : (
              <div className="space-y-2 max-h-72 overflow-y-auto">
                {backups.map((b: any) => (
                  <div key={b.filename} className="flex items-center gap-3 rounded-lg border border-border p-3 hover:bg-accent/30 transition-colors group">
                    <Database className="h-4 w-4 text-muted-foreground shrink-0" />
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium font-mono truncate">{b.filename}</div>
                      <div className="flex items-center gap-3 text-xs text-muted-foreground mt-0.5">
                        <span className="flex items-center gap-1"><Clock className="h-3 w-3" />{formatDate(b.created_at)}</span>
                        <span>{b.size_mb} MB</span>
                      </div>
                    </div>
                    <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
                      <Button variant="ghost" size="sm" className="h-7 gap-1 text-xs" onClick={() => restoreMut.mutate(b.filename)} disabled={restoring === b.filename}>
                        {restoring === b.filename ? <Loader2 className="h-3 w-3 animate-spin" /> : <Upload className="h-3 w-3" />}
                        {t("settings.restore")}
                      </Button>
                      <Button variant="ghost" size="sm" className="h-7 gap-1 text-xs" asChild>
                        <a href={downloadBackupUrl(b.filename)} download><Download className="h-3 w-3" />{t("settings.download")}</a>
                      </Button>
                      <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive" onClick={() => deleteBackupMut.mutate(b.filename)} disabled={deletingBackup === b.filename}>
                        {deletingBackup === b.filename ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          <Separator />

          {/* Upload Restore */}
          <div>
            <Label className="text-sm font-medium">{t("settings.uploadRestore")}</Label>
            <p className="text-xs text-muted-foreground mt-0.5 mb-2">{t("settings.uploadRestoreDesc")}</p>
            <input type="file" accept=".db" onChange={handleFileRestore} className="hidden" id="restore-upload" />
            <Button variant="outline" size="sm" className="gap-1.5" onClick={() => document.getElementById("restore-upload")?.click()}>
              <Upload className="h-3.5 w-3.5" />{t("settings.selectDbFile")}
            </Button>
          </div>
        </CardContent>
      </Card>
</div>
  );
}

