import axios from 'axios';

const API_BASE = '/admin/api';

// Error message translation map
const ERROR_TRANSLATIONS: Record<string, string> = {
  'Invalid provider config': '供应商配置无效',
  'already exists': '已存在',
  'not found': '未找到',
  'Failed to save config': '配置保存失败',
  'Missing token': '未登录或登录已过期',
  'Invalid or expired token': '登录已过期，请重新登录',
  'Wrong password': '密码错误',
  'Network Error': '网络错误，请检查连接',
};

export function translateError(error: unknown): { message: string; detail: string } {
  if (axios.isAxiosError(error)) {
    const status = error.response?.status;
    const detail = error.response?.data?.detail || error.message || '';

    if (status === 401) {
      return { message: '认证失败', detail: '登录已过期，请重新登录' };
    }
    if (status === 403) {
      return { message: '权限不足', detail: detail };
    }
    if (status === 404) {
      return { message: '资源未找到', detail: detail };
    }
    if (status === 400) {
      // Try to translate the detail
      let translated = detail;
      for (const [en, zh] of Object.entries(ERROR_TRANSLATIONS)) {
        if (detail.includes(en)) {
          translated = detail.replace(en, zh);
          break;
        }
      }
      return { message: '请求参数错误', detail: translated };
    }
    if (status && status >= 500) {
      return { message: '服务器错误', detail: detail };
    }

    return { message: '请求失败', detail: detail };
  }

  if (error instanceof Error) {
    return { message: '操作失败', detail: error.message };
  }

  return { message: '未知错误', detail: String(error) };
}

