import React, { useEffect, useRef, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  getStrategies, createStrategy, updateStrategy, deleteStrategy,
  getProviders, getModels, testStrategy, updateRule, addRule, deleteRule, getAccounts, testStrategyRules,
  monitorStreamUrl, testStrategyRule,
} from '../services/api';
import { useI18n } from '../i18n';
import { toast } from '../stores/toast';
import { Button } from '../components/ui/button';
import { Card, CardContent } from '../components/ui/card';
import { PageHeader, EmptyState, CopyButton } from '../components/shared';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Badge } from '../components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '../components/ui/dialog';
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '../components/ui/select';
import { Switch } from '../components/ui/switch';
import { Textarea } from '../components/ui/textarea';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '../components/ui/tabs';
import { Plus, Trash2, Pencil, Zap, GitBranch, X, Loader2, ArrowRight, Check, Search, ListChecks, LayoutGrid, Workflow, Eye, EyeOff, GripVertical, Server, UserRound, Gift, ChevronDown, ChevronRight } from 'lucide-react';

interface Rule { provider_id: number; model_id: number; model_name?: string; priority: number; weight: number; is_active: boolean; }

export default function Strategies() {
  const { t } = useI18n();
  const qc = useQueryClient();

  const lbOptions = [
    { value: 'round_robin', label: t('strategies.optionRoundRobin'), desc: t('strategies.optionDescOrder') },
    { value: 'weighted', label: t('strategies.optionWeighted'), desc: t('strategies.optionDescWeight') },
    { value: 'random', label: t('strategies.optionRandom'), desc: t('strategies.optionDescRandom') },
    { value: 'failover', label: t('strategies.optionFailover'), desc: t('strategies.optionDescFailover') },
    { value: 'priority', label: t('strategies.optionPriority'), desc: t('strategies.optionDescHighest') },
    { value: 'token_threshold', label: t('strategies.optionTokenThreshold'), desc: t('strategies.optionTokenThresholdDesc') },
  ];

  const tokenPeriodOptions = [
    { value: 'per_minute', label: t('strategies.tokenPeriodMinute') },
    { value: 'per_5min', label: t('strategies.tokenPeriod5Min') },
    { value: 'per_day', label: t('strategies.tokenPeriodDay') },
    { value: 'per_month', label: t('strategies.tokenPeriodMonth') },
  ];

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<any>(null);
  const [form, setForm] = useState({ name: '', description: '', lb_strategy: 'round_robin', key_strategy: 'round_robin', key_switch_mode: 'none', key_rpm_threshold: 0, key_count_threshold: 0, rule_token_threshold: 0, rule_token_period: 'per_day', is_active: true, timeout: 120, retry_count: 2, rules: [] as Rule[] });
  const [newRule, setNewRule] = useState({ provider_id: 0, model_id: 0, priority: 0, weight: 1, is_active: true });
  const [testingId, setTestingId] = useState<number | null>(null);
  const [testResults, setTestResults] = useState<Record<number, any>>({});

  // 卡片规则行快捷切换模型：懒加载各平台的模型列表（providerId -> models），切换后直接更新 rule
  const [ruleModelsCache, setRuleModelsCache] = useState<Record<number, any[]>>({});
  const updateRuleMut = useMutation({
    mutationFn: ({ ruleId, modelId }: { ruleId: number; modelId: number }) => updateRule(ruleId, { model_id: modelId }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['strategies'] }); toast({ title: t('strategies.updated'), variant: 'success' }); },
    onError: (e: any) => toast({ title: t('strategies.updateFailed'), description: e?.response?.data?.detail, variant: 'destructive' }),
  });

  const loadRuleModels = (providerId: number) => {
    if (ruleModelsCache[providerId]) return;
    getModels(providerId).then((r) => setRuleModelsCache(prev => ({ ...prev, [providerId]: r.data || [] }))).catch(() => {});
  };

  // 视图模式：cards=编辑视图（默认）| visual=可视化视图；privacyMode 用于可视化中隐藏账号/平台标识
  const [viewMode, setViewMode] = useState<'cards' | 'visual'>('cards');
  const [privacyMode, setPrivacyMode] = useState(false);
  const [activeStrategyId, setActiveStrategyId] = useState<number | null>(null);

  // Create by model dialog state
  const [byModelOpen, setByModelOpen] = useState(false);
  const [byModelProviders, setByModelProviders] = useState<any[]>([]);
  const [byModelSelProvider, setByModelSelProvider] = useState<number>(0);
  const [byModelModels, setByModelModels] = useState<any[]>([]);
  const [byModelChecked, setByModelChecked] = useState<Set<number>>(new Set());
  const [byModelSearch, setByModelSearch] = useState('');
  const [byModelLoading, setByModelLoading] = useState(false);
  // 搜索时按需加载全部平台模型（跨所有平台匹配），未搜索时仍按左侧选中平台展示
  const [byModelAllModels, setByModelAllModels] = useState<any[]>([]);
  const [byModelAllLoaded, setByModelAllLoaded] = useState(false);

  const { data: strategies = [] } = useQuery({ queryKey: ['strategies'], queryFn: () => getStrategies().then((r) => r.data) });
  const { data: providers = [] } = useQuery({ queryKey: ['providers'], queryFn: () => getProviders().then((r) => r.data) });
  const { data: models = [] } = useQuery({ queryKey: ['models', newRule.provider_id], queryFn: () => newRule.provider_id ? getModels(newRule.provider_id).then((r) => r.data) : [], enabled: !!newRule.provider_id });

  // 当前激活策略（可视化视图用）；必须在 strategies 声明之后计算（避免 TDZ）
  const activeStrategy = strategies.find((s: any) => s.id === activeStrategyId) || strategies[0] || null;

  const createMut = useMutation({
    mutationFn: (d: any) => createStrategy(d),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['strategies'] }); setOpen(false); toast({ title: t('strategies.created'), variant: 'success' }); },
    onError: (e: any) => toast({ title: t('strategies.createFailed'), description: e?.response?.data?.detail, variant: 'destructive' }),
  });

  const updateMut = useMutation({
    mutationFn: ({ id, ...d }: any) => updateStrategy(id, d),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['strategies'] }); setOpen(false); toast({ title: t('strategies.updated'), variant: 'success' }); },
    onError: (e: any) => toast({ title: t('strategies.updateFailed'), description: e?.response?.data?.detail, variant: 'destructive' }),
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => deleteStrategy(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['strategies'] }); toast({ title: t('strategies.deleted'), variant: 'success' }); },
  });

  const testMut = useMutation({
    mutationFn: (id: number) => { setTestingId(id); return testStrategy(id); },
    onSuccess: (res, id) => { setTestingId(null); setTestResults((prev) => ({ ...prev, [id]: res.data })); toast({ title: res.data.success ? t('strategies.testSuccess') : t('strategies.testFailed'), description: res.data.message, variant: res.data.success ? 'success' : 'destructive' }); },
    onError: () => { setTestingId(null); toast({ title: t('strategies.testFailed'), variant: 'destructive' }); },
  });

  const toggleActive = useMutation({
    mutationFn: ({ id, is_active }: { id: number; is_active: boolean }) => updateStrategy(id, { is_active }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['strategies'] }); },
    onError: () => { toast({ title: t('common.error'), variant: 'destructive' }); },
  });

  const openByModel = async () => {
    setByModelChecked(new Set());
    setByModelSearch('');
    setByModelSelProvider(0);
    setByModelModels([]);
    setByModelAllModels([]);
    setByModelAllLoaded(false);
    setByModelOpen(true);
    setByModelLoading(true);
    try {
      const res = await getProviders();
      setByModelProviders(res.data || []);
    } catch (_e) { toast({ title: t('common.error'), variant: 'destructive' }); }
    setByModelLoading(false);
  };

  const loadByModelModels = async (providerId: number) => {
    setByModelSelProvider(providerId);
    setByModelLoading(true);
    try {
      const res = await getModels(providerId);
      setByModelModels(res.data || []);
    } catch (_e) { toast({ title: t('common.error'), variant: 'destructive' }); }
    setByModelLoading(false);
  };

  // 有搜索词时按需并发拉取全部平台的模型；结果附带 provider_id/name 便于展示与提交
  const loadAllByModelModels = async () => {
    if (byModelAllLoaded || byModelLoading) return;
    setByModelLoading(true);
    try {
      const list = byModelProviders.length ? byModelProviders : ((await getProviders()).data || []);
      const settled = await Promise.all(list.map(async (p: any) => {
        try {
          const res = await getModels(p.id);
          return (res.data || []).map((m: any) => ({ ...m, provider_id: m.provider_id ?? p.id, provider_name: m.provider_name || p.name }));
        } catch (_e) { return []; }
      }));
      setByModelAllModels(settled.flat());
      setByModelAllLoaded(true);
    } catch (_e) { toast({ title: t('common.error'), variant: 'destructive' }); }
    setByModelLoading(false);
  };

  const toggleByModelCheck = (modelId: number) => {
    setByModelChecked(prev => {
      const next = new Set(prev);
      if (next.has(modelId)) next.delete(modelId); else next.add(modelId);
      return next;
    });
  };

  const toggleByModelAll = () => {
    const filteredIds = new Set(byModelFiltered.map((m: any) => m.id));
    const allFilteredSelected = byModelFiltered.length > 0 && byModelFiltered.every((m: any) => byModelChecked.has(m.id));
    if (allFilteredSelected) {
      setByModelChecked(prev => { const next = new Set(prev); filteredIds.forEach(id => next.delete(id)); return next; });
    } else {
      setByModelChecked(prev => { const next = new Set(prev); filteredIds.forEach(id => next.add(id)); return next; });
    }
  };

  // 有搜索词时跨全部平台模型匹配；否则仅显示左侧选中平台的模型
  const byModelSearchActive = byModelSearch.trim().length > 0;
  const byModelSource = byModelSearchActive ? byModelAllModels : byModelModels;
  const byModelFiltered = byModelSource.filter((m: any) => !byModelSearchActive || m.model_id.toLowerCase().includes(byModelSearch.toLowerCase()) || (m.display_name || '').toLowerCase().includes(byModelSearch.toLowerCase()) || (m.provider_name || '').toLowerCase().includes(byModelSearch.toLowerCase()));
  // 已选模型可能来自任意平台，需从合并池反查（all 池仅搜索后才能有数据）
  const byModelLookup = new Map<number, any>();
  [...byModelAllModels, ...byModelModels].forEach((m: any) => { if (!byModelLookup.has(m.id)) byModelLookup.set(m.id, m); });

  const openCreate = () => {
    setEditing(null);
    setForm({ name: '', description: '', lb_strategy: 'round_robin', key_strategy: 'round_robin', key_switch_mode: 'none', key_rpm_threshold: 0, key_count_threshold: 0, rule_token_threshold: 0, rule_token_period: 'per_day', is_active: true, timeout: 120, retry_count: 2, rules: [] });
    setOpen(true);
  };

  const openEdit = (s: any) => {
    setEditing(s);
    setForm({
      name: s.name, description: s.description || '', lb_strategy: s.lb_strategy, key_strategy: s.key_strategy || 'round_robin', key_switch_mode: s.key_switch_mode || 'none', key_rpm_threshold: s.key_rpm_threshold || 0, key_count_threshold: s.key_count_threshold || 0, rule_token_threshold: s.rule_token_threshold || 0, rule_token_period: s.rule_token_period || 'per_day',
      is_active: s.is_active, timeout: s.timeout, retry_count: s.retry_count,
      rules: (s.rules || []).map((r: any) => ({ provider_id: r.provider_id, model_id: r.model_id, model_name: r.model_name || '', priority: r.priority, weight: r.weight, is_active: r.is_active })),
    });
    setOpen(true);
  };

  const addRule = () => {
    if (newRule.provider_id && newRule.model_id) {
      const modelName = (models as any[]).find((m: any) => m.id === newRule.model_id)?.model_id || '';
      setForm({ ...form, rules: [...form.rules, { ...newRule, model_name: modelName }] });
      setNewRule({ provider_id: 0, model_id: 0, priority: 0, weight: 1, is_active: true });
    }
  };

  const removeRule = (idx: number) => setForm({ ...form, rules: form.rules.filter((_, i) => i !== idx) });

  const save = () => {
    const payload = {
      name: form.name, description: form.description, lb_strategy: form.lb_strategy,
      key_strategy: form.key_strategy, key_switch_mode: form.key_switch_mode,
      key_rpm_threshold: form.key_rpm_threshold, key_count_threshold: form.key_count_threshold,
      rule_token_threshold: form.rule_token_threshold, rule_token_period: form.rule_token_period,
      is_active: form.is_active, timeout: form.timeout, retry_count: form.retry_count,
      rules: form.rules,
    };
    if (editing) { updateMut.mutate({ id: editing.id, ...payload }); }
    else { createMut.mutate(payload); }
  };

  const getProviderName = (id: number) => providers.find((p: any) => p.id === id)?.name || `${id}`;

  return (
    <div className='space-y-6'>
      <PageHeader
        title={t('strategies.title')}
        description={
          <span className='inline-flex flex-wrap items-center gap-x-2 gap-y-0.5'>
            <span>{t('strategies.routingRules')}</span>
            <span className='text-muted-foreground/60' aria-hidden='true'>·</span>
            <span>{t('strategies.strategyNameAsModelHint')}</span>
          </span>
        }
        actions={
          <>
            <div className='flex items-center rounded-md border border-border p-0.5' role='group' aria-label={t('strategies.viewVisual')}>
              <Button
                variant='ghost'
                size='sm'
                className={'h-7 gap-1.5 px-2.5 text-xs ' + (viewMode === 'cards' ? 'bg-primary/10 text-primary hover:bg-primary/15' : 'text-muted-foreground')}
                onClick={() => setViewMode('cards')}
              >
                <LayoutGrid className='h-3.5 w-3.5' aria-hidden='true' />{t('strategies.viewEdit')}
              </Button>
              <Button
                variant='ghost'
                size='sm'
                className={'h-7 gap-1.5 px-2.5 text-xs ' + (viewMode === 'visual' ? 'bg-primary/10 text-primary hover:bg-primary/15' : 'text-muted-foreground')}
                onClick={() => setViewMode('visual')}
              >
                <Workflow className='h-3.5 w-3.5' aria-hidden='true' />{t('strategies.viewVisual')}
              </Button>
            </div>
            {viewMode === 'visual' && (
              <Button
                variant='ghost'
                size='icon'
                className='h-8 w-8'
                title={privacyMode ? t('strategies.privacyShow') : t('strategies.privacyHide')}
                aria-label={privacyMode ? t('strategies.privacyShow') : t('strategies.privacyHide')}
                onClick={() => setPrivacyMode(v => !v)}
              >
                {privacyMode ? <EyeOff className='h-4 w-4' aria-hidden='true' /> : <Eye className='h-4 w-4' aria-hidden='true' />}
              </Button>
            )}
            <Button variant='outline' onClick={openByModel} className='gap-2'><ListChecks className='h-4 w-4' aria-hidden='true' />{t('strategies.createByModel')}</Button>
            <Button onClick={openCreate} className='gap-2'><Plus className='h-4 w-4' aria-hidden='true' />{t('strategies.createStrategy')}</Button>
          </>
        }
      />

      {viewMode === 'visual' ? (
        <StrategyVisualMode
          strategies={strategies as any[]}
          activeStrategy={activeStrategy}
          onSelectStrategy={(id: number) => setActiveStrategyId(id)}
          onEditStrategy={openEdit}
          providers={providers as any[]}
          accountsQueryEnabled={viewMode === 'visual'}
          privacyMode={privacyMode}
          ruleModelsCache={ruleModelsCache}
          loadRuleModels={loadRuleModels}
          updateRuleMut={updateRuleMut}
          getProviderName={getProviderName}
        />
      ) : strategies.length === 0 ? (
        <EmptyState
          icon={<GitBranch />}
          title={t('strategies.noRules')}
          action={<Button onClick={openCreate} variant='outline' className='gap-2'><Plus className='h-4 w-4' aria-hidden='true' />{t('strategies.createStrategy')}</Button>}
        />
      ) : (
        <div className='grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4'>
          {strategies.map((s: any) => (
            <Card key={s.id} className='card-hover h-full flex flex-col'>
              <CardContent className='p-5 flex-1 flex flex-col'>
                <div className='mb-4'>
                  <div className='flex items-center gap-3'>
                    <div className='flex h-10 w-10 flex-none items-center justify-center rounded-xl bg-primary/12'>
                      <GitBranch className='h-5 w-5 text-primary' aria-hidden='true' />
                    </div>
                    <div className='min-w-0 flex-1'>
                      <div className='flex items-center gap-0.5'>
                        <h3 className='font-semibold break-all leading-tight'>{s.name}</h3>
                        <CopyButton value={s.name} label={t('strategies.copyStrategyName')} className='h-6 w-6 flex-none' />
                      </div>
                      {s.description && <p className='text-xs text-muted-foreground mt-0.5 truncate' title={s.description}>{s.description}</p>}
                    </div>
                    <Button
                      variant='ghost'
                      size='icon'
                      className='h-8 w-8 flex-none text-muted-foreground hover:text-primary'
                      title={t('strategies.viewVisual')}
                      aria-label={t('strategies.viewVisual')}
                      onClick={() => { setActiveStrategyId(s.id); setViewMode('visual'); }}
                    >
                      <Eye className='h-4 w-4' aria-hidden='true' />
                    </Button>
                  </div>
                  {(s.lb_strategy === 'token_threshold' && s.rule_token_threshold > 0) && (
                    <div className='flex flex-wrap items-center justify-end gap-1.5 mt-3'>
                      <Badge variant='success'>
                        {t('strategies.tokenThreshold')}: {s.rule_token_threshold.toLocaleString()} / {tokenPeriodOptions.find(p => p.value === (s.rule_token_period || 'per_day'))?.label || s.rule_token_period}
                      </Badge>
                    </div>
                  )}
                </div>

                <div className='flex items-center gap-4 text-xs text-muted-foreground mb-3'>
                  <span>{t('strategies.routingRules')}: {s.rules?.length || 0}</span>
                  <span>{t('strategies.timeout')}: {s.timeout}s</span>
                  <span>{t('strategies.retryCount')}: {s.retry_count}</span>
                </div>

                {s.rules && s.rules.length > 0 && (
                  <div className='flex flex-col gap-2 overflow-y-auto mb-4 pr-1 h-[176px]'>
                    {s.rules.map((r: any, i: number) => (
                      <div key={i} className='flex flex-none items-center gap-1 text-xs rounded-md border bg-muted/50 px-2 py-1.5'>
                        <span className='font-medium pl-0.5'>{getProviderName(r.provider_id)}</span>
                        <ArrowRight className='h-3 w-3 text-muted-foreground' aria-hidden='true' />
                        <Select
                          value={String(r.model_id)}
                          onValueChange={(v) => r.id && updateRuleMut.mutate({ ruleId: r.id, modelId: Number(v) })}
                          onOpenChange={(o) => { if (o) loadRuleModels(r.provider_id); }}
                        >
                          <SelectTrigger className='h-6 gap-1 border-none bg-transparent px-1 py-0 text-xs text-muted-foreground shadow-none focus:ring-0 hover:text-foreground w-auto max-w-[220px]'>
                            <SelectValue>{r.model_name || ('Model #' + r.model_id)}</SelectValue>
                          </SelectTrigger>
                          <SelectContent className='max-h-64'>
                            {(ruleModelsCache[r.provider_id] || []).map((m: any) => (
                              <SelectItem key={m.id} value={String(m.id)} className='text-xs'>{m.display_name || m.model_id}</SelectItem>
                            ))}
                            {!ruleModelsCache[r.provider_id] && (
                              <div className='flex items-center gap-1.5 px-2 py-1.5 text-xs text-muted-foreground'>
                                <Loader2 className='h-3 w-3 animate-spin' aria-hidden='true' />{t('common.loading')}
                              </div>
                            )}
                          </SelectContent>
                        </Select>
                        <Badge variant='outline' className='px-1 py-0 text-[10px]'>P{r.priority}</Badge>
                      </div>
                    ))}
                  </div>
                )}

                <div className='mt-auto border-t border-border pt-3'>
                  <div className='flex flex-wrap items-center gap-x-1.5 gap-y-1 mb-2'>
                    <Switch checked={s.is_active} onCheckedChange={(v) => toggleActive.mutate({ id: s.id, is_active: v })} aria-label={t('common.enabled')} />
                    <span className='text-xs text-muted-foreground'>{s.is_active ? t('common.enabled') : t('common.disabled')}</span>
                    <Badge variant='outline' className='ml-1'>{lbOptions.find(l => l.value === s.lb_strategy)?.label || s.lb_strategy}</Badge>
                    {testResults[s.id] && (
                      <span className={'ml-auto self-center truncate text-xs font-medium ' + (testResults[s.id].success ? 'text-success' : 'text-destructive')}>
                        {testResults[s.id].message}
                      </span>
                    )}
                  </div>
                  <div className='flex items-center justify-between gap-1'>
                    <Button variant='ghost' size='sm' className='gap-1.5' onClick={() => openEdit(s)}><Pencil className='h-3.5 w-3.5' aria-hidden='true' />{t('common.edit')}</Button>
                    <Button variant='ghost' size='sm' className='gap-1.5' onClick={() => testMut.mutate(s.id)} disabled={testingId === s.id}>
                      {testingId === s.id ? <Loader2 className='h-3.5 w-3.5 animate-spin' aria-hidden='true' /> : <Zap className='h-3.5 w-3.5' aria-hidden='true' />}{t('common.test')}
                    </Button>
                    <Button variant='ghost' size='sm' className='gap-1.5 text-destructive hover:text-destructive' onClick={() => deleteMut.mutate(s.id)}>
                      <Trash2 className='h-3.5 w-3.5' aria-hidden='true' />{t('common.delete')}
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className='max-w-2xl max-h-[85vh] overflow-y-auto'>
          <DialogHeader>
            <DialogTitle>{editing ? t('strategies.editStrategy') : t('strategies.createStrategy')}</DialogTitle>
          </DialogHeader>
          <div className='space-y-5 py-2'>
            <div className='grid grid-cols-2 gap-4'>
              <div><Label>{t('strategies.strategyName')} *</Label><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder='e.g. my-gpt4' /></div>
              <div><Label>{t('providers.description')}</Label><Textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} rows={2} placeholder={t('providers.description')} /></div>
            </div>

            <div className='grid grid-cols-2 gap-4'>
              <div>
                <Label>{t('strategies.loadBalancing')}</Label>
                <Select value={form.lb_strategy} onValueChange={(v) => setForm({ ...form, lb_strategy: v })}>
                  <SelectTrigger className='mt-1.5'><SelectValue /></SelectTrigger>
                  <SelectContent>{lbOptions.map((o) => <SelectItem key={o.value} value={o.value}>{o.label} - {o.desc}</SelectItem>)}</SelectContent>
                </Select>
              </div>
              <div>
                <Label>{t('strategies.timeout')}</Label>
                <Input type='number' value={form.timeout} onChange={(e) => setForm({ ...form, timeout: Number(e.target.value) })} className='mt-1.5' />
              </div>
            </div>

            {form.lb_strategy === 'token_threshold' && (
              <div className='border rounded-lg p-4 bg-muted/30 space-y-3'>
                <div>
                  <Label className='text-sm font-semibold'>{t('strategies.tokenThreshold')}</Label>
                  <p className='text-xs text-muted-foreground mb-2'>{t('strategies.tokenThresholdDesc')}</p>
                </div>
                <div className='grid grid-cols-2 gap-4'>
                  <div>
                    <Label>{t('strategies.tokenThreshold')}</Label>
                    <Input type='number' min='1' value={form.rule_token_threshold || ''} onChange={(e) => setForm({ ...form, rule_token_threshold: Number(e.target.value) })} placeholder='100000' className='mt-1.5' />
                    <p className='text-[10px] text-muted-foreground mt-1'>{t('strategies.tokenThresholdHint')}</p>
                  </div>
                  <div>
                    <Label>{t('strategies.tokenPeriod')}</Label>
                    <Select value={form.rule_token_period} onValueChange={(v) => setForm({ ...form, rule_token_period: v })}>
                      <SelectTrigger className='mt-1.5'><SelectValue /></SelectTrigger>
                      <SelectContent>{tokenPeriodOptions.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}</SelectContent>
                    </Select>
                  </div>
                </div>
              </div>
            )}

            <div className='grid grid-cols-2 gap-4'>
              <div>
                <Label>{t('strategies.retryCount')}</Label>
                <Input type='number' value={form.retry_count} onChange={(e) => setForm({ ...form, retry_count: Number(e.target.value) })} className='mt-1.5' />
              </div>
              <div className='flex items-end gap-2 pb-0.5'>
                <Switch checked={form.is_active} onCheckedChange={(v) => setForm({ ...form, is_active: v })} />
                <Label>{t('strategies.enable')}</Label>
              </div>
            </div>

            <div className='border-t pt-5'>
              <Label className='text-sm font-semibold'>{t('strategies.routingRules')}</Label>
              <div className='mt-3 space-y-2'>
                {form.rules.map((r, idx) => (
                  <div key={idx} className='flex items-center gap-2 text-sm bg-muted/50 rounded-lg p-3'>
                    <span className='flex-1 font-medium'>{getProviderName(r.provider_id)}</span>
                    <ArrowRight className='h-3 w-3 text-muted-foreground' />
                    <span className='text-muted-foreground'>{r.model_name || ('Model #' + r.model_id)}</span>
                    <Badge variant='outline' className='text-xs'>P{r.priority}</Badge>
                    <div className='flex items-center gap-1'>
                      <span className='text-[10px] text-muted-foreground'>W</span>
                      <Input type='number' className='h-6 w-14 text-xs px-1' value={r.weight} onChange={(e) => {
                        const newRules = [...form.rules];
                        newRules[idx] = { ...newRules[idx], weight: Number(e.target.value) || 1 };
                        setForm({ ...form, rules: newRules });
                      }} />
                    </div>
                    <Button variant='ghost' size='icon' className='h-6 w-6 ml-1' onClick={() => removeRule(idx)}><X className='h-3 w-3' /></Button>
                  </div>
                ))}
                {form.rules.length === 0 && <p className='text-xs text-muted-foreground py-2'>{t('strategies.noRules')}</p>}
              </div>
              <div className='mt-3 grid grid-cols-4 gap-2'>
                <Select value={String(newRule.provider_id)} onValueChange={(v) => setNewRule({ ...newRule, provider_id: Number(v), model_id: 0 })}>
                  <SelectTrigger><SelectValue placeholder={t('strategies.selectPlatform')} /></SelectTrigger>
                  <SelectContent>{providers.map((p: any) => <SelectItem key={p.id} value={String(p.id)}>{p.name}</SelectItem>)}</SelectContent>
                </Select>
                <Select value={String(newRule.model_id)} onValueChange={(v) => setNewRule({ ...newRule, model_id: Number(v) })}>
                  <SelectTrigger><SelectValue placeholder={t('strategies.selectModel')} /></SelectTrigger>
                  <SelectContent>{(models as any[]).map((m: any) => <SelectItem key={m.id} value={String(m.id)}>{m.model_id}</SelectItem>)}</SelectContent>
                </Select>
                <Input type='number' placeholder={t('strategies.priorityPlaceholder')} value={newRule.priority} onChange={(e) => setNewRule({ ...newRule, priority: Number(e.target.value) })} />
                <Button variant='outline' onClick={addRule} disabled={!newRule.provider_id || !newRule.model_id}><Plus className='h-4 w-4 mr-1' />{t('strategies.addRule')}</Button>
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant='outline' onClick={() => setOpen(false)}>{t('common.cancel')}</Button>
            <Button onClick={save} disabled={!form.name}>{editing ? t('common.update') : t('common.create')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

    {/* Create Strategy by Model Dialog */}
    <Dialog open={byModelOpen} onOpenChange={setByModelOpen}>
      <DialogContent className='max-w-4xl max-h-[85vh] flex flex-col'>
        <DialogHeader><DialogTitle>{t('strategies.createByModel')}</DialogTitle></DialogHeader>
        <div className='flex-1 flex gap-3 overflow-hidden min-h-0'>
          <div className='w-48 flex-shrink-0 border rounded-lg overflow-y-auto'>
            <div className='p-2 text-xs font-semibold text-muted-foreground border-b'>{t('providers.title')}</div>
            {byModelLoading && byModelSelProvider === 0 && <div className='p-4 text-center text-muted-foreground'><Loader2 className='h-4 w-4 animate-spin mx-auto' /></div>}
            {byModelProviders.map((p: any) => (
              <button key={p.id} type='button' onClick={() => loadByModelModels(p.id)}
                className={'w-full text-left px-3 py-2 text-sm transition-colors ' + (byModelSelProvider === p.id ? 'bg-primary/10 text-primary font-medium' : 'hover:bg-muted')}>
                {p.name}
              </button>
            ))}
          </div>
          <div className='flex-1 flex flex-col border rounded-lg overflow-hidden min-w-0'>
            <div className='p-2 border-b flex items-center gap-2'>
              <div className='relative flex-1'>
                <Search className='absolute left-2 top-1/2 -translate-y-1/2 h-3 w-3 text-muted-foreground' />
                <Input value={byModelSearch} onChange={e => { setByModelSearch(e.target.value); if (e.target.value.trim()) loadAllByModelModels(); }} placeholder={t('common.search') + '...'} className='pl-7 h-7 text-xs' />
              </div>
              <Button variant='outline' size='sm' className='h-7 text-xs' onClick={toggleByModelAll}>
                {byModelChecked.size === byModelFiltered.length && byModelFiltered.length > 0 ? t('strategies.deselectAll') : t('strategies.selectAll')}
              </Button>
              <span className='text-xs text-muted-foreground whitespace-nowrap'>{byModelChecked.size}/{byModelFiltered.length}</span>
            </div>
            <div className='flex-1 overflow-y-auto'>
              {byModelSearchActive ? (
                byModelLoading || !byModelAllLoaded ? (
                  <div className='flex items-center justify-center h-full'><Loader2 className='h-5 w-5 animate-spin text-muted-foreground' /></div>
                ) : byModelFiltered.length === 0 ? (
                  <div className='flex items-center justify-center h-full text-muted-foreground text-sm'>{t('providers.noModels')}</div>
                ) : (
                  byModelFiltered.map((m: any) => (
                    <label key={`${m.provider_id}-${m.id}`} onClick={() => toggleByModelCheck(m.id)} className={'flex items-center gap-2 px-3 py-2 text-sm cursor-pointer transition-colors hover:bg-muted/50 ' + (byModelChecked.has(m.id) ? 'bg-primary/5' : '')}>
                      <div className={'w-4 h-4 rounded border flex items-center justify-center flex-shrink-0 transition-colors ' + (byModelChecked.has(m.id) ? 'bg-primary border-primary text-primary-foreground' : 'border-muted-foreground/30')}>
                        {byModelChecked.has(m.id) && <Check className='h-3 w-3' />}
                      </div>
                      <span className='font-mono text-xs truncate'>{m.model_id}</span>
                      {m.display_name && m.display_name !== m.model_id && <span className='text-xs text-muted-foreground truncate'>{'·'} {m.display_name}</span>}
                      {m.provider_name && <span className='ml-auto flex-none text-[10px] text-muted-foreground/70'>{m.provider_name}</span>}
                    </label>
                  ))
                )
              ) : byModelSelProvider === 0 ? (
                <div className='flex items-center justify-center h-full text-muted-foreground text-sm'>{t('strategies.selectPlatformFirst')}</div>
              ) : byModelLoading ? (
                <div className='flex items-center justify-center h-full'><Loader2 className='h-5 w-5 animate-spin text-muted-foreground' /></div>
              ) : byModelFiltered.length === 0 ? (
                <div className='flex items-center justify-center h-full text-muted-foreground text-sm'>{t('providers.noModels')}</div>
              ) : (
                byModelFiltered.map((m: any) => (
                  <label key={m.id} onClick={() => toggleByModelCheck(m.id)} className={'flex items-center gap-2 px-3 py-2 text-sm cursor-pointer transition-colors hover:bg-muted/50 ' + (byModelChecked.has(m.id) ? 'bg-primary/5' : '')}>
                    <div className={'w-4 h-4 rounded border flex items-center justify-center flex-shrink-0 transition-colors ' + (byModelChecked.has(m.id) ? 'bg-primary border-primary text-primary-foreground' : 'border-muted-foreground/30')}>
                      {byModelChecked.has(m.id) && <Check className='h-3 w-3' />}
                    </div>
                    <span className='font-mono text-xs truncate'>{m.model_id}</span>
                    {m.display_name && m.display_name !== m.model_id && <span className='text-xs text-muted-foreground truncate'>{'·'} {m.display_name}</span>}
                  </label>
                ))
              )}
            </div>
          </div>
          <div className='w-56 flex-shrink-0 border rounded-lg flex flex-col overflow-hidden'>
            <div className='p-2 border-b text-xs font-semibold text-muted-foreground'>{t('strategies.selectedModels')} ({byModelChecked.size})</div>
            <div className='flex-1 overflow-y-auto'>
              {byModelChecked.size === 0 ? (
                <div className='flex items-center justify-center h-full text-muted-foreground text-xs p-2 text-center'>{t('strategies.hintSelectModels')}</div>
              ) : (
                Array.from(byModelChecked).map(modelId => {
                  const m = byModelLookup.get(modelId);
                  if (!m) return null;
                  return (
                    <div key={m.id} className='flex items-center gap-1.5 px-2.5 py-1.5 text-xs border-b last:border-b-0'>
                      <span className='font-mono truncate flex-1' title={m.model_id}>{m.model_id}</span>
                      {m.provider_name && <span className='flex-none text-[10px] text-muted-foreground/70'>{m.provider_name}</span>}
                      <button type='button' onClick={() => toggleByModelCheck(m.id)} className='text-muted-foreground hover:text-destructive'><X className='h-3 w-3' /></button>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button variant='outline' onClick={() => setByModelOpen(false)}>{t('common.cancel')}</Button>
          <Button disabled={byModelChecked.size === 0} onClick={() => {
            const rules: Rule[] = [];
            let idx = 0;
            byModelChecked.forEach(modelId => {
              const m = byModelLookup.get(modelId);
              if (!m) return;
              rules.push({ provider_id: m.provider_id, model_id: m.id, model_name: m.model_id, priority: idx, weight: 1, is_active: true });
              idx++;
            });
            setForm({ name: '', description: '', lb_strategy: 'round_robin', key_strategy: 'round_robin', key_switch_mode: 'none', key_rpm_threshold: 0, key_count_threshold: 0, rule_token_threshold: 0, rule_token_period: 'per_day', is_active: true, timeout: 120, retry_count: 2, rules });
            setEditing(null);
            setByModelOpen(false);
            setOpen(true);
          }} className='gap-2'><ListChecks className='h-4 w-4' />{t('strategies.createStrategy')}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    </div>
  );
}

// ---------------------------------------------------------------------------
// 可视化视图：左侧调用链（拖拽排序 + 快捷换模型）+ 右侧素材面板（平台/账号/免费模型）
// ---------------------------------------------------------------------------

function iconUrl(icon: string | undefined): string {
  if (!icon) return "";
  if (icon.startsWith("http")) return icon;
  if (icon.startsWith("icons/")) return "/api/icons/file/" + icon.replace("icons/", "");
  return icon;
}

function StrategyVisualMode({
  strategies, activeStrategy, onSelectStrategy, providers, accountsQueryEnabled, privacyMode,
  ruleModelsCache, loadRuleModels, updateRuleMut, getProviderName, onEditStrategy,
}: {
  strategies: any[];
  activeStrategy: any;
  onSelectStrategy: (id: number) => void;
  providers: any[];
  accountsQueryEnabled: boolean;
  privacyMode: boolean;
  ruleModelsCache: Record<number, any[]>;
  loadRuleModels: (providerId: number) => void;
  updateRuleMut: any;
  getProviderName: (id: number) => string;
  onEditStrategy: (s: any) => void;
}) {
  const { t } = useI18n();
  const qc = useQueryClient();

  const sortedRules = [...(activeStrategy?.rules || [])].sort((a: any, b: any) => a.priority - b.priority || a.id - b.id);

  // 加入规则（策略底部追加）
  const addRuleMut = useMutation({
    mutationFn: ({ providerId, modelId }: { providerId: number; modelId: number }) =>
      addRule(activeStrategy.id, { provider_id: providerId, model_id: modelId, priority: sortedRules.length, weight: 1, is_active: true }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['strategies'] }); toast({ title: t('strategies.ruleAdded'), variant: 'success' }); },
    onError: (e: any) => toast({ title: t('strategies.ruleAddFailed'), description: e?.response?.data?.detail, variant: 'destructive' }),
  });

  const deleteRuleMut = useMutation({
    mutationFn: (ruleId: number) => deleteRule(ruleId),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['strategies'] }); toast({ title: t('strategies.ruleDeleted'), variant: 'success' }); },
  });

  // 模型开关：切换规则 is_active，关闭后 Balancer 不再路由到该模型
  const toggleRuleMut = useMutation({
    mutationFn: ({ ruleId, is_active }: { ruleId: number; is_active: boolean }) => updateRule(ruleId, { is_active }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['strategies'] }); },
    onError: (e: any) => toast({ title: t('strategies.updateFailed'), description: e?.response?.data?.detail, variant: 'destructive' }),
  });

  // 单条规则测试：各行独立 loading，结果写入与「测试全部」共用的 ruleTestResults
  const [testingRuleIds, setTestingRuleIds] = useState<Set<number>>(() => new Set());
  const testOneRule = async (ruleId: number) => {
    if (testingRuleIds.has(ruleId) || !activeStrategy) return;
    setTestingRuleIds((prev) => new Set(prev).add(ruleId));
    try {
      const res = await testStrategyRule(activeStrategy.id, ruleId);
      const d = res.data || {};
      setRuleTestResults((prev) => ({ ...prev, [ruleId]: { success: !!d.success, latency_ms: d.latency_ms || 0, message: d.message || '' } }));
      qc.invalidateQueries({ queryKey: ['models'] });
      toast({ title: d.success ? t('strategies.ruleOk') : t('strategies.ruleFail'), description: `${d.model_name || ''} · ${d.latency_ms ?? 0}ms`, variant: d.success ? 'success' : 'destructive' });
    } catch (e: any) {
      toast({ title: t('strategies.ruleFail'), description: e?.response?.data?.detail, variant: 'destructive' });
    } finally {
      setTestingRuleIds((prev) => { const n = new Set(prev); n.delete(ruleId); return n; });
    }
  };

  // 拖拽重排：本地数组换位 + priority=index 一次保存（PUT 全删重建）
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [overIndex, setOverIndex] = useState<number | null>(null);
  const reorderMut = useMutation({
    mutationFn: (rules: any[]) => updateStrategy(activeStrategy.id, {
      name: activeStrategy.name, description: activeStrategy.description || '', lb_strategy: activeStrategy.lb_strategy,
      key_strategy: activeStrategy.key_strategy || 'round_robin', key_switch_mode: activeStrategy.key_switch_mode || 'none',
      key_rpm_threshold: activeStrategy.key_rpm_threshold || 0, key_count_threshold: activeStrategy.key_count_threshold || 0,
      rule_token_threshold: activeStrategy.rule_token_threshold || 0, rule_token_period: activeStrategy.rule_token_period || 'per_day',
      is_active: activeStrategy.is_active, timeout: activeStrategy.timeout, retry_count: activeStrategy.retry_count,
      rules: rules.map((r: any, i: number) => ({ provider_id: r.provider_id, model_id: r.model_id, model_name: r.model_name || '', priority: i, weight: r.weight, is_active: r.is_active })),
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['strategies'] }); toast({ title: t('strategies.reordered'), variant: 'success' }); },
    onError: (e: any) => toast({ title: t('strategies.updateFailed'), description: e?.response?.data?.detail, variant: 'destructive' }),
  });

  const handleDrop = () => {
    if (dragIndex === null || overIndex === null || dragIndex === overIndex) { setDragIndex(null); setOverIndex(null); return; }
    const next = [...sortedRules];
    const [moved] = next.splice(dragIndex, 1);
    next.splice(overIndex, 0, moved);
    setDragIndex(null); setOverIndex(null);
    reorderMut.mutate(next);
  };

  // 隐私打码：按 provider_id 稳定映射为「标识N」
  const masked = (pid: number) => `#${t('strategies.maskedName')}${(providers.findIndex((p: any) => p.id === pid) + 1) || pid}`;
  const providerLabel = (pid: number) => (privacyMode ? masked(pid) : getProviderName(pid));

  // 一键测试全部规则：后端并发探测，返回每条规则的连通性与延迟
  const [ruleTestResults, setRuleTestResults] = useState<Record<number, { success: boolean; latency_ms: number; message: string }>>({});
  const [testingAll, setTestingAll] = useState(false);
  const testAllRules = async () => {
    if (!activeStrategy || testingAll) return;
    setTestingAll(true);
    try {
      const res = await testStrategyRules(activeStrategy.id);
      const map: Record<number, any> = {};
      for (const r of res.data.results || []) {
        map[r.rule_id] = { success: r.success, latency_ms: r.latency_ms, message: r.message };
      }
      setRuleTestResults(map);
      qc.invalidateQueries({ queryKey: ['models'] });
    } catch (e: any) {
      toast({ title: t('strategies.ruleAddFailed'), description: e?.response?.data?.detail, variant: 'destructive' });
    }
    setTestingAll(false);
  };

  // 实时调用状态：订阅 monitor SSE 的 start/done 事件，按 rid 关联。
  // start 点亮对应 rule 的 key，同 rid 的 done 熄灭；支持同一 key 多并发（引用计数）。
  const [activeCallCounts, setActiveCallCounts] = useState<Record<string, number>>({});
  useEffect(() => {
    const es = new EventSource(monitorStreamUrl());
    const ridToKey = new Map<string, string>();
    const timers = new Map<string, ReturnType<typeof setTimeout>>();

    const drop = (rid: string) => {
      const key = ridToKey.get(rid);
      const tm = timers.get(rid);
      if (tm) clearTimeout(tm);
      timers.delete(rid);
      ridToKey.delete(rid);
      if (key) {
        setActiveCallCounts((prev) => {
          const next = { ...prev };
          const c = (next[key] || 0) - 1;
          if (c <= 0) delete next[key]; else next[key] = c;
          return next;
        });
      }
    };

    es.onmessage = (msg) => {
      let ev: any;
      try { ev = JSON.parse(msg.data); } catch { return; }
      if (!ev || !ev.rid) return;
      if (ev.phase === "start") {
        const key = callKeyOf(ev.strategy_id, ev.provider_id, ev.model);
        ridToKey.set(ev.rid, key);
        setActiveCallCounts((prev) => ({ ...prev, [key]: (prev[key] || 0) + 1 }));
        // 兜底：迟迟没有 done 时自动熄灭，避免永久显示"调用中"
        const tm = setTimeout(() => drop(ev.rid), 200000);
        const old = timers.get(ev.rid);
        if (old) clearTimeout(old);
        timers.set(ev.rid, tm);
      } else {
        // done（完成）事件：熄灭对应 rid
        drop(ev.rid);
      }
    };
    return () => {
      es.close();
      timers.forEach((tm) => clearTimeout(tm));
    };
  }, []);

  return (
    <div className='flex gap-4'>
      {/* 左侧：策略名条 + 调用链 */}
      <div className='min-w-0 flex-1 space-y-3'>
        {strategies.length === 0 ? (
          <EmptyState icon={<GitBranch />} title={t('strategies.visualNoStrategies')} />
        ) : (
          <>
            <div className='flex flex-wrap gap-2'>
              {strategies.map((s: any) => (
                <button
                  key={s.id}
                  type='button'
                  onClick={() => onSelectStrategy(s.id)}
                  className={'inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm transition-colors ' + (activeStrategy?.id === s.id ? 'border-primary/60 bg-primary/10 font-medium text-primary' : 'border-border text-muted-foreground hover:bg-accent/40 hover:text-foreground')}
                >
                  <GitBranch className='h-3.5 w-3.5' aria-hidden='true' />
                  {s.name}
                  <Badge variant='outline' className='px-1 py-0 text-[10px]'>{s.rules?.length || 0}</Badge>
                </button>
              ))}
            </div>

            {activeStrategy && (
              <Card>
                <CardContent className='p-4'>
                  <div className='mb-3 flex items-center justify-between gap-2'>
                    <div className='flex min-w-0 items-center gap-2'>
                      <span className='truncate text-sm font-semibold'>{activeStrategy.name}</span>
                      <CopyButton value={activeStrategy.name} label={t('strategies.copyStrategyName')} className='h-6 w-6' />
                      <Badge variant='outline'>{lbOptionsLabel(activeStrategy.lb_strategy)}</Badge>
                      {activeStrategy.is_active ? <Badge variant='success'>{t('common.enabled')}</Badge> : <Badge variant='secondary'>{t('common.disabled')}</Badge>}
                      <Button variant='outline' size='sm' className='h-7 gap-1.5' disabled={testingAll} onClick={testAllRules} title={t('strategies.testAllRules')}>
                        {testingAll ? <Loader2 className='h-3.5 w-3.5 animate-spin' aria-hidden='true' /> : <Zap className='h-3.5 w-3.5' aria-hidden='true' />}
                        {testingAll ? t('strategies.testing') : t('strategies.testAllRules')}
                      </Button>
                      <Button variant='outline' size='sm' className='h-7 gap-1.5' onClick={() => onEditStrategy(activeStrategy)} title={t('strategies.strategyEdit')}>
                        <Pencil className='h-3.5 w-3.5' aria-hidden='true' />
                        {t('strategies.strategyEdit')}
                      </Button>
                    </div>
                    <p className='hidden flex-none text-xs text-muted-foreground sm:block'>{t('strategies.visualHint')}</p>
                  </div>

                  <div className='overflow-x-auto'>
                    <div className={RULE_ROW_GRID + ' px-3 pb-1 text-[10px] font-semibold text-muted-foreground'}>
                      <span />
                      <span className='text-center'>#</span>
                      <span>{t('strategies.colPlatform')}</span>
                      <span />
                      <span className='text-center'>{t('strategies.colEnabled')}</span>
                      <span>{t('strategies.colModel')}</span>
                      <span className='text-center'>{t('strategies.colPriority')}</span>
                      <span>{t('strategies.colTest')}</span>
                      <span>{t('strategies.colCallStatus')}</span>
                      <span className='text-center'>{t('strategies.colTestBtn')}</span>
                      <span />
                    </div>
                    <div className='space-y-1.5' onDragOver={(e) => e.preventDefault()} onDrop={handleDrop}>
                      {sortedRules.map((r: any, i: number) => {
                        const models = ruleModelsCache[r.provider_id];
                        const modelKey = r.model_name || (models || []).find((m: any) => m.id === r.model_id)?.model_id || '';
                        const isCalling = (activeCallCounts[callKeyOf(activeStrategy.id, r.provider_id, modelKey)] || 0) > 0;
                        return (
                        <div
                          key={r.id}
                          draggable
                          onDragStart={() => setDragIndex(i)}
                          onDragEnter={() => setOverIndex(i)}
                          onDragEnd={handleDrop}
                          className={RULE_ROW_GRID + ' rounded-lg border bg-muted/40 px-3 py-2.5 transition-colors ' + (dragIndex === i ? 'border-primary/50 opacity-60' : overIndex === i && dragIndex !== null ? 'border-primary/40 bg-primary/5' : 'border-border')}
                        >
                          <GripVertical className='h-4 w-4 cursor-grab text-muted-foreground/50' aria-hidden='true' />
                          <span className='text-center text-xs font-semibold tabular-nums text-muted-foreground'>{i + 1}</span>
                          <span className='min-w-0 truncate text-sm font-medium' title={providerLabel(r.provider_id)}>{providerLabel(r.provider_id)}</span>
                          <ArrowRight className='h-3 w-3 text-muted-foreground' aria-hidden='true' />
                          <Switch
                            checked={r.is_active !== false}
                            onCheckedChange={(v) => toggleRuleMut.mutate({ ruleId: r.id, is_active: v })}
                            className='justify-self-center'
                            aria-label={t('strategies.colEnabled')}
                          />
                          <Select
                            value={String(r.model_id)}
                            onValueChange={(v) => updateRuleMut.mutate({ ruleId: r.id, modelId: Number(v) })}
                            onOpenChange={(o) => { if (o) loadRuleModels(r.provider_id); }}
                          >
                            <SelectTrigger className='h-7 w-full min-w-0 gap-1 bg-background px-2 text-xs'>
                              <SelectValue>{r.model_name || ('Model #' + r.model_id)}</SelectValue>
                            </SelectTrigger>
                            <SelectContent className='max-h-64'>
                              {(models || []).map((m: any) => (
                                <SelectItem key={m.id} value={String(m.id)} className='text-xs'>{m.display_name || m.model_id}</SelectItem>
                              ))}
                              {!models && (
                                <div className='flex items-center gap-1.5 px-2 py-1.5 text-xs text-muted-foreground'>
                                  <Loader2 className='h-3 w-3 animate-spin' aria-hidden='true' />{t('common.loading')}
                                </div>
                              )}
                            </SelectContent>
                          </Select>
                          <Badge variant='outline' className='justify-center px-1 py-0 text-[10px]'>P{r.priority}</Badge>
                          {(() => {
                            const tr = ruleTestResults[r.id];
                            if (!tr) return <Badge variant='secondary' className='justify-center px-1.5 py-0 text-[10px] text-muted-foreground'>{t('strategies.noResult')}</Badge>;
                            return tr.success
                              ? <Badge variant='success' className='justify-center px-1.5 py-0 text-[10px]'>{t('strategies.ruleOk')} {tr.latency_ms}ms</Badge>
                              : <Badge variant='destructive' className='justify-center px-1.5 py-0 text-[10px]' title={tr.message}>{tr.message === 'Rule target missing' ? t('strategies.ruleInvalid') : t('strategies.ruleFail')}</Badge>;
                          })()}
                          {isCalling ? (
                            <span className='inline-flex w-fit items-center gap-1 rounded-full bg-primary/15 px-2 py-0.5 text-[10px] font-medium text-primary'>
                              <span className='h-1.5 w-1.5 animate-pulse rounded-full bg-primary' />
                              {t('strategies.calling')}
                            </span>
                          ) : (
                            <span className='inline-flex w-fit items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground'>
                              <span className='h-1.5 w-1.5 rounded-full bg-muted-foreground/50' />
                              {t('strategies.idle')}
                            </span>
                          )}
                          <Button
                            variant='outline'
                            size='icon'
                            className='h-7 w-7 text-primary hover:text-primary'
                            disabled={testingRuleIds.has(r.id)}
                            title={t('strategies.testThisRule')}
                            aria-label={t('strategies.testThisRule')}
                            onClick={() => testOneRule(r.id)}
                          >
                            {testingRuleIds.has(r.id) ? <Loader2 className='h-3.5 w-3.5 animate-spin' aria-hidden='true' /> : <Zap className='h-3.5 w-3.5' aria-hidden='true' />}
                          </Button>
                          <Button variant='ghost' size='icon' className='h-7 w-7 text-destructive hover:text-destructive' aria-label={t('common.delete')} onClick={() => deleteRuleMut.mutate(r.id)}>
                            <Trash2 className='h-3.5 w-3.5' aria-hidden='true' />
                          </Button>
                        </div>
                        );
                      })}
                      {sortedRules.length === 0 && (
                        <p className='py-8 text-center text-sm text-muted-foreground'>{t('strategies.noRules')}</p>
                      )}
                    </div>
                  </div>
                </CardContent>
              </Card>
            )}
          </>
        )}
      </div>

      {/* 右侧素材面板 */}
      <SourcePanel
        activeStrategy={activeStrategy}
        providers={providers}
        accountsQueryEnabled={accountsQueryEnabled}
        privacyMode={privacyMode}
        providerLabel={providerLabel}
        ruleModelsCache={ruleModelsCache}
        loadRuleModels={loadRuleModels}
        addRuleMut={addRuleMut}
      />
    </div>
  );
}

function lbOptionsLabel(v: string): string { return v; }

// 规则行统一栅格模板：仅模型列弹性，其余列固定，保证表头与各行严格对齐
const RULE_ROW_GRID = 'grid grid-cols-[16px_24px_140px_12px_44px_minmax(140px,1fr)_44px_92px_80px_56px_28px] items-center gap-2 min-w-[760px]';

// 进行中请求聚合键：与 monitor SSE 的 start 事件字段一致（strategy|provider|model）
function callKeyOf(strategyId: number | null | undefined, providerId: number | null | undefined, model: string): string {
  return `${strategyId ?? 0}|${providerId ?? 0}|${model || ''}`;
}

function SourcePanel({
  activeStrategy, providers, accountsQueryEnabled, privacyMode, providerLabel, ruleModelsCache, loadRuleModels, addRuleMut,
}: {
  activeStrategy: any;
  providers: any[];
  accountsQueryEnabled: boolean;
  privacyMode: boolean;
  providerLabel: (pid: number) => string;
  ruleModelsCache: Record<number, any[]>;
  loadRuleModels: (providerId: number) => void;
  addRuleMut: any;
}) {
  const { t } = useI18n();
  const [expandedPid, setExpandedPid] = useState<number | null>(null);
  const [expandedAcc, setExpandedAcc] = useState<number | null>(null);
  // 平台标签页下的全量模型搜索：输入后并发拉取所有平台模型，跨全平台按模型名匹配
  const [platformSearch, setPlatformSearch] = useState('');
  const [platformSearchRows, setPlatformSearchRows] = useState<{ pid: number; pName: string; m: any }[]>([]);
  const [platformSearchLoading, setPlatformSearchLoading] = useState(false);
  const [platformSearchLoaded, setPlatformSearchLoaded] = useState(false);

  const { data: accountsRaw = [] } = useQuery({
    queryKey: ['accounts'],
    queryFn: () => getAccounts().then((r) => r.data),
    enabled: accountsQueryEnabled,
  });
  // /api/accounts 返回 { accounts: [...] }（plugin/echo/native 合并快照），解包为数组
  const accounts: any[] = Array.isArray(accountsRaw) ? accountsRaw : (accountsRaw as any)?.accounts || [];

  const usedProviderIds = new Set((activeStrategy?.rules || []).map((r: any) => r.provider_id));
  const providersSorted = [...providers].sort((a: any, b: any) => (Number(usedProviderIds.has(a.id)) - Number(usedProviderIds.has(b.id))));

  // 全量搜索：首次输入时并发拉取所有平台模型，结果缓存复用
  const loadAllForPlatformSearch = async () => {
    if (platformSearchLoaded || platformSearchLoading) return;
    setPlatformSearchLoading(true);
    try {
      const settled = await Promise.all(providers.map(async (p: any) => {
        try {
          const res = await getModels(p.id);
          return (res.data || []).map((m: any) => ({ pid: p.id, pName: p.name, m }));
        } catch (_e) { return []; }
      }));
      setPlatformSearchRows(settled.flat());
      setPlatformSearchLoaded(true);
    } catch (_e) { toast({ title: t('common.error'), variant: 'destructive' }); }
    setPlatformSearchLoading(false);
  };
  const platformSearchActive = platformSearch.trim().length > 0;
  const platformSearchFiltered = platformSearchRows.filter(({ pName, m }) => {
    const q = platformSearch.trim().toLowerCase();
    return (m.model_id || '').toLowerCase().includes(q) || (m.display_name || '').toLowerCase().includes(q) || (pName || '').toLowerCase().includes(q);
  });

  // 免费模型：聚合已接入平台中 model_id 含 free 的模型
  const freeRows: { pid: number; pName: string; m: any }[] = [];
  for (const p of providers) {
    const models = ruleModelsCache[p.id];
    if (!models) continue;
    for (const m of models) {
      if ((m.model_id || '').toLowerCase().includes('free')) freeRows.push({ pid: p.id, pName: p.name, m });
    }
  }

  const toggleExpand = (pid: number) => {
    setExpandedPid(prev => (prev === pid ? null : pid));
    loadRuleModels(pid);
  };

  // 快捷加入：取该平台模型列表的第一个模型（无缓存则先拉取），复用 addToStrategy（含查重）
  const quickAddFirst = async (pid: number) => {
    if (!pid) return;
    let models = ruleModelsCache[pid];
    if (!models) {
      try {
        const res = await getModels(pid);
        models = res.data || [];
        setRuleModelsCache(prev => ({ ...prev, [pid]: models }));
      } catch (_e) {
        toast({ title: t('strategies.ruleAddFailed'), variant: 'destructive' });
        return;
      }
    }
    if (!models.length) {
      toast({ title: t('strategies.noModelsHint'), variant: 'warning' });
      return;
    }
    addToStrategy(pid, models[0]);
  };

  const QuickAddButton = ({ pid, disabled }: { pid: number; disabled?: boolean }) => (
    <button
      type='button'
      disabled={disabled || addRuleMut.isPending}
      title={t('strategies.quickAddFirst')}
      aria-label={t('strategies.quickAddFirst')}
      onClick={(e) => { e.stopPropagation(); quickAddFirst(pid); }}
      className='flex h-6 w-6 flex-none items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-primary/10 hover:text-primary disabled:opacity-40'
    >
      <Plus className='h-3.5 w-3.5' aria-hidden='true' />
    </button>
  );

  const addToStrategy = (pid: number, m: any) => {
    if (!activeStrategy) return;
    // 查重：同一平台 + 同一模型已存在时提示，不重复添加
    const dup = (activeStrategy.rules || []).some((r: any) => r.provider_id === pid && r.model_id === m.id);
    if (dup) {
      toast({ title: t('strategies.duplicateRule'), description: `${providerLabel(pid)} · ${m.display_name || m.model_id}`, variant: 'warning' });
      return;
    }
    addRuleMut.mutate({ providerId: pid, modelId: m.id });
  };

  const ModelRow = ({ pid, pName, m, showProvider }: { pid: number; pName: string; m: any; showProvider?: boolean }) => (
    <button
      type='button'
      onClick={() => addToStrategy(pid, m)}
      disabled={addRuleMut.isPending}
      className='flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs transition-colors hover:bg-primary/10 disabled:opacity-50'
    >
      <Plus className='h-3 w-3 flex-none text-primary' aria-hidden='true' />
      <span className='min-w-0 flex-1 truncate' title={m.model_id}>{m.display_name || m.model_id}</span>
      {(privacyMode || showProvider) && <span className='flex-none text-[10px] text-muted-foreground/60'>{providerLabel(pid)}</span>}
    </button>
  );

  const providerRow = (p: any) => (
    <div key={p.id} className='rounded-lg border bg-muted/30'>
      <button
        type='button'
        onClick={() => toggleExpand(p.id)}
        className='flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-muted/50'
      >
        {expandedPid === p.id ? <ChevronDown className='h-3.5 w-3.5 flex-none text-muted-foreground' aria-hidden='true' /> : <ChevronRight className='h-3.5 w-3.5 flex-none text-muted-foreground' aria-hidden='true' />}
        {p.icon ? <img src={iconUrl(p.icon)} alt='' className='h-4 w-4 flex-none rounded' /> : <Server className='h-4 w-4 flex-none text-muted-foreground' aria-hidden='true' />}
        <span className='min-w-0 flex-1 truncate text-sm font-medium' title={providerLabel(p.id)}>{providerLabel(p.id)}</span>
        {usedProviderIds.has(p.id) && <Badge variant='secondary' className='flex-none px-1 py-0 text-[10px]'>✓</Badge>}
        {ruleModelsCache[p.id] && <span className='flex-none text-[10px] text-muted-foreground'>{ruleModelsCache[p.id].length}</span>}
        <QuickAddButton pid={p.id} />
      </button>
      {expandedPid === p.id && (
        <div className='border-t px-1.5 py-1'>
          {(ruleModelsCache[p.id] || []).map((m: any) => <ModelRow key={m.id} pid={p.id} pName={p.name} m={m} />)}
          {!ruleModelsCache[p.id] && (
            <div className='flex items-center gap-1.5 px-2 py-2 text-xs text-muted-foreground'>
              <Loader2 className='h-3 w-3 animate-spin' aria-hidden='true' />{t('common.loading')}
            </div>
          )}
        </div>
      )}
    </div>
  );

  return (
    <div className='hidden w-80 flex-none md:block'>
      <Tabs defaultValue='platforms'>
        <TabsList className='w-full'>
          <TabsTrigger value='platforms' className='flex-1 gap-1 text-xs'><Server className='h-3.5 w-3.5' aria-hidden='true' />{t('strategies.tabPlatforms')}</TabsTrigger>
          <TabsTrigger value='accounts' className='flex-1 gap-1 text-xs'><UserRound className='h-3.5 w-3.5' aria-hidden='true' />{t('strategies.tabAccounts')}</TabsTrigger>
          <TabsTrigger value='free' className='flex-1 gap-1 text-xs'><Gift className='h-3.5 w-3.5' aria-hidden='true' />{t('strategies.tabFreeModels')}</TabsTrigger>
        </TabsList>

        <TabsContent value='platforms' className='mt-2 max-h-[calc(100vh-16rem)] space-y-1.5 overflow-y-auto pr-1'>
          <div className='relative'>
            <Search className='absolute left-2 top-1/2 -translate-y-1/2 h-3 w-3 text-muted-foreground' aria-hidden='true' />
            <Input
              value={platformSearch}
              onChange={e => { setPlatformSearch(e.target.value); if (e.target.value.trim()) loadAllForPlatformSearch(); }}
              placeholder={t('common.search') + '...'}
              className='h-7 pl-7 text-xs'
            />
          </div>
          {platformSearchActive ? (
            platformSearchLoading || !platformSearchLoaded ? (
              <div className='flex items-center justify-center py-8'><Loader2 className='h-5 w-5 animate-spin text-muted-foreground' aria-hidden='true' /></div>
            ) : platformSearchFiltered.length === 0 ? (
              <p className='py-6 text-center text-xs text-muted-foreground'>{t('providers.noModels')}</p>
            ) : (
              <div className='rounded-lg border bg-muted/30'>
                {platformSearchFiltered.map(({ pid, pName, m }, idx) => <ModelRow key={`${pid}-${m.id}-${idx}`} pid={pid} pName={pName} m={m} showProvider />)}
              </div>
            )
          ) : (
            providersSorted.map(providerRow)
          )}
        </TabsContent>

        <TabsContent value='accounts' className='mt-2 max-h-[calc(100vh-16rem)] space-y-1.5 overflow-y-auto pr-1'>
          {accounts.length === 0 && <p className='py-6 text-center text-xs text-muted-foreground'>-</p>}
          {accounts.map((aRaw: any) => {
            // 插件/echo 账号的 provider_id=0（非库内 ApiKey 关联）；按「平台标签 + 身份」匹配同名 Provider 行，
            // 匹配到即视为可加入（策略里的 WorkBuddy 182****1319 等就是这类 Provider）
            const pid: number = aRaw.provider_id
              || providers.find((p: any) => p.name && (p.name === `${aRaw.platform_label || ''} ${aRaw.identity || ''}`.trim()
                || (aRaw.identity && p.name.endsWith(String(aRaw.identity)))))?.id || 0;
            const a = { ...aRaw, provider_id: pid };
            return (
            <div key={a.id} className='rounded-lg border bg-muted/30'>
              <button
                type='button'
                onClick={() => { setExpandedAcc(prev => (prev === pid ? null : pid)); if (pid) loadRuleModels(pid); }}
                disabled={!pid}
                title={pid ? '' : t('strategies.accountNoProvider')}
                className='flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-muted/50 disabled:opacity-50'
              >
                {expandedAcc === pid ? <ChevronDown className='h-3.5 w-3.5 flex-none text-muted-foreground' aria-hidden='true' /> : <ChevronRight className='h-3.5 w-3.5 flex-none text-muted-foreground' aria-hidden='true' />}
                <UserRound className='h-4 w-4 flex-none text-muted-foreground' aria-hidden='true' />
                <div className='min-w-0 flex-1'>
                  <p className='truncate text-sm font-medium'>{a.platform_label || a.platform || a.alias || a.identity}</p>
                  <p className='truncate text-xs text-muted-foreground'>{privacyMode ? `#${t('strategies.maskedName')}${accounts.findIndex((x: any) => x.id === a.id) + 1}` : (a.identity || a.alias || '')}</p>
                </div>
                <QuickAddButton pid={pid} disabled={!pid} />
              </button>
              {expandedAcc === pid && pid && (
                <div className='border-t px-1.5 py-1'>
                  {(ruleModelsCache[pid] || []).map((m: any) => <ModelRow key={m.id} pid={pid} pName={a.provider_name} m={m} />)}
                  {!ruleModelsCache[pid] && (
                    <div className='flex items-center gap-1.5 px-2 py-2 text-xs text-muted-foreground'>
                      <Loader2 className='h-3 w-3 animate-spin' aria-hidden='true' />{t('common.loading')}
                    </div>
                  )}
                </div>
              )}
            </div>
            );
          })}
        </TabsContent>

        <TabsContent value='free' className='mt-2 max-h-[calc(100vh-16rem)] space-y-1.5 overflow-y-auto pr-1'>
          {providers.some((p: any) => !ruleModelsCache[p.id]) && (
            <p className='px-2 py-1 text-[11px] text-muted-foreground'>{t('common.loading')}…</p>
          )}
          {freeRows.length === 0 && !providers.some((p: any) => !ruleModelsCache[p.id]) && (
            <p className='py-6 text-center text-xs text-muted-foreground'>-</p>
          )}
          <div className='rounded-lg border bg-muted/30'>
            {freeRows.map(({ pid, m }, idx) => <ModelRow key={`${pid}-${m.id}-${idx}`} pid={pid} pName='' m={m} />)}
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}
