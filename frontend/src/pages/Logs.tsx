import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getLogs, getStrategies, getProviders } from '../services/api';
import { useI18n } from '../i18n';
import { Card, CardContent } from '../components/ui/card';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Skeleton } from '../components/ui/skeleton';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '../components/ui/dialog';
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '../components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../components/ui/table';
import { PageHeader, EmptyState } from '../components/shared';
import { ChevronLeft, ChevronRight, ScrollText, Copy, Check } from 'lucide-react';

const PAGE_SIZE = 20;

export default function Logs() {
  const { t } = useI18n();
  const [page, setPage] = useState(1);
  const [strategyFilter, setStrategyFilter] = useState<string>('');
  const [providerFilter, setProviderFilter] = useState<string>('');
  const [selectedError, setSelectedError] = useState<any>(null);
  const [copied, setCopied] = useState(false);

  const { data: strategies = [] } = useQuery({ queryKey: ['strategies'], queryFn: () => getStrategies().then((r) => r.data) });
  const { data: providers = [] } = useQuery({ queryKey: ['providers'], queryFn: () => getProviders().then((r) => r.data) });

  const params: any = { page, page_size: PAGE_SIZE };
  if (strategyFilter) params.strategy_id = strategyFilter;
  if (providerFilter) params.provider_id = providerFilter;

  const { data, isLoading } = useQuery({ queryKey: ['logs', params], queryFn: () => getLogs(params).then((r) => r.data) });
  const logs = data?.items || [];
  const total = data?.total || 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const getStrategyName = (id: number) => strategies.find((s: any) => s.id === id)?.name || '#' + id;
  const getProviderName = (id: number) => providers.find((p: any) => p.id === id)?.name || '#' + id;
  const formatTime = (dt: string) => dt ? new Date(dt + 'Z').toLocaleString() : '-';

  const copyError = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className='mx-auto max-w-7xl space-y-6'>
      <PageHeader title={t('logs.title')} description={t('logs.subtitle')} />

      <div className='flex flex-wrap items-center gap-3'>
        <Select value={strategyFilter} onValueChange={(v) => { setStrategyFilter(v === 'all' ? '' : v); setPage(1); }}>
          <SelectTrigger className='w-48'><SelectValue placeholder={t('logs.allStrategies')} /></SelectTrigger>
          <SelectContent>
            <SelectItem value='all'>{t('logs.allStrategies')}</SelectItem>
            {strategies.map((s: any) => <SelectItem key={s.id} value={String(s.id)}>{s.name}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={providerFilter} onValueChange={(v) => { setProviderFilter(v === 'all' ? '' : v); setPage(1); }}>
          <SelectTrigger className='w-48'><SelectValue placeholder={t('logs.allProviders')} /></SelectTrigger>
          <SelectContent>
            <SelectItem value='all'>{t('logs.allProviders')}</SelectItem>
            {providers.map((p: any) => <SelectItem key={p.id} value={String(p.id)}>{p.name}</SelectItem>)}
          </SelectContent>
        </Select>
        <span className='ml-auto text-xs text-muted-foreground'>
          {t('logs.totalRecords')}: <span className='font-medium tabular-nums text-foreground'>{total}</span>
        </span>
      </div>

      {logs.length === 0 && !isLoading ? (
        <EmptyState
          icon={<ScrollText />}
          title={t('logs.noLogs')}
          description={t('logs.noLogsHint')}
        />
      ) : (
        <Card className='animate-in'>
          <CardContent className='p-0'>
            <Table>
              <TableHeader>
                <TableRow className='hover:bg-transparent'>
                  <TableHead className='w-14'>ID</TableHead>
                  <TableHead>{t('logs.time')}</TableHead>
                  <TableHead>{t('logs.strategy')}</TableHead>
                  <TableHead>{t('logs.platform')}</TableHead>
                  <TableHead>{t('logs.model')}</TableHead>
                  <TableHead>{t('logs.key')}</TableHead>
                  <TableHead>{t('logs.statusCode')}</TableHead>
                  <TableHead>{t('logs.latency')}</TableHead>
                  <TableHead>{t('logs.stream')}</TableHead>
                  <TableHead>{t('logs.error')}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {logs.map((l: any) => (
                  <TableRow key={l.id}>
                    <TableCell className='text-xs tabular-nums text-muted-foreground'>{l.id}</TableCell>
                    <TableCell className='whitespace-nowrap text-xs tabular-nums text-muted-foreground'>{formatTime(l.created_at)}</TableCell>
                    <TableCell className='text-sm'>{l.strategy_id ? getStrategyName(l.strategy_id) : '-'}</TableCell>
                    <TableCell className='text-sm'>{l.provider_id ? getProviderName(l.provider_id) : '-'}</TableCell>
                    <TableCell className='font-mono text-xs text-muted-foreground'>{l.model_used || '-'}</TableCell>
                    <TableCell className='text-sm'>{l.key_alias || '-'}</TableCell>
                    <TableCell>
                      {l.status_code && l.status_code >= 200 && l.status_code < 300 ? (
                        <Badge variant='success'>{l.status_code}</Badge>
                      ) : l.status_code ? (
                        <Badge variant='destructive'>{l.status_code}</Badge>
                      ) : '-'}
                    </TableCell>
                    <TableCell className='text-sm tabular-nums'>{l.latency_ms ? `${l.latency_ms}ms` : '-'}</TableCell>
                    <TableCell>{l.is_stream ? <Badge variant='outline'>SSE</Badge> : '-'}</TableCell>
                    <TableCell>
                      {l.error_message ? (
                        <button
                          type='button'
                          className='block max-w-[220px] cursor-pointer truncate text-left text-xs text-destructive hover:underline'
                          title={l.error_message}
                          onClick={() => { setSelectedError(l); setCopied(false); }}
                        >
                          {l.error_message}
                        </button>
                      ) : '-'}
                    </TableCell>
                  </TableRow>
                ))}
                {isLoading && (
                  Array.from({ length: 5 }).map((_, i) => (
                    <TableRow key={`skeleton-${i}`}>
                      <TableCell colSpan={10} className='py-2.5'>
                        <Skeleton className='h-5 w-full' />
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {totalPages > 1 && (
        <div className='flex items-center justify-between'>
          <span className='text-sm text-muted-foreground' aria-hidden='true' />
          <div className='flex items-center gap-2'>
            <Button variant='outline' size='icon' className='h-8 w-8' onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1} aria-label={t('common.previous')}>
              <ChevronLeft className='h-4 w-4' aria-hidden='true' />
            </Button>
            <span className='min-w-16 text-center text-sm tabular-nums text-muted-foreground'>
              {page} / {totalPages}
            </span>
            <Button variant='outline' size='icon' className='h-8 w-8' onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page === totalPages} aria-label={t('common.next')}>
              <ChevronRight className='h-4 w-4' aria-hidden='true' />
            </Button>
          </div>
        </div>
      )}

      <Dialog open={selectedError !== null} onOpenChange={(v) => { if (!v) setSelectedError(null); }}>
        <DialogContent className='max-w-2xl'>
          <DialogHeader>
            <DialogTitle>{t('logs.error')} - #{selectedError?.id}</DialogTitle>
          </DialogHeader>
          <div className='space-y-3'>
            <div className='flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground'>
              <span>{t('logs.time')}: <span className='tabular-nums text-foreground'>{formatTime(selectedError?.created_at)}</span></span>
              <span>{t('logs.statusCode')}: <Badge variant={selectedError?.status_code >= 200 && selectedError?.status_code < 300 ? 'success' : 'destructive'}>{selectedError?.status_code}</Badge></span>
              <span>{t('logs.latency')}: <span className='tabular-nums text-foreground'>{selectedError?.latency_ms}ms</span></span>
              <span>{t('logs.platform')}: <span className='text-foreground'>{selectedError?.provider_id ? getProviderName(selectedError.provider_id) : '-'}</span></span>
            </div>
            <div className='flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground'>
              <span>{t('logs.model')}: <span className='font-mono text-foreground'>{selectedError?.model_used || '-'}</span></span>
              <span>{t('logs.key')}: <span className='text-foreground'>{selectedError?.key_alias || '-'}</span></span>
              <span>{t('logs.stream')}: <span className='text-foreground'>{selectedError?.is_stream ? 'SSE' : '-'}</span></span>
              {selectedError?.strategy_id && <span>{t('logs.strategy')}: <span className='text-foreground'>{getStrategyName(selectedError.strategy_id)}</span></span>}
            </div>
            <pre className='max-h-[400px] overflow-auto whitespace-pre-wrap break-all rounded-lg bg-muted/60 p-4 font-mono text-xs leading-relaxed text-foreground'>
              {selectedError?.error_message || '-'}
            </pre>
          </div>
          <DialogFooter>
            <Button variant='outline' size='sm' onClick={() => copyError(selectedError?.error_message || '')} className='gap-1.5'>
              {copied ? <Check className='h-3.5 w-3.5 text-success' aria-hidden='true' /> : <Copy className='h-3.5 w-3.5' aria-hidden='true' />}
              {copied ? t('common.copied') : t('common.copy')}
            </Button>
            <Button variant='outline' size='sm' onClick={() => setSelectedError(null)}>{t('common.close')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