export function createApi(token: string) {
  const instance = axios.create({
    baseURL: API_BASE,
    headers: { Authorization: `Bearer ${token}` },
    timeout: 30000, // 30 second timeout to prevent hanging
  });

  // Request deduplication: prevent duplicate concurrent requests
  const pendingRequests = new Map<string, Promise<unknown>>();
  const dedupRequest = <T>(key: string, fn: () => Promise<T>): Promise<T> => {
    if (pendingRequests.has(key)) {
      return pendingRequests.get(key) as Promise<T>;
    }
    const promise = fn().finally(() => pendingRequests.delete(key));
    pendingRequests.set(key, promise as Promise<unknown>);
    return promise;
  };

  return {
    // Auth
    login: (password: string) =>
      axios.post(`${API_BASE}/login`, { password }).then((r) => r.data),

    // Dashboard
    getDashboard: (period: string) =>
      dedupRequest(`dashboard_${period}`, () =>
        instance.get(`/dashboard?period=${period}`).then((r) => r.data)),

    // Models
    getModels: () => instance.get('/models').then((r) => r.data),
    listModels: () => instance.get('/models').then((r) => r.data),
    createModel: (model: Record<string, unknown>) =>
      instance.post('/models', model).then((r) => r.data),
    cloneModel: (name: string, newName: string) =>
      instance.post(`/models/${encodeURIComponent(name)}/clone`, { new_name: newName }).then((r) => r.data),
    updateModel: (name: string, model: Record<string, unknown>) =>
      instance.put(`/models/${encodeURIComponent(name)}/config`, { model }).then((r) => r.data),
    deleteModel: (name: string) =>
      instance.delete(`/models/${encodeURIComponent(name)}`).then((r) => r.data),

    // Providers
    listProviders: () => instance.get('/providers').then((r) => r.data),
    createProvider: (provider: Record<string, unknown>) =>
      instance.post('/providers', provider).then((r) => r.data),
    updateProvider: (name: string, provider: Record<string, unknown>) =>
      instance.put(`/providers/${encodeURIComponent(name)}`, provider).then((r) => r.data),
    deleteProvider: (name: string, data?: Record<string, unknown>) =>
      instance.delete(`/providers/${encodeURIComponent(name)}`, data ? { data } : undefined).then((r) => r.data),
    cloneProvider: (name: string, data: Record<string, unknown>) =>
      instance.post(`/providers/${encodeURIComponent(name)}/clone`, data).then((r) => r.data),
    batchDeleteProviders: (names: string[]) =>
      instance.post('/providers/batch-delete', { names }).then((r) => r.data),
    fetchProviderModels: (name: string) =>
      instance.post(`/providers/${encodeURIComponent(name)}/fetch-models`).then((r) => r.data),

    // Tenants
    listTenants: () => instance.get('/tenants').then((r) => r.data),
    createTenant: (tenant: Record<string, unknown>) =>
      instance.post('/tenants', tenant).then((r) => r.data),
    updateTenant: (id: string, tenant: Record<string, unknown>) =>
      instance.put(`/tenants/${encodeURIComponent(id)}`, tenant).then((r) => r.data),
    deleteTenant: (id: string) =>
      instance.delete(`/tenants/${encodeURIComponent(id)}`).then((r) => r.data),
    resetTenantApiKey: (tenantId: string) =>
      instance.post(`/tenants/${encodeURIComponent(tenantId)}/reset-api-key`).then((r) => r.data),
    getTenantApiKey: (tenantId: string) =>
      instance.get(`/tenants/${encodeURIComponent(tenantId)}/api-key`).then((r) => r.data),

    // Config
    getConfig: () => instance.get('/config').then((r) => r.data),
    getConfigInfo: () => instance.get('/config/info').then((r) => r.data),
    reloadConfig: () => instance.post('/config/reload').then((r) => r.data),
    updateBasicSettings: (settings: Record<string, unknown>) =>
      instance.put('/config/basic', settings).then((r) => r.data),
    updateRouteWeights: (weights: Record<string, unknown>) =>
      instance.put('/config/route-weights', weights).then((r) => r.data),
    updateRLConfig: (config: Record<string, unknown>) =>
      instance.put('/config/rl', config).then((r) => r.data),
    updateHealthCheckConfig: (config: Record<string, unknown>) =>
      instance.put('/config/health-check', config).then((r) => r.data),
    updateStorageConfig: (config: Record<string, unknown>) =>
      instance.put('/config/storage', config).then((r) => r.data),
    updateWebConfig: (config: Record<string, unknown>) =>
      instance.put('/config/web', config).then((r) => r.data),
    updateExchangeRates: (rates: Record<string, unknown>) =>
      instance.put('/config/exchange-rates', { exchange_rates: rates }).then((r) => r.data),
    updateModelAliases: (aliases: Record<string, unknown>) =>
      instance.put('/config/model-aliases', aliases).then((r) => r.data),
    updateDifficultyRanges: (ranges: unknown[]) =>
      instance.post('/config/difficulty-ranges', { difficulty_ranges: ranges }).then((r) => r.data),

    // Notifications
    updateNotifications: (config: Record<string, unknown>) =>
      instance.put('/config/notifications', config).then((r) => r.data),
    getNotifications: () =>
      instance.get('/config/notifications').then((r) => r.data),
    testNotification: (data: Record<string, unknown>) =>
      instance.post('/config/notifications/test', data).then((r) => r.data),
    testAllNotifications: () =>
      instance.post('/config/notifications/test-all').then((r) => r.data),
    getBalanceTemplates: () =>
      instance.get('/balance/templates').then((r) => r.data),
    deductBalance: (data: Record<string, unknown>) =>
      instance.post('/balance/deduct', data).then((r) => r.data),
    syncProviderBalance: (providerName: string) =>
      instance.post(`/balance/sync/${encodeURIComponent(providerName)}`).then((r) => r.data),
    syncModelBalance: (modelName: string) =>
      instance.post(`/balance/sync-model/${encodeURIComponent(modelName)}`).then((r) => r.data),
    syncAllBalances: () =>
      instance.post('/balance/sync-all').then((r) => r.data),

    // Schedule & Holidays
    getHolidays: () =>
      instance.get('/holidays').then((r) => r.data),
    updateHolidays: (data: Record<string, unknown>) =>
      instance.put('/holidays', data).then((r) => r.data),
    updateModelSchedule: (modelName: string, data: Record<string, unknown>) =>
      instance.put(`/models/${encodeURIComponent(modelName)}/schedule`, data).then((r) => r.data),
    getModelScheduleStatus: (modelName: string) =>
      instance.get(`/models/${encodeURIComponent(modelName)}/schedule-status`).then((r) => r.data),

    // Tag feedback
    submitTagFeedback: (data: Record<string, unknown>) =>
      instance.post('/tag-feedback', data).then((r) => r.data),
    listTags: () =>
      instance.get('/tags/list').then((r) => r.data),

    // Payment (UNTESTED - interface stub)
    createPaymentOrder: (data: Record<string, unknown>) =>
      instance.post('/payment/create-order', data).then((r) => r.data),
    paymentCallback: (data: Record<string, unknown>) =>
      instance.post('/payment/callback', data).then((r) => r.data),
    listPaymentOrders: (params?: Record<string, unknown>) =>
      instance.get('/payment/orders', { params }).then((r) => r.data),
    createRefund: (data: Record<string, unknown>) =>
      instance.post('/payment/refund', data).then((r) => r.data),

    // RBAC
    loginGuest: () =>
      instance.post('/login/guest').then((r) => r.data),

    // Per-model balance & priority
    getModelBalances: () =>
      instance.get('/balance/model-balances').then((r) => r.data),
    getBalancePriority: () =>
      instance.get('/balance/priority').then((r) => r.data),
    updateBalancePriority: (data: Record<string, unknown>) =>
      instance.put('/balance/priority', data).then((r) => r.data),

    // Super admin setup & user management
    getSetupStatus: () =>
      instance.get('/setup/status').then((r) => r.data),
    initSetup: (data: Record<string, unknown>) =>
      instance.post('/setup/init', data).then((r) => r.data),
    resetSetup: () =>
      instance.post('/setup/reset').then((r) => r.data),
    loginUserAuth: (data: Record<string, unknown>) =>
      instance.post('/login/user-auth', data).then((r) => r.data),
    getUsers: () =>
      instance.get('/users').then((r) => r.data),
    createUser: (data: Record<string, unknown>) =>
      instance.post('/users', data).then((r) => r.data),
    updateUser: (username: string, data: Record<string, unknown>) =>
      instance.put(`/users/${encodeURIComponent(username)}`, data).then((r) => r.data),
    deleteUser: (username: string) =>
      instance.delete(`/users/${encodeURIComponent(username)}`).then((r) => r.data),
    transferSuperadmin: (data: Record<string, unknown>) =>
      instance.post('/users/transfer-superadmin', data).then((r) => r.data),
    getPermissions: () =>
      instance.get('/config/permissions').then((r) => r.data),
    updatePermissions: (data: Record<string, unknown>) =>
      instance.put('/config/permissions', data).then((r) => r.data),
    getApiKeyFormat: () =>
      instance.get('/config/api-key-format').then((r) => r.data),
    updateApiKeyFormat: (data: Record<string, unknown>) =>
      instance.put('/config/api-key-format', data).then((r) => r.data),
    generateApiKeySample: (data?: Record<string, unknown>) =>
      instance.post('/config/api-key-format/generate', data || {}).then((r) => r.data),
    getPriceTemplates: () =>
      instance.get('/price/templates').then((r) => r.data),

    // ML Models
    listMlModels: () => instance.get('/ml-models').then((r) => r.data),
    deleteMlModel: (name: string) =>
      instance.delete(`/ml-models/${encodeURIComponent(name)}`).then((r) => r.data),
    rebuildMlModels: () => instance.post('/ml-models/rebuild').then((r) => r.data),

    // User profiles
    getUserProfiles: () => instance.get('/users/profile').then((r) => r.data),
    updateUserProfile: (username: string, profile: Record<string, unknown>) =>
      instance.put(`/users/profile/${encodeURIComponent(username)}`, profile).then((r) => r.data),

    // Tenant balance
    getTenantBalances: () => instance.get('/tenants/balance').then((r) => r.data),
    updateTenantBalance: (tenantId: string, config: Record<string, unknown>) =>
      instance.put(`/tenants/balance/${encodeURIComponent(tenantId)}`, config).then((r) => r.data),

    // Training Samples
    listTrainingSamples: (params?: Record<string, unknown>) =>
      instance.get('/training-samples', { params }).then((r) => r.data),
    createTrainingSample: (sample: Record<string, unknown>) =>
      instance.post('/training-samples', sample).then((r) => r.data),
    updateTrainingSample: (id: string, sample: Record<string, unknown>) =>
      instance.put(`/training-samples/${encodeURIComponent(id)}`, sample).then((r) => r.data),
    deleteTrainingSample: (id: string) =>
      instance.delete(`/training-samples/${encodeURIComponent(id)}`).then((r) => r.data),
    batchDeleteTrainingSamples: (ids: number[]) =>
      instance.post('/training-samples/batch-delete', { ids }).then((r) => r.data),
    batchUpdateTrainingSamples: (ids: number[], updates: Record<string, unknown>) =>
      instance.post('/training-samples/batch-update', { ids, updates }).then((r) => r.data),

    // Tuning
    getTuningStatus: () => instance.get('/tuning/status').then((r) => r.data),
    triggerRetrain: () => instance.post('/tuning/retrain').then((r) => r.data),
    getRetrainStatus: (taskId: string) =>
      instance.get(`/tuning/retrain/${encodeURIComponent(taskId)}`).then((r) => r.data),
    updateRLParams: (params: Record<string, unknown>) =>
      instance.put('/tuning/rl-params', params).then((r) => r.data),
    toggleAutoTune: (enabled: boolean) =>
      instance.put('/tuning/auto-tune', { enabled }).then((r) => r.data),
    resetModels: () => instance.post('/tuning/reset').then((r) => r.data),

    // Request Logs
    listRequestLogs: (params?: Record<string, unknown>) =>
      instance.get('/request-logs', { params }).then((r) => r.data),

    // Feedback
    listFeedback: (params?: Record<string, unknown>) =>
      instance.get('/feedback', { params }).then((r) => r.data),
    getFeedbackStats: () => instance.get('/feedback/stats').then((r) => r.data),

    // Health
    getModelsHealth: () => instance.get('/health/models').then((r) => r.data),

    // Routing
    getRoutingStatus: () => instance.get('/routing/status').then((r) => r.data),

    // Database
    resetDatabase: () => instance.post('/database/reset').then((r) => r.data),
    checkDatabase: () => instance.get('/database/check').then((r) => r.data),
    repairDatabase: () => instance.post('/database/repair').then((r) => r.data),

    // Exchange Rates
    fetchExchangeRates: () =>
      instance.get('/exchange-rates/fetch').then((r) => r.data),
    syncExchangeRates: (manualOverrides?: Record<string, number>) =>
      instance.post('/exchange-rates/sync', { manual_overrides: manualOverrides }).then((r) => r.data),

    // Models Batch
    batchModelOperation: (operation: string, modelNames: string[], extra?: Record<string, unknown>) =>
      instance.post('/models/batch', { operation, model_names: modelNames, ...extra }).then((r) => r.data),
    importModels: (models: Record<string, unknown>[]) =>
      instance.post('/models/import', { models }).then((r) => r.data),

    // Balance
    getBalance: () => instance.get('/balance').then((r) => r.data),

    // Modality Detection
    detectModalities: (modelNames: string[], save = true, methods?: string[]) =>
      instance.post('/models/detect-modalities', { model_names: modelNames, save, methods }).then((r) => r.data),
    confirmModalities: (modelNames: string[], discard = false) =>
      instance.post('/models/confirm-modalities', { model_names: modelNames, discard }).then((r) => r.data),
    applyPendingModalities: () =>
      instance.post('/models/apply-pending-modalities').then((r) => r.data),

    // Tenant Usage
    getTenantUsage: (params?: Record<string, unknown>) =>
      instance.get('/tenants/usage', { params }).then((r) => r.data),
  };
}

export type Api = ReturnType<typeof createApi>;
