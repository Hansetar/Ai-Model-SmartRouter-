import { useState, useEffect } from 'react';
import { createApi, translateError } from '../api';
import { useToast } from '../components/Toast';

interface TenantsPageProps {
  token: string;
}

interface TenantData {
  tenant_id: string;
  name: string;
  api_key: string;
  email: string;
  enabled: boolean;
  quota_daily_tokens: number;
  quota_daily_requests: number;
  allowed_models: string[];
  blocked_models: string[];
  model_filter_mode: string;
  balance_threshold_type: string;
  balance_threshold_value: number;
  balance_notify_enabled: boolean;
  [key: string]: unknown;
}

const emptyTenant: Partial<TenantData> = {
  tenant_id: '',
  name: '',
  api_key: '',
  email: '',
  enabled: true,
  quota_daily_tokens: 0,
  quota_daily_requests: 0,
  allowed_models: [],
  blocked_models: [],
  model_filter_mode: 'whitelist',
  balance_threshold_type: 'fixed',
  balance_threshold_value: 10,
  balance_notify_enabled: true,
};

export default function TenantsPage({ token }: TenantsPageProps) {
  const api = createApi(token);
  const toast = useToast();
  const [tenants, setTenants] = useState<TenantData[]>([]);
  const [allModels, setAllModels] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [form, setForm] = useState<Partial<TenantData>>({ ...emptyTenant });
  const [saving, setSaving] = useState(false);
  const [modelInput, setModelInput] = useState('');
  const [visibleKeys, setVisibleKeys] = useState<Set<string>>(new Set());
  const [fullKeys, setFullKeys] = useState<Map<string, string>>(new Map());

  useEffect(() => {
    loadTenants();
    loadModels();
  }, []);

  const loadTenants = async () => {
    setLoading(true);
    try {
      const result = await api.listTenants();
      setTenants(result.tenants || []);
    } catch (err) {
      const { message, detail } = translateError(err);
      toast.addToast(message, 'error', detail);
    } finally {
      setLoading(false);
    }
  };

  const loadModels = async () => {
    try {
      const result = await api.listModels();
      const models = (result.models || []).map((m: Record<string, unknown>) => String(m.name));
      setAllModels(models);
    } catch {
      // ignore
    }
  };

  const handleCreate = async () => {
    if (!form.tenant_id?.trim()) {
      toast.addToast('租户ID不能为空', 'error');
      return;
    }
    if (!form.name?.trim()) {
      toast.addToast('租户名称不能为空', 'error');
      return;
    }
    setSaving(true);
    try {
      const result = await api.createTenant(form as Record<string, unknown>);
      toast.addToast('租户创建成功', 'success', result.api_key ? `API Key: ${result.api_key}` : `已添加租户: ${form.tenant_id}`);
      setShowForm(false);
      setForm({ ...emptyTenant });
      await loadTenants();
    } catch (err) {
      const { message, detail } = translateError(err);
      toast.addToast(`新增租户失败: ${message}`, 'error', detail);
    } finally {
      setSaving(false);
    }
  };

  const handleUpdate = async () => {
    if (!editId) return;
    setSaving(true);
    try {
      await api.updateTenant(editId, form as Record<string, unknown>);
      toast.addToast('租户更新成功', 'success');
      setEditId(null);
      setForm({ ...emptyTenant });
      await loadTenants();
    } catch (err) {
      const { message, detail } = translateError(err);
      toast.addToast(`更新租户失败: ${message}`, 'error', detail);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm('确定删除此租户?')) return;
    try {
      await api.deleteTenant(id);
      toast.addToast('租户已删除', 'success');
      await loadTenants();
    } catch (err) {
      const { message, detail } = translateError(err);
      toast.addToast(`删除失败: ${message}`, 'error', detail);
    }
  };

  const handleResetApiKey = async (id: string) => {
    if (!confirm('确定重置此租户的API Key? 重置后旧Key将立即失效。')) return;
    try {
      const result = await api.resetTenantApiKey(id);
      toast.addToast('API Key已重置', 'success', `新Key: ${result.api_key}`);
      setFullKeys(prev => { const next = new Map(prev); next.set(id, result.api_key); return next; });
      await loadTenants();
    } catch (err) {
      const { message, detail } = translateError(err);
      toast.addToast(`重置Key失败: ${message}`, 'error', detail);
    }
  };

  const fetchFullKey = async (tenantId: string): Promise<string | null> => {
    if (fullKeys.has(tenantId)) return fullKeys.get(tenantId)!;
    try {
      const result = await api.getTenantApiKey(tenantId);
      const key = result.api_key || '';
      setFullKeys(prev => { const next = new Map(prev); next.set(tenantId, key); return next; });
      return key;
    } catch (err) {
      const { message } = translateError(err);
      toast.addToast(`获取API Key失败: ${message}`, 'error');
      return null;
    }
  };

  const handleToggleKeyVisibility = async (tenantId: string) => {
    const next = new Set(visibleKeys);
    if (next.has(tenantId)) {
      next.delete(tenantId);
    } else {
      await fetchFullKey(tenantId);
      next.add(tenantId);
    }
    setVisibleKeys(next);
  };

  const handleCopyKey = async (tenantId: string) => {
    const key = await fetchFullKey(tenantId);
    if (key) {
      await navigator.clipboard.writeText(key);
      toast.addToast('已复制API Key', 'success');
    }
  };

  const startEdit = (t: TenantData) => {
    setEditId(t.tenant_id);
    setForm({ ...t });
    setShowForm(true);
  };

  const addModel = (model: string, list: 'allowed_models' | 'blocked_models') => {
    const current = form[list] || [];
    if (model && !current.includes(model)) {
      setForm({ ...form, [list]: [...current, model] });
    }
    setModelInput('');
  };

  const removeModel = (model: string, list: 'allowed_models' | 'blocked_models') => {
    const current = form[list] || [];
    setForm({ ...form, [list]: current.filter((m) => m !== model) });
  };

  const activeModelList = form.model_filter_mode === 'blacklist' ? 'blocked_models' : 'allowed_models';

  if (loading) return <div className="text-gray-500">加载中...</div>;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold">租户管理</h2>
        <button
          onClick={() => { setShowForm(true); setEditId(null); setForm({ ...emptyTenant }); }}
          className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600 text-sm"
        >
          + 新增租户
        </button>
      </div>

      {showForm && (
        <div className="bg-white rounded-lg shadow p-4 mb-4">
          <h3 className="font-semibold mb-3">{editId ? '编辑租户' : '新增租户'}</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">租户ID</label>
              <input
                className="w-full border rounded px-2 py-1 text-sm"
                value={form.tenant_id || ''}
                onChange={(e) => setForm({ ...form, tenant_id: e.target.value })}
                disabled={!!editId}
                placeholder="唯一标识，如 tenant-001"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">名称</label>
              <input
                className="w-full border rounded px-2 py-1 text-sm"
                value={form.name || ''}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">API Key</label>
              <input
                type="password"
                className="w-full border rounded px-2 py-1 text-sm"
                value={form.api_key || ''}
                onChange={(e) => setForm({ ...form, api_key: e.target.value })}
                placeholder="留空自动生成"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">联系邮箱</label>
              <input
                type="email"
                className="w-full border rounded px-2 py-1 text-sm"
                value={form.email || ''}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
                placeholder="tenant@example.com"
              />
            </div>
            <div className="flex items-end">
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={form.enabled || false}
                  onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
                />
                启用
              </label>
            </div>
          </div>

          {/* Quota settings */}
          <div className="mt-4">
            <h4 className="text-sm font-medium mb-2">配额设置</h4>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label className="block text-xs text-gray-500 mb-1">每日Token上限 (0=无限)</label>
                <input
                  type="number"
                  className="w-full border rounded px-2 py-1 text-sm"
                  value={form.quota_daily_tokens || 0}
                  onChange={(e) => setForm({ ...form, quota_daily_tokens: Number(e.target.value) })}
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">每日请求上限 (0=无限)</label>
                <input
                  type="number"
                  className="w-full border rounded px-2 py-1 text-sm"
                  value={form.quota_daily_requests || 0}
                  onChange={(e) => setForm({ ...form, quota_daily_requests: Number(e.target.value) })}
                />
              </div>
            </div>
          </div>

          {/* Balance notification settings */}
          <div className="mt-4">
            <h4 className="text-sm font-medium mb-2">余额通知设置</h4>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <div className="flex items-end">
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={form.balance_notify_enabled !== false}
                    onChange={(e) => setForm({ ...form, balance_notify_enabled: e.target.checked })}
                  />
                  启用余额不足通知
                </label>
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">阈值类型</label>
                <select
                  className="w-full border rounded px-2 py-1 text-sm"
                  value={form.balance_threshold_type || 'fixed'}
                  onChange={(e) => setForm({ ...form, balance_threshold_type: e.target.value })}
                >
                  <option value="fixed">固定金额</option>
                  <option value="percentage">百分比</option>
                </select>
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">阈值 (金额或百分比)</label>
                <input
                  type="number"
                  className="w-full border rounded px-2 py-1 text-sm"
                  value={form.balance_threshold_value || 10}
                  onChange={(e) => setForm({ ...form, balance_threshold_value: Number(e.target.value) })}
                />
              </div>
            </div>
          </div>

          {/* Model filter settings */}
          <div className="mt-4">
            <h4 className="text-sm font-medium mb-2">可用模型设置</h4>
            <div className="mb-3">
              <label className="block text-xs text-gray-500 mb-1">过滤模式</label>
              <select
                className="w-full border rounded px-2 py-1 text-sm"
                value={form.model_filter_mode || 'whitelist'}
                onChange={(e) => setForm({ ...form, model_filter_mode: e.target.value })}
              >
                <option value="whitelist">白名单（仅允许选中的模型）</option>
                <option value="blacklist">黑名单（禁止选中的模型）</option>
              </select>
            </div>
            <div className="mb-2">
              <label className="block text-xs text-gray-500 mb-1">
                {form.model_filter_mode === 'blacklist' ? '禁止的模型' : '允许的模型'}
                {form.model_filter_mode === 'whitelist' ? '（空=全部允许）' : '（空=无禁止）'}
              </label>
              {/* Selected models display */}
              <div className="flex flex-wrap gap-1 mb-2">
                {(form[activeModelList] || []).map((model) => (
                  <span key={model} className="inline-flex items-center gap-1 px-2 py-0.5 bg-blue-100 text-blue-700 rounded text-xs">
                    {model}
                    <button
                      type="button"
                      onClick={() => removeModel(model, activeModelList)}
                      className="text-blue-400 hover:text-blue-600"
                    >
                      x
                    </button>
                  </span>
                ))}
              </div>
              {/* Model input with autocomplete */}
              <div className="flex gap-1">
                <input
                  className="flex-1 border rounded px-2 py-1 text-sm"
                  value={modelInput}
                  onChange={(e) => setModelInput(e.target.value)}
                  placeholder="输入模型名称或从下方选择"
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault();
                      addModel(modelInput.trim(), activeModelList);
                    }
                  }}
                />
                <button
                  type="button"
                  onClick={() => addModel(modelInput.trim(), activeModelList)}
                  className="px-3 py-1 bg-gray-200 rounded text-sm hover:bg-gray-300"
                >
                  添加
                </button>
              </div>
              {/* Quick select from existing models */}
              {allModels.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  <span className="text-xs text-gray-400">快速选择:</span>
                  {allModels
                    .filter((m) => !(form[activeModelList] || []).includes(m))
                    .map((m) => (
                      <button
                        key={m}
                        type="button"
                        onClick={() => addModel(m, activeModelList)}
                        className="px-2 py-0.5 bg-gray-100 text-gray-600 rounded text-xs hover:bg-gray-200"
                      >
                        + {m}
                      </button>
                    ))}
                </div>
              )}
            </div>
          </div>

          <div className="flex gap-2 mt-3">
            <button
              onClick={editId ? handleUpdate : handleCreate}
              disabled={saving}
              className={`px-4 py-2 rounded text-sm font-medium transition-colors ${
                saving ? 'bg-gray-400 text-white cursor-not-allowed' : 'bg-blue-500 text-white hover:bg-blue-600'
              }`}
            >
              {saving ? '保存中...' : '保存'}
            </button>
            <button
              onClick={() => { setShowForm(false); setEditId(null); }}
              disabled={saving}
              className="px-4 py-2 bg-gray-200 text-gray-700 rounded text-sm hover:bg-gray-300 disabled:opacity-50"
            >
              取消
            </button>
          </div>
        </div>
      )}

      <div className="bg-white rounded-lg shadow overflow-x-auto">
        <table className="w-full text-sm min-w-[600px]">
          <thead className="bg-gray-50 text-gray-600">
            <tr>
              <th className="px-4 py-3 text-left">ID</th>
              <th className="px-4 py-3 text-left">名称</th>
              <th className="px-4 py-3 text-left">API Key</th>
              <th className="px-4 py-3 text-left">模型过滤</th>
              <th className="px-4 py-3 text-left">状态</th>
              <th className="px-4 py-3 text-left">操作</th>
            </tr>
          </thead>
          <tbody>
            {tenants.map((t) => (
              <tr key={t.tenant_id} className="border-b hover:bg-gray-50">
                <td className="px-4 py-3 text-xs text-gray-500">{t.tenant_id}</td>
                <td className="px-4 py-3 font-medium">{t.name}</td>
                <td className="px-4 py-3 text-xs">
                  {t.api_key ? (
                    <div className="flex items-center gap-1">
                      <span className="font-mono">
                        {visibleKeys.has(t.tenant_id)
                          ? (fullKeys.get(t.tenant_id) || '加载中...')
                          : `${t.api_key.slice(0, 8)}***`}
                      </span>
                      <button
                        onClick={() => handleToggleKeyVisibility(t.tenant_id)}
                        className="text-gray-400 hover:text-gray-600 p-0.5"
                        title={visibleKeys.has(t.tenant_id) ? '隐藏' : '显示'}
                      >
                        {visibleKeys.has(t.tenant_id) ? (
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.478 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 5.411m0 0L21 21" /></svg>
                        ) : (
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" /></svg>
                        )}
                      </button>
                      <button
                        onClick={() => handleCopyKey(t.tenant_id)}
                        className="text-gray-400 hover:text-gray-600 p-0.5"
                        title="复制"
                      >
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" /></svg>
                      </button>
                    </div>
                  ) : (
                    <span className="text-gray-400">未设置</span>
                  )}
                </td>
                <td className="px-4 py-3 text-xs">
                  {t.model_filter_mode === 'blacklist' ? (
                    <span className="text-orange-600">
                      黑名单({(t.blocked_models || []).length}个)
                    </span>
                  ) : (
                    <span className="text-blue-600">
                      白名单({(t.allowed_models || []).length === 0 ? '全部' : `${(t.allowed_models || []).length}个`})
                    </span>
                  )}
                </td>
                <td className="px-4 py-3">
                  <span className={`px-2 py-0.5 rounded text-xs ${t.enabled ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
                    {t.enabled ? '启用' : '禁用'}
                  </span>
                </td>
                <td className="px-4 py-3 space-x-2">
                  <button onClick={() => startEdit(t)} className="text-blue-500 hover:underline text-xs">编辑</button>
                  <button onClick={() => handleResetApiKey(t.tenant_id)} className="text-orange-500 hover:underline text-xs">重置Key</button>
                  <button onClick={() => handleDelete(t.tenant_id)} className="text-red-500 hover:underline text-xs">删除</button>
                </td>
              </tr>
            ))}
            {tenants.length === 0 && (
              <tr><td colSpan={6} className="px-4 py-6 text-center text-gray-400">暂无租户</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
