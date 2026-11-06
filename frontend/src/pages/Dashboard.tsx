import React, { useState } from 'react';
import { toast } from "../stores/toast";
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getDashboardStats, getStrategies, getProviders, clearTodayStats, getSettings, getCloudMeta } from '../services/api';
import { useI18n } from '../i18n';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../components/ui/card';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { PageHeader, StatCard, ConfirmDialog, CodeBlock } from '../components/shared';
import {
  Activity, CheckCircle, XCircle, Clock, GitBranch, Server, KeyRound,
  ArrowUpRight, Zap, TrendingUp, Coins, Trash2, Megaphone, HeartHandshake, ExternalLink,
} from 'lucide-react';

export default function Dashboard() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [clearing, setClearing] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ['dashboard'],
    queryFn: () => getDashboardStats().then((r) => r.data),
    refetchInterval: 5000,
  });

  const clearMutation = useMutation({
    mutationFn: () => clearTodayStats(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dashboard'] });
      toast({ title: t('dashboard.clearToday') + ' OK', variant: 'success' });
    },
    onError: () => {
      toast({ title: t('common.error'), variant: 'destructive' });
    },
  });

  const { data: strategies = [] } = useQuery({
    queryKey: ['strategies'],
    queryFn: () => getStrategies().then((r) => r.data),
  });

  // 接入示例用后端真实监听端口，不写死上游默认值
  const { data: appSettings } = useQuery({
    queryKey: ['appSettings'],
    queryFn: () => getSettings().then((r) => r.data),
  });
  const backendPort = appSettings?.backend_port || 12002;

  const { data: providers = [] } = useQuery({
    queryKey: ['providers'],
    queryFn: () => getProviders().then((r) => r.data),
  });

  // 云端元数据：软件公告 + 赞助商席位（后端代理晴沐云端，5 分钟缓存）
  const { data: cloudMeta } = useQuery({
    queryKey: ['cloudMeta'],
    queryFn: () => getCloudMeta().then((r) => r.data),
    staleTime: 5 * 60 * 1000,
    refetchInterval: 10 * 60 * 1000,
  });
  const announcements: any[] = cloudMeta?.announcements || [];
  const sponsors: any[] = cloudMeta?.sponsors || [];
  // 默认 3 个席位，云端席位更多时全部展示
  const sponsorSlots = Math.max(3, sponsors.length);

  const stats = data || {
    total_requests: 0, success_count: 0, error_count: 0,
    success_rate: 0, avg_latency_ms: 0,
    total_prompt_tokens: 0, total_completion_tokens: 0, total_tokens: 0,
    active_strategies: 0, active_providers: 0, active_keys: 0,
  };

  const primaryStats = [
    { label: t('dashboard.todayRequests'), value: stats.total_requests, icon: Activity, tone: 'info' as const },
    { label: t('dashboard.successRate'), value: `${stats.success_rate}%`, icon: TrendingUp, tone: 'success' as const },
    { label: t('dashboard.avgLatency'), value: `${Math.round(stats.avg_latency_ms)}${t('dashboard.ms')}`, icon: Clock, tone: 'primary' as const },
    { label: t('dashboard.failedRequests'), value: stats.error_count, icon: XCircle, tone: 'destructive' as const },
  ];

  const secondaryStats = [
    { label: t('dashboard.successRequests'), value: stats.success_count, icon: CheckCircle },
    { label: t('dashboard.promptTokens'), value: stats.total_prompt_tokens.toLocaleString(), icon: Coins },
    { label: t('dashboard.completionTokens'), value: stats.total_completion_tokens.toLocaleString(), icon: Coins },
    { label: t('dashboard.totalTokens'), value: stats.total_tokens.toLocaleString(), icon: Coins },
    { label: t('dashboard.activeStrategies'), value: stats.active_strategies, icon: GitBranch },
    { label: t('dashboard.activeProviders'), value: stats.active_providers, icon: Server },
    { label: t('dashboard.activeKeys'), value: stats.active_keys, icon: KeyRound },
  ];

  const quickSteps = [
    { step: '1', text: t('dashboard.step1Title'), desc: t('dashboard.step1Desc') },
    { step: '2', text: t('dashboard.step2Title'), desc: t('dashboard.step2Desc') },
    { step: '3', text: t('dashboard.step3Title'), desc: t('dashboard.step3Desc') },
    { step: '4', text: t('dashboard.step4Title'), desc: t('dashboard.step4Desc') },
  ];

  const endpointCode = `POST http://127.0.0.1:${backendPort}/v1/chat/completions`;

  return (
    <div className='mx-auto max-w-7xl space-y-6'>
      <PageHeader
        title={t('dashboard.title')}
        description={t('dashboard.subtitle')}
        actions={
          <Button
            variant='outline'
            size='sm'
            className='text-destructive hover:bg-destructive/10 hover:text-destructive'
            onClick={() => setConfirmClear(true)}
            disabled={clearing}
          >
            <Trash2 className='h-3.5 w-3.5' aria-hidden='true' />
            {t('dashboard.clearToday')}
          </Button>
        }
      />

      {/* Primary metrics */}
      <div className='grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4'>
        {primaryStats.map((c) => (
          <StatCard
            key={c.label}
            label={c.label}
            value={c.value}
            icon={<c.icon />}
            tone={c.tone}
            loading={isLoading}
          />
        ))}
      </div>

      {/* Secondary metrics strip */}
      <Card className='animate-in stagger-2'>
        <CardContent className='grid grid-cols-2 gap-x-6 gap-y-4 p-5 sm:grid-cols-4 xl:grid-cols-7'>
          {secondaryStats.map((c) => (
            <div key={c.label} className='flex items-center gap-2.5'>
              <c.icon className='h-4 w-4 flex-none text-muted-foreground' aria-hidden='true' />
              <div className='min-w-0'>
                <p className='truncate text-[11px] text-muted-foreground'>{c.label}</p>
                <p className='text-sm font-semibold tabular-nums'>
                  {isLoading ? '—' : c.value}
                </p>
              </div>
            </div>
          ))}
        </CardContent>
      </Card>

      {/* Quick Actions + Active Strategies */}
      <div className='grid grid-cols-1 gap-6 lg:grid-cols-2'>
        {/* Quick Start Guide */}
        <Card className='animate-in stagger-3'>
          <CardHeader className='pb-4'>
            <CardTitle className='flex items-center gap-2 text-base'>
              <Zap className='h-4 w-4 text-primary' aria-hidden='true' />
              {t('dashboard.quickStart')}
            </CardTitle>
            <CardDescription>{t('dashboard.subtitle')}</CardDescription>
          </CardHeader>
          <CardContent className='space-y-4'>
            <ol className='space-y-3'>
              {quickSteps.map((item) => (
                <li key={item.step} className='flex items-start gap-3'>
                  <div className='flex h-6 w-6 flex-none items-center justify-center rounded-md border border-primary/25 bg-primary/10 text-xs font-bold text-primary'>
                    {item.step}
                  </div>
                  <div>
                    <p className='text-sm font-medium'>{item.text}</p>
                    <p className='text-xs text-muted-foreground'>{item.desc}</p>
                  </div>
                </li>
              ))}
            </ol>
            <div className='border-t border-border pt-4'>
              <p className='mb-2 text-xs text-muted-foreground'>{t('dashboard.requestEndpoint')}</p>
              <CodeBlock code={endpointCode} />
            </div>
          </CardContent>
        </Card>

        {/* Active Strategies */}
        <Card className='animate-in stagger-4'>
          <CardHeader className='pb-4'>
            <CardTitle className='flex items-center gap-2 text-base'>
              <GitBranch className='h-4 w-4 text-info' aria-hidden='true' />
              {t('dashboard.activeStrategiesTitle')}
            </CardTitle>
            <CardDescription>
              {providers.length > 0
                ? `${providers.length} ${t('dashboard.rulesCount')}`
                : undefined}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {strategies.length === 0 ? (
              <p className='py-6 text-center text-sm text-muted-foreground'>{t('dashboard.noStrategies')}</p>
            ) : (
              <ul className='space-y-2'>
                {strategies.slice(0, 5).map((s: any) => (
                  <li key={s.id}>
                    <div className='flex items-center justify-between rounded-lg border border-transparent bg-muted/40 px-3 py-2.5 transition-colors hover:border-border hover:bg-muted/60'>
                      <div className='flex min-w-0 items-center gap-3'>
                        <span className='h-2 w-2 flex-none rounded-full bg-success' aria-hidden='true' />
                        <div className='min-w-0'>
                          <p className='truncate text-sm font-medium'>{s.name}</p>
                          <p className='text-xs text-muted-foreground'>
                            {s.rules?.length || 0} {t('dashboard.rulesCount')} · {s.lb_strategy}
                          </p>
                        </div>
                      </div>
                      <ArrowUpRight className='h-4 w-4 flex-none text-muted-foreground' aria-hidden='true' />
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>

      {/* 软件公告 + 赞助商席位（云端数据） */}
      <div className='grid grid-cols-1 gap-6 lg:grid-cols-2'>
        {/* Software Announcements */}
        <Card className='animate-in stagger-5'>
          <CardHeader className='pb-4'>
            <CardTitle className='flex items-center gap-2 text-base'>
              <Megaphone className='h-4 w-4 text-warning' aria-hidden='true' />
              {t('dashboard.announcements')}
            </CardTitle>
            {announcements.length > 0 && <Badge variant='secondary' className='w-fit'>{announcements.length}</Badge>}
          </CardHeader>
          <CardContent>
            {announcements.length === 0 ? (
              <p className='py-6 text-center text-sm text-muted-foreground'>{t('dashboard.noAnnouncements')}</p>
            ) : (
              <ul className='space-y-2'>
                {announcements.map((a: any) => (
                  <li key={a.id}>
                    <div className='rounded-lg bg-muted/40 px-3 py-2.5 transition-colors hover:bg-muted/60'>
                      <div className='flex items-center justify-between gap-2'>
                        <p className='text-sm font-medium'>{a.title || t('dashboard.announcements')}</p>
                        {a.created_at && <span className='flex-none text-xs text-muted-foreground'>{a.created_at}</span>}
                      </div>
                      {a.content && <p className='mt-1 whitespace-pre-wrap break-words text-xs text-muted-foreground'>{a.content}</p>}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        {/* Sponsor Seats */}
        <Card className='animate-in stagger-6'>
          <CardHeader className='pb-4'>
            <CardTitle className='flex items-center gap-2 text-base'>
              <HeartHandshake className='h-4 w-4 text-primary' aria-hidden='true' />
              {t('dashboard.sponsorSlots')}
            </CardTitle>
            {sponsors.length > 0 && <Badge variant='secondary' className='w-fit'>{sponsors.length}</Badge>}
          </CardHeader>
          <CardContent>
            <div className='grid grid-cols-1 gap-3 sm:grid-cols-3'>
              {Array.from({ length: sponsorSlots }).map((_, i) => {
                const s = sponsors[i];
                if (!s) {
                  return (
                    <div
                      key={`empty-${i}`}
                      className='flex flex-col items-center justify-center gap-1.5 rounded-xl border border-dashed px-3 py-6 text-center'
                    >
                      <HeartHandshake className='h-5 w-5 text-muted-foreground/40' aria-hidden='true' />
                      <p className='text-xs font-medium text-muted-foreground/70'>{t('dashboard.sponsorEmpty')}</p>
                    </div>
                  );
                }
                return (
                  <div key={s.id} className='flex flex-col rounded-xl border bg-muted/30 p-3 transition-colors hover:bg-muted/50'>
                    <p className='truncate text-sm font-semibold'>{s.name}</p>
                    <p className='mt-1.5 line-clamp-3 flex-1 text-xs text-muted-foreground'>{s.brief}</p>
                    {s.website && (
                      <a
                        href={s.website}
                        target='_blank'
                        rel='noreferrer'
                        className='mt-2 inline-flex items-center gap-1 text-xs text-info hover:underline'
                      >
                        {t('dashboard.sponsorVisit')}
                        <ExternalLink className='h-3 w-3' aria-hidden='true' />
                      </a>
                    )}
                  </div>
                );
              })}
            </div>
          </CardContent>
        </Card>
      </div>

      <ConfirmDialog
        open={confirmClear}
        onOpenChange={setConfirmClear}
        title={t('dashboard.clearToday')}
        description={t('dashboard.clearTodayDesc')}
        confirmLabel={t('common.confirm')}
        cancelLabel={t('common.cancel')}
        loading={clearing || clearMutation.isPending}
        onConfirm={() => {
          clearMutation.mutate(undefined, {
            onSettled: () => setConfirmClear(false),
          });
        }}
      />
    </div>
  );
}
