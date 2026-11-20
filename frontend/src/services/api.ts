import axios from 'axios';

const api = axios.create({
  baseURL: '',
  // 防止慢/失联请求无限挂起、堆积占用连接（流式聊天走原生 fetch，不受此限制）。
  timeout: 30_000,
  headers: { 'Content-Type': 'application/json' },
});

// Providers
export const getHotProviders = () => api.get('/api/providers/hot-providers');
export const getOnlineProviders = () => api.get('/api/providers/online-providers');
export const refreshOnlineProviders = () => api.post('/api/providers/online-providers/refresh');
export const getFreeModels = (force = false) => api.get('/api/providers/free-models', { params: force ? { force: true } : undefined });
export const joinFreeChannel = (channelId: string, extra?: { gateway_base_url?: string; gateway_key?: string }) =>
  api.post('/api/providers/free-models/join', { channel_id: channelId, ...(extra || {}) });
// Cloud meta: software announcements & sponsor seats (fetched by backend from cloud auth server)
export const getCloudMeta = (force = false) => api.get('/api/cloud/meta', { params: force ? { force: true } : undefined });
export const getClientKeys = () => api.get('/api/client-keys');
export const createClientKey = (data: any) => api.post('/api/client-keys', data);
export const updateClientKey = (id: number, data: any) => api.patch('/api/client-keys/' + id, data);
export const deleteClientKey = (id: number) => api.delete('/api/client-keys/' + id);
export const getEndpointsInfo = () => api.get('/api/endpoints');
export const getAuthStatus = () => api.get('/api/auth/status');
export const saveAuthCredentials = (data: any) => api.post('/api/auth/credentials', data);
export const getOauthProfiles = () => api.get('/api/oauth/profiles');
export const startOauthLogin = (data: any) => api.post('/api/oauth/start', data);
export const getOauthStatus = (sessionId: string) => api.get('/api/oauth/status/' + sessionId);
export const getCliPlatforms = () => api.get('/api/cli/platforms');
export const cliDetect = (data: any) => api.post('/api/cli/detect', data);
export const cliImport = (data: any) => api.post('/api/cli/import', data);
// Qoder CLI 凭证导入走 EchoBird 本地存储的专用路由（非 api_keys）
export const importQoderCli = (path: string, platform: string) =>
  api.post('/api/accounts/qoder/cli/import', { path, platform });
export const cliBrowserStart = (data: any) => api.post('/api/cli/browser-start', data);
export const cliStatus = (sessionId: string) => api.get('/api/cli/status/' + sessionId);
export const getProviders = () => api.get('/api/providers');
export const createProvider = (data: any) => api.post('/api/providers', data);
export const updateProvider = (id: number, data: any) => api.put('/api/providers/' + id, data);
export const deleteProvider = (id: number) => api.delete('/api/providers/' + id);

// API Keys
export const getKeys = (providerId: number) => api.get('/api/providers/' + providerId + '/keys');
export const createKey = (data: any) => api.post('/api/api-keys', data);
export const createKeysBatch = (providerId: number, data: any) => api.post('/api/providers/' + providerId + '/keys/batch', data);
export const updateKey = (id: number, data: any) => api.put('/api/api-keys/' + id, data);
export const deleteKey = (id: number) => api.delete('/api/api-keys/' + id);
export const testKey = (id: number) => api.post('/api/api-keys/' + id + '/test');
export const testAllKeys = (providerId: number) => api.post('/api/providers/' + providerId + '/keys/test-all');
export const deleteInvalidKeys = (providerId: number) => api.delete('/api/providers/' + providerId + '/keys/invalid');
export const recoverKeys = (providerId: number) => api.post('/api/providers/' + providerId + '/keys/recover');

// Models
export const getModels = (providerId: number) => api.get('/api/providers/' + providerId + '/models');
export const createModel = (data: any) => api.post('/api/models', data);
export const updateModel = (id: number, data: any) => api.put('/api/models/' + id, data);
export const deleteModel = (id: number) => api.delete('/api/models/' + id);
export const fetchModels = (providerId: number) => api.post('/api/models/fetch/' + providerId);
export const syncModels = (providerId: number) => api.post('/api/models/sync/' + providerId);
export const testModel = (id: number) => api.post('/api/models/' + id + '/test');
export const testAllModels = (providerId: number) => api.post('/api/providers/' + providerId + '/models/test-all');
export const deleteInvalidModels = (providerId: number) => api.delete('/api/providers/' + providerId + '/models/invalid');
export const clearModels = (providerId: number) => api.delete('/api/providers/' + providerId + '/models');

// Strategies
export const getStrategies = () => api.get('/api/strategies');
export const createStrategy = (data: any) => api.post('/api/strategies', data);
export const updateStrategy = (id: number, data: any) => api.put('/api/strategies/' + id, data);
export const deleteStrategy = (id: number) => api.delete('/api/strategies/' + id);
export const addRule = (strategyId: number, data: any) => api.post('/api/strategies/' + strategyId + '/rules', data);
export const updateRule = (ruleId: number, data: any) => api.put('/api/strategies/rules/' + ruleId, data);
export const deleteRule = (ruleId: number) => api.delete('/api/strategies/rules/' + ruleId);
export const testStrategy = (id: number) => api.post('/api/strategies/' + id + '/test');
export const testStrategyRules = (id: number) => api.post('/api/strategies/' + id + '/test-rules');
export const testStrategyRule = (strategyId: number, ruleId: number) => api.post('/api/strategies/' + strategyId + '/rules/' + ruleId + '/test');

// Logs
export const getLogs = (params: any) => api.get('/api/logs', { params });

// Dashboard
export const getDashboardStats = () => api.get('/api/dashboard/stats');
export const clearTodayStats = () => api.post('/api/dashboard/clear-today');

// Health
export const getHealth = () => api.get('/api/health');

// Monitor (可视化监控)
export const getMonitorRecent = () => api.get('/api/monitor/recent');
export const monitorStreamUrl = () => '/api/monitor/stream';

// Icons
export const searchIcons = (keyword: string, page?: number, engine?: string) => api.post('/api/icons/search', { keyword, page: page || 0, count: 9, engine: engine || 'baidu' });
export const saveIcon = (url: string, providerId: number) => api.post('/api/icons/save', { url, provider_id: providerId });

// Backup/Restore
export const getBackupConfig = () => api.get('/api/backup/config');
export const updateBackupConfig = (data: any) => api.put('/api/backup/config', data);
export const createBackup = () => api.post('/api/backup/create');
export const listBackups = () => api.get('/api/backup/list');
export const restoreLocalBackup = (filename: string) => api.post('/api/backup/restore-local?filename=' + encodeURIComponent(filename));
export const deleteBackup = (filename: string) => api.delete('/api/backup/' + encodeURIComponent(filename));
export const downloadBackupUrl = (filename: string) => '/api/backup/download/' + encodeURIComponent(filename);

// App Settings
export const getVersion = () => api.get("/api/version");
export const getSettings = () => api.get("/api/settings");
export const updateSettings = (data: any) => api.put("/api/settings", data);
export const getLanIp = () => api.get("/api/settings/lan-ip");
export const getRepositoryStatus = () => api.get("/api/settings/repository");
export const updateRepository = () => api.post("/api/settings/repository/update");
export const getAutoStart = () => api.get("/api/settings/auto-start");
export const setAutoStart = (enabled: boolean) => api.post("/api/settings/auto-start", { enabled });
export const restartPluginService = (pluginId: string) => api.post("/api/script-plugins/" + encodeURIComponent(pluginId) + "/service-restart");

// Project self-update (GitHub Releases / git)
export const checkAppUpdate = (fetch = true) => api.get(`/api/update/check?fetch=${fetch ? 1 : 0}`);
export const applyAppUpdate = (asset?: string) => api.post("/api/update/apply", asset ? { asset } : {});
export const getUpdateStatus = () => api.get("/api/update/status");

// Account Center (账号接入, EchoBird-style)
export const getAccountPlatforms = () => api.get('/api/accounts/platforms');
export const getAccounts = () => api.get('/api/accounts');
export const startAccountLogin = (platform: string) => api.post('/api/accounts/' + platform + '/login');
export const refreshAccount = (keyId: number) => api.post('/api/accounts/' + keyId + '/refresh');
export const claimAccount = (keyId: number) => api.post('/api/accounts/' + keyId + '/claim');
export const syncModelGroups = () => api.post('/api/accounts/sync-model-groups');
export const claimAllAccounts = (platform?: string) => api.post('/api/accounts/claim-all', platform ? { platform } : {});
// ZCode 本地客户端签到（按账号隔离 profile；不传 keyId 时平台级兼容）
export const getZcodeClaimStatus = () => api.get('/api/accounts/zcode/status');
export const claimZcode = (keyId?: number) => api.post('/api/accounts/zcode/claim', keyId != null ? { key_id: keyId } : {});
// Plugin-backed account operations (echobird-plugin)
export const getPluginLoginStatus = (loginId: string) => api.get('/api/accounts/plugin-login/' + loginId);
export const cancelPluginLogin = (loginId: string) => api.post('/api/accounts/plugin-login/' + loginId + '/cancel');
export const refreshPluginAccount = (application: string, accountId: string) => api.post('/api/accounts/plugin/' + application + '/' + accountId + '/refresh');
export const claimPluginAccount = (application: string, accountId: string) => api.post('/api/accounts/plugin/' + application + '/' + accountId + '/claim');
export const deletePluginAccount = (application: string, accountId: string) => api.delete('/api/accounts/plugin/' + application + '/' + accountId);
// EchoBird-native platform account operations (DeepSeek Harness / Kilo / Qoder / QwenWork)
export const getEchoLoginStatus = (loginId: string) => api.get('/api/accounts/echo-login/' + loginId);
export const cancelEchoLogin = (loginId: string) => api.post('/api/accounts/echo-login/' + loginId + '/cancel');
export const refreshEchoAccount = (platform: string, accountId: string) => api.post('/api/accounts/echo/' + platform + '/' + accountId + '/refresh');
export const claimEchoAccount = (platform: string, accountId: string) => api.post('/api/accounts/echo/' + platform + '/' + accountId + '/claim');
export const deleteEchoAccount = (platform: string, accountId: string) => api.delete('/api/accounts/echo/' + platform + '/' + accountId);

// Plugin Hub (echobird-plugin 管理与同步)
export const getPlugins = () => api.get('/api/plugins');
export const checkPluginUpdates = () => api.post('/api/plugins/check');
export const installPlugin = (pluginId: string) => api.post('/api/plugins/' + pluginId + '/install');
export const getPluginTask = () => api.get('/api/plugins/task');
export const enablePlugin = (pluginId: string) => api.post('/api/plugins/' + pluginId + '/enable');
export const disablePlugin = (pluginId: string) => api.post('/api/plugins/' + pluginId + '/disable');
export const uninstallPlugin = (pluginId: string) => api.delete('/api/plugins/' + pluginId);
export const updatePluginSettings = (data: any) => api.put('/api/plugins/settings', data);

// Script Plugins (独立脚本插件：COS 分发 / 权限 / 执行)
export const getScriptPlugins = () => api.get('/api/script-plugins');
export const checkScriptCatalog = () => api.post('/api/script-plugins/check');
export const installScriptPlugin = (data: { plugin_id: string; download_url?: string; sha256?: string }) =>
  api.post('/api/script-plugins/install', data);
export const getScriptPluginTask = () => api.get('/api/script-plugins/task');
export const enableScriptPlugin = (pluginId: string) => api.post('/api/script-plugins/' + pluginId + '/enable');
export const disableScriptPlugin = (pluginId: string) => api.post('/api/script-plugins/' + pluginId + '/disable');
export const uninstallScriptPlugin = (pluginId: string) => api.delete('/api/script-plugins/' + pluginId);
export const getScriptPluginSettings = (pluginId: string) => api.get('/api/script-plugins/' + pluginId + '/settings');
export const saveScriptPluginSettings = (pluginId: string, values: Record<string, any>) =>
  api.put('/api/script-plugins/' + pluginId + '/settings', { values });
export const runScriptPlugin = (pluginId: string, params?: Record<string, any>) =>
  api.post('/api/script-plugins/' + pluginId + '/run', { params: params || {} });
export const getScriptPluginAudit = (limit = 100) => api.get('/api/script-plugins/audit?limit=' + limit);

// 项目内已登录账号 + 多账号批量执行（后台任务）
export const getWorkbuddyAccounts = () => api.get('/api/script-plugins/workbuddy-accounts');
export const getPluginAccounts = (pluginId: string) => api.get('/api/script-plugins/plugin-accounts?plugin_id=' + encodeURIComponent(pluginId));
export const runScriptPluginBatch = (pluginId: string, data: { account_ids?: string[]; all_accounts?: boolean; params?: Record<string, any> }) =>
  api.post('/api/script-plugins/' + pluginId + '/run-batch', data);
export const getScriptRunTask = (taskId: string) => api.get('/api/script-plugins/run-task/' + taskId);

// 脚本插件定时任务（设置 / 列表 / 执行日志）
export const getScheduleTasks = () => api.get('/api/scheduled-tasks');
export const createScheduleTask = (data: any) => api.post('/api/scheduled-tasks', data);
export const updateScheduleTask = (id: number, data: any) => api.put('/api/scheduled-tasks/' + id, data);
export const deleteScheduleTask = (id: number) => api.delete('/api/scheduled-tasks/' + id);
export const toggleScheduleTask = (id: number) => api.post('/api/scheduled-tasks/' + id + '/toggle');
export const runScheduleTaskNow = (id: number, data?: any) => api.post('/api/scheduled-tasks/' + id + '/run', data || {});
export const getScheduleRuns = (taskId?: number | null, limit = 100) =>
  api.get('/api/scheduled-tasks/runs?limit=' + limit + (taskId ? '&task_id=' + taskId : ''));
export const getScheduleStats = () => api.get('/api/scheduled-tasks/stats');

// Conversations
export const getConversations = () => api.get('/api/conversations');
export const createConversation = (data: any) => api.post('/api/conversations', data);
export const updateConversation = (id: number, data: any) => api.put('/api/conversations/' + id, data);
export const deleteConversation = (id: number) => api.delete('/api/conversations/' + id);


// Image Generation
export const generateImage = (data: any) => api.post('/v1/images/generations', data);

// TTS (Text-to-Speech)
export const generateSpeech = async (data: any): Promise<Blob> => {
  const resp = await fetch('/v1/audio/speech', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ error: { message: resp.statusText } }));
    throw new Error(err.error?.detail || err.error?.message || 'TTS request failed');
  }
  return resp.blob();
};

// Video Generation
export const createVideo = (data: any) => api.post('/v1/videos', data);
export const getVideoTask = (taskId: string, providerId: number) =>
  api.get('/v1/videos/' + taskId + '?_provider_id=' + providerId);
export default api;
