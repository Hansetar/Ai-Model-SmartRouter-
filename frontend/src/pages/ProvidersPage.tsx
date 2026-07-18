import { useState, useEffect } from 'react';
import { createApi, translateError } from '../api';
import { useToast } from '../components/Toast';

interface ProvidersPageProps {
  token: string;
}

interface ProviderData {
  name: string;
  display_name: string;
  base_url: string;
  api_key: string;
  api_type: string;
  enabled: boolean;
  balance_script: string;
  balance_manual: number | null;
  balance_currency: string;
  balance_source: string;
  balance_deduction_mode: string;
  provider_type: string;
  price_script: string;
  [key: string]: unknown;
}

interface BoundModel {
  name: string;
  provider: string;
}

interface DeleteBindingState {
  providerName: string;
  boundModels: BoundModel[];
  transferTo: string;
}

const emptyProvider: ProviderData = {
  name: '',
  display_name: '',
  base_url: '',
  api_key: '',
  api_type: 'openai',
  enabled: true,
  balance_script: '',
  balance_manual: null,
  balance_currency: 'CNY',
  balance_source: 'auto',
  balance_deduction_mode: 'realtime',
  provider_type: 'openai',
  price_script: '',
};

// 预置余额查询模板
const BALANCE_TEMPLATES: Record<string, { label: string; script: string }> = {
  deepseek: {
    label: 'DeepSeek',
    script: `import httpx
resp = httpx.get("https://api.deepseek.com/user/balance",
                 headers={"Authorization": f"Bearer {api_key}"}, timeout=10)
if resp.status_code == 200:
    data = resp.json()
    balance_infos = data.get("balance_infos", [])
    total = sum(float(b.get("total_balance", 0)) for b in balance_infos)
    result = {"balance": total, "balance_currency": "CNY"}
else:
    result = None`,
  },
  siliconflow: {
    label: 'SiliconFlow',
    script: `import httpx
resp = httpx.get("https://api.siliconflow.cn/v1/user/info",
                 headers={"Authorization": f"Bearer {api_key}"}, timeout=10)
if resp.status_code == 200:
    data = resp.json()
    result = {"balance": float(data.get("totalBalance", 0)), "balance_currency": "CNY"}
else:
    result = None`,
  },
  zhipu: {
    label: '智谱 GLM',
    script: `# 智谱暂无余额查询API，需手动设置余额
# 可通过 balance_manual 字段手动设定
result = None`,
  },
  openai: {
    label: 'OpenAI',
    script: `# OpenAI 余额需通过 Dashboard 查看，暂无公开API
# 可通过 balance_manual 字段手动设定
result = None`,
  },
  custom: {
    label: '自定义脚本',
    script: `# 余额获取脚本
# 可用变量: api_key, base_url, model_name
# 返回: result = 余额数值 或 result = {"balance": 数值, "balance_currency": "CNY"}
import httpx
resp = httpx.get(base_url + "/user/balance",
                 headers={"Authorization": f"Bearer {api_key}"}, timeout=10)
if resp.status_code == 200:
    data = resp.json()
    result = float(data.get("total_balance", 0))
else:
    result = None`,
  },
};

export default function ProvidersPage({ token }: ProvidersPageProps) {
  const api = createApi(token);
  const toast = useToast();
  const [providers, setProviders] = useState<ProviderData[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editName, setEditName] = useState<string | null>(null);
  const [form, setForm] = useState<ProviderData>({ ...emptyProvider });
  const [saving, setSaving] = useState(false);
  const [formErrors, setFormErrors] = useState<Record<string, string>>({});
  const [deleteBinding, setDeleteBinding] = useState<DeleteBindingState | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [fetchingModels, setFetchingModels] = useState<string | null>(null);
  const [fetchedModels, setFetchedModels] = useState<Record<string, Array<{ id: string; owned_by: string }>>>({});
  const [showFetchedModels, setShowFetchedModels] = useState<string | null>(null);
  const [syncingBalance, setSyncingBalance] = useState<string | null>(null);
  const [syncResult, setSyncResult] = useState<{ provider: string; oldBalance: number | null; newBalance: number; currency: string } | null>(null);
  const [selectedImportModels, setSelectedImportModels] = useState<Set<string>>(new Set());
  const [importingModels, setImportingModels] = useState(false);
  const [existingModelNames, setExistingModelNames] = useState<Set<string>>(new Set());

  useEffect(() => {
    loadProviders();
  }, []);

  const loadProviders = async () => {
    setLoading(true);
    try {
      const result = await api.listProviders();
      setProviders(result.providers || []);
    } catch (err) {
      const { message, detail } = translateError(err);
      toast.addToast(message, 'error', detail);
    } finally {
      setLoading(false);
    }
  };

  const validateForm = (): boolean => {
    const errors: Record<string, string> = {};
    if (!form.name.trim()) {
      errors.name = '供应商名称不能为空';
    }
    setFormErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const handleCreate = async () => {
    if (!validateForm()) return;
    setSaving(true);
    try {
      await api.createProvider(form);
      toast.addToast('供应商创建成功', 'success', `已添加供应商: ${form.name}`);
      setShowForm(false);
      setForm({ ...emptyProvider });
      setFormErrors({});
      await loadProviders();
    } catch (err) {
      const { message, detail } = translateError(err);
      toast.addToast(message, 'error', detail);
    } finally {
      setSaving(false);
    }
  };

  const handleUpdate = async () => {
    if (!editName) return;
    setSaving(true);
    try {
      await api.updateProvider(editName, form);
      toast.addToast('供应商更新成功', 'success', `已更新供应商: ${editName}`);
      setEditName(null);
      setForm({ ...emptyProvider });
      setFormErrors({});
      await loadProviders();
    } catch (err) {
      const { message, detail } = translateError(err);
      toast.addToast(message, 'error', detail);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (name: string) => {
    if (!confirm(`确定删除供应商 "${name}"?`)) return;
    setDeleting(true);
    try {
      const result = await api.deleteProvider(name);
      // Check if backend returned binding info
      if (result && result.status === 'has_bindings') {
        setDeleteBinding({
          providerName: name,
          boundModels: result.bound_models || [],
          transferTo: '',
        });
        return;
      }
      toast.addToast('供应商已删除', 'success', `已删除供应商: ${name}`);
      await loadProviders();
    } catch (err) {
      const { message, detail } = translateError(err);
      toast.addToast(message, 'error', detail);
    } finally {
      setDeleting(false);
    }
  };

  const handleForceDelete = async () => {
    if (!deleteBinding) return;
    if (!confirm(`确定连同 ${deleteBinding.boundModels.length} 个绑定模型一起删除? 此操作不可撤销。`)) return;
    setDeleting(true);
    try {
      await api.deleteProvider(deleteBinding.providerName, { force: true });
      toast.addToast('供应商及绑定模型已删除', 'success');
      setDeleteBinding(null);
      await loadProviders();
    } catch (err) {
      const { message, detail } = translateError(err);
      toast.addToast(message, 'error', detail);
    } finally {
      setDeleting(false);
    }
  };

  const handleTransferDelete = async () => {
    if (!deleteBinding || !deleteBinding.transferTo) {
      toast.addToast('请选择目标供应商', 'error');
      return;
    }
    setDeleting(true);
    try {
      await api.deleteProvider(deleteBinding.providerName, { transfer_to: deleteBinding.transferTo });
      toast.addToast('供应商已删除，模型已转移', 'success', `模型已转移到: ${deleteBinding.transferTo}`);
      setDeleteBinding(null);
      await loadProviders();
    } catch (err) {
      const { message, detail } = translateError(err);
      toast.addToast(message, 'error', detail);
    } finally {
      setDeleting(false);
    }
  };

  const startEdit = (p: ProviderData) => {
    setEditName(p.name);
    setForm({ ...p });
    setShowForm(true);
    setFormErrors({});
  };

  const handleFetchModels = async (providerName: string) => {
    setFetchingModels(providerName);
    try {
      const [modelResult, existingResult] = await Promise.all([
        api.fetchProviderModels(providerName),
        api.getModels().catch(() => ({ models: [] })),
      ]);
      const models = modelResult.models || [];
      setFetchedModels((prev) => ({ ...prev, [providerName]: models }));
      setShowFetchedModels(providerName);
      // 记录已配置的模型名称
      const existingNames = new Set((existingResult.models || []).map((m: { name: string }) => m.name));
      setExistingModelNames(existingNames);
      setSelectedImportModels(new Set());
      toast.addToast(
        `查询到 ${models.length} 个模型`,
        'success',
        `其中 ${models.filter((m: { id: string }) => !existingNames.has(m.id)).length} 个未配置`
      );
    } catch (err) {
      const { message, detail } = translateError(err);
      toast.addToast(message, 'error', detail);
    } finally {
      setFetchingModels(null);
    }
  };

  const handleImportModels = async (providerName: string, modelIds: string[]) => {
    if (modelIds.length === 0) {
      toast.addToast('请选择要导入的模型', 'warning');
      return;
    }
    setImportingModels(true);
    try {
      const provider = providers.find((p) => p.name === providerName);
      const modelsToImport = modelIds.map((id) => ({
        name: id,
        provider: providerName,
        api_key: provider?.api_key || '',
        api_type: provider?.api_type || 'openai',
        base_url: provider?.base_url || '',
        modalities: ['text'], // 默认文本模态，后续自动探测
        enabled: false, // 导入后默认禁用
        capability_tags: [],
        price_input: 0,
        price_output: 0,
        price_currency: provider?.balance_currency || 'CNY',
      }));
      const result = await api.importModels(modelsToImport);
      toast.addToast(
        `导入完成: ${result.imported || 0} 个成功, ${result.errors || 0} 个失败`,
        result.errors > 0 ? 'warning' : 'success'
      );
      setShowFetchedModels(null);
      setSelectedImportModels(new Set());
    } catch (err) {
      const { message, detail } = translateError(err);
      toast.addToast(message, 'error', detail);
    } finally {
      setImportingModels(false);
    }
  };

  const handleSyncBalance = async (providerName: string) => {
    setSyncingBalance(providerName);
    setSyncResult(null);
    try {
      // Get current balance before sync
      const currentProvider = providers.find((p) => p.name === providerName);
      const oldBalance = currentProvider?.balance_manual ?? null;

      const result = await api.syncProviderBalance(providerName);
      if (result.status === 'ok') {
        setSyncResult({
          provider: providerName,
          oldBalance,
          newBalance: result.balance,
          currency: result.currency || 'CNY',
        });
        toast.addToast(`${providerName} 余额查询成功`, 'success', `新余额: ${result.balance} ${result.currency || ''}`);
        await loadProviders();
      } else if (result.status === 'no_script') {
        toast.addToast(`${providerName} 未配置余额查询脚本`, 'warning', '请先配置余额查询脚本或手动设置余额');
      } else if (result.status === 'error') {
        toast.addToast(`${providerName} 余额查询失败`, 'error', result.message || '脚本执行失败');
      } else if (result.status === 'no_data') {
        toast.addToast(`${providerName} 脚本未返回余额数据`, 'warning', result.message || '');
      }
    } catch (err) {
      const { message, detail } = translateError(err);
      toast.addToast(message, 'error', detail);
    } finally {
      setSyncingBalance(null);
    }
  };

  const handleConfirmSync = async () => {
    if (!syncResult) return;
    // Balance already synced in backend, just close the dialog
    setSyncResult(null);
  };

  // Other providers for transfer target
  const otherProviders = deleteBinding
    ? providers.filter((p) => p.name !== deleteBinding.providerName)
    : [];

  if (loading) return <div className="text-gray-500">加载中...</div>;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold">供应商管理</h2>
        <button
          onClick={() => { setShowForm(true); setEditName(null); setForm({ ...emptyProvider }); setFormErrors({}); }}
          className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600 text-sm"
        >
          + 新增供应商
        </button>
      </div>

      {/* Delete binding dialog */}
      {deleteBinding && (
        <div className="bg-white rounded-lg shadow p-4 mb-4 border-l-4 border-orange-500">
          <h3 className="font-semibold mb-2 text-orange-700">
            供应商 "{deleteBinding.providerName}" 存在绑定模型
          </h3>
          <p className="text-sm text-gray-600 mb-3">
            以下 {deleteBinding.boundModels.length} 个模型绑定了此供应商，请选择处理方式：
          </p>
          <div className="mb-3">
            <div className="flex flex-wrap gap-1">
              {deleteBinding.boundModels.map((m) => (
                <span key={m.name} className="px-2 py-0.5 bg-orange-100 text-orange-700 rounded text-xs">
                  {m.name}
                </span>
              ))}
            </div>
          </div>

          <div className="space-y-3">
            {/* Option 1: Transfer all models */}
            <div className="border rounded p-3">
              <h4 className="text-sm font-medium mb-2">转移模型到其他供应商</h4>
              <div className="flex gap-2">
                <select
                  className="flex-1 border rounded px-2 py-1 text-sm"
                  value={deleteBinding.transferTo}
                  onChange={(e) => setDeleteBinding({ ...deleteBinding, transferTo: e.target.value })}
                >
                  <option value="">选择目标供应商...</option>
                  {otherProviders.map((p) => (
                    <option key={p.name} value={p.name}>{p.name}</option>
                  ))}
                </select>
                <button
                  onClick={handleTransferDelete}
                  disabled={deleting || !deleteBinding.transferTo}
                  className="px-4 py-1 bg-blue-500 text-white rounded text-sm hover:bg-blue-600 disabled:bg-gray-400"
                >
                  {deleting ? '处理中...' : '转移并删除'}
                </button>
              </div>
            </div>

            {/* Option 2: Force delete */}
            <div className="border rounded p-3">
              <h4 className="text-sm font-medium mb-2 text-red-600">连同绑定模型一起删除</h4>
              <p className="text-xs text-gray-500 mb-2">此操作将同时删除供应商和所有绑定模型，不可撤销。</p>
              <button
                onClick={handleForceDelete}
                disabled={deleting}
                className="px-4 py-1 bg-red-500 text-white rounded text-sm hover:bg-red-600 disabled:bg-gray-400"
              >
                {deleting ? '删除中...' : '强制删除全部'}
              </button>
            </div>

            {/* Cancel */}
            <button
              onClick={() => setDeleteBinding(null)}
              className="px-4 py-1 bg-gray-200 text-gray-700 rounded text-sm hover:bg-gray-300"
            >
              取消
            </button>
          </div>
        </div>
      )}

      {showForm && (
        <div className="bg-white rounded-lg shadow p-4 mb-4">
          <h3 className="font-semibold mb-3">{editName ? '编辑供应商' : '新增供应商'}</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">名称</label>
              <input
                className={`w-full border rounded px-2 py-1 text-sm ${formErrors.name ? 'border-red-500' : ''}`}
                value={form.name}
                onChange={(e) => { setForm({ ...form, name: e.target.value }); setFormErrors({ ...formErrors, name: '' }); }}
                disabled={!!editName}
              />
              {formErrors.name && <div className="text-xs text-red-500 mt-1">{formErrors.name}</div>}
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">显示名称</label>
              <input className="w-full border rounded px-2 py-1 text-sm" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} placeholder="可选，用于界面显示" />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Base URL</label>
              <input className="w-full border rounded px-2 py-1 text-sm" value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">API Key</label>
              <input type="password" className="w-full border rounded px-2 py-1 text-sm" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">API 类型</label>
              <select className="w-full border rounded px-2 py-1 text-sm" value={form.api_type} onChange={(e) => setForm({ ...form, api_type: e.target.value })}>
                <option value="openai">OpenAI 兼容</option>
                <option value="zhipu">智谱 GLM</option>
                <option value="anthropic">Anthropic</option>
                <option value="azure">Azure OpenAI</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">供应商类型</label>
              <select className="w-full border rounded px-2 py-1 text-sm" value={form.provider_type} onChange={(e) => setForm({ ...form, provider_type: e.target.value })}>
                <option value="openai">OpenAI</option>
                <option value="deepseek">DeepSeek</option>
                <option value="openrouter">OpenRouter</option>
                <option value="anthropic">Anthropic</option>
                <option value="zhipu">智谱</option>
                <option value="siliconflow">SiliconFlow</option>
                <option value="custom">自定义</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">手动余额 (空=自动查询)</label>
              <div className="flex gap-1">
                <input type="number" step="0.01" className="flex-1 border rounded px-2 py-1 text-sm" value={form.balance_manual ?? ''} onChange={(e) => setForm({ ...form, balance_manual: e.target.value === '' ? null : Number(e.target.value) })} />
                {editName && (
                  <button
                    type="button"
                    onClick={() => handleSyncBalance(editName)}
                    disabled={syncingBalance === editName}
                    className="px-2 py-1 bg-purple-100 text-purple-700 rounded text-xs hover:bg-purple-200 disabled:opacity-50 whitespace-nowrap"
                    title="执行余额查询脚本并同步"
                  >
                    {syncingBalance === editName ? '查询中...' : '查询同步'}
                  </button>
                )}
              </div>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">余额货币</label>
              <select className="w-full border rounded px-2 py-1 text-sm" value={form.balance_currency} onChange={(e) => setForm({ ...form, balance_currency: e.target.value })}>
                <option value="CNY">CNY (人民币)</option>
                <option value="USD">USD (美元)</option>
                <option value="EUR">EUR (欧元)</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">余额来源</label>
              <select className="w-full border rounded px-2 py-1 text-sm" value={form.balance_source} onChange={(e) => setForm({ ...form, balance_source: e.target.value })}>
                <option value="auto">自动 (脚本优先，回退手动)</option>
                <option value="script">仅脚本查询</option>
                <option value="manual">仅手动输入</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">扣费模式</label>
              <select className="w-full border rounded px-2 py-1 text-sm" value={form.balance_deduction_mode} onChange={(e) => setForm({ ...form, balance_deduction_mode: e.target.value })}>
                <option value="realtime">实时扣费</option>
                <option value="periodic">定时扣费</option>
              </select>
            </div>
            <div className="flex items-end">
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />
                启用
              </label>
            </div>
          </div>
          {/* 余额查询脚本 */}
          <div className="mt-3">
            <div className="flex items-center justify-between mb-1">
              <label className="text-xs text-gray-500">余额查询脚本</label>
              <select
                className="border rounded px-2 py-0.5 text-xs"
                value=""
                onChange={(e) => {
                  if (e.target.value && BALANCE_TEMPLATES[e.target.value]) {
                    setForm({ ...form, balance_script: BALANCE_TEMPLATES[e.target.value].script });
                  }
                }}
              >
                <option value="">选择预置模板...</option>
                {Object.entries(BALANCE_TEMPLATES).map(([key, tpl]) => (
                  <option key={key} value={key}>{tpl.label}</option>
                ))}
              </select>
            </div>
            <textarea
              className="w-full border rounded px-2 py-1 text-sm font-mono"
              rows={8}
              value={form.balance_script}
              onChange={(e) => setForm({ ...form, balance_script: e.target.value })}
              placeholder="# 余额查询脚本&#10;# 可用变量: api_key, base_url, model_name&#10;# 返回: result = 余额数值 或 result = {&quot;balance&quot;: 数值, &quot;balance_currency&quot;: &quot;CNY&quot;}"
            />
          </div>
          {/* 价格查询脚本 */}
          <div className="mt-3">
            <label className="text-xs text-gray-500 block mb-1">价格查询脚本</label>
            <textarea
              className="w-full border rounded px-2 py-1 text-sm font-mono"
              rows={4}
              value={form.price_script}
              onChange={(e) => setForm({ ...form, price_script: e.target.value })}
              placeholder="# 价格查询脚本（可选）&#10;# 返回: result = {&quot;price_input&quot;: 数值, &quot;price_output&quot;: 数值, &quot;price_currency&quot;: &quot;CNY&quot;}"
            />
          </div>
          <div className="flex gap-2 mt-3">
            <button
              onClick={editName ? handleUpdate : handleCreate}
              disabled={saving}
              className={`px-4 py-2 rounded text-sm font-medium transition-colors ${
                saving
                  ? 'bg-gray-400 text-white cursor-not-allowed'
                  : 'bg-blue-500 text-white hover:bg-blue-600'
              }`}
            >
              {saving ? '保存中...' : '保存'}
            </button>
            <button
              onClick={() => { setShowForm(false); setEditName(null); setFormErrors({}); }}
              disabled={saving}
              className="px-4 py-2 bg-gray-200 text-gray-700 rounded text-sm hover:bg-gray-300 disabled:opacity-50"
            >
              取消
            </button>
          </div>
        </div>
      )}

      {/* Sync balance result dialog */}
      {syncResult && (
        <div className="bg-white rounded-lg shadow p-4 mb-4 border-l-4 border-blue-500">
          <h3 className="font-semibold mb-2 text-blue-700">
            供应商 "{syncResult.provider}" 余额查询结果
          </h3>
          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-gray-500">原余额:</span>
              <span className="font-medium">{syncResult.oldBalance !== null ? `${syncResult.oldBalance} ${syncResult.currency}` : '未设置'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">新余额:</span>
              <span className="font-bold text-blue-600">{syncResult.newBalance} {syncResult.currency}</span>
            </div>
          </div>
          <p className="text-xs text-gray-500 mt-2">余额已自动同步到供应商</p>
          <button
            onClick={handleConfirmSync}
            className="mt-3 px-4 py-1 bg-blue-500 text-white rounded text-sm hover:bg-blue-600"
          >
            确认
          </button>
        </div>
      )}

      {/* Fetched models display */}
      {showFetchedModels && fetchedModels[showFetchedModels] && (
        <div className="bg-white rounded-lg shadow p-4 mb-4 border-l-4 border-green-500">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold text-green-700">
              供应商 "{showFetchedModels}" 可用模型 ({fetchedModels[showFetchedModels].length})
            </h3>
            <div className="flex gap-2 items-center">
              <button
                onClick={() => {
                  // 一键导入所有未配置的模型
                  const unconfigured = fetchedModels[showFetchedModels]
                    .filter((m) => !existingModelNames.has(m.id))
                    .map((m) => m.id);
                  if (unconfigured.length === 0) {
                    toast.addToast('没有未配置的模型可导入', 'info');
                    return;
                  }
                  handleImportModels(showFetchedModels, unconfigured);
                }}
                disabled={importingModels}
                className="px-3 py-1 bg-green-500 text-white rounded text-xs hover:bg-green-600 disabled:opacity-50"
              >
                {importingModels ? '导入中...' : '一键导入未配置'}
              </button>
              <button
                onClick={() => setShowFetchedModels(null)}
                className="text-gray-400 hover:text-gray-600 text-sm"
              >
                关闭
              </button>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {fetchedModels[showFetchedModels].map((m) => {
              const isExisting = existingModelNames.has(m.id);
              const isSelected = selectedImportModels.has(m.id);
              return (
                <span
                  key={m.id}
                  className={`inline-flex items-center gap-1 px-3 py-1 rounded-full text-xs border cursor-pointer transition-colors ${
                    isExisting
                      ? 'bg-gray-100 text-gray-400 border-gray-200'
                      : isSelected
                        ? 'bg-blue-100 text-blue-700 border-blue-300'
                        : 'bg-green-50 text-green-700 border-green-200 hover:bg-green-100'
                  }`}
                  onClick={() => {
                    if (isExisting) return;
                    setSelectedImportModels((prev) => {
                      const next = new Set(prev);
                      if (next.has(m.id)) next.delete(m.id);
                      else next.add(m.id);
                      return next;
                    });
                  }}
                >
                  {m.id}
                  {isExisting && <span className="text-gray-400 ml-1">(已配置)</span>}
                </span>
              );
            })}
          </div>
          {selectedImportModels.size > 0 && (
            <div className="mt-3 flex items-center gap-2">
              <span className="text-sm text-gray-600">已选择 {selectedImportModels.size} 个模型</span>
              <button
                onClick={() => handleImportModels(showFetchedModels, Array.from(selectedImportModels))}
                disabled={importingModels}
                className="px-3 py-1 bg-blue-500 text-white rounded text-xs hover:bg-blue-600 disabled:opacity-50"
              >
                {importingModels ? '导入中...' : `导入选中 (${selectedImportModels.size})`}
              </button>
              <button
                onClick={() => setSelectedImportModels(new Set())}
                className="px-3 py-1 bg-gray-200 text-gray-700 rounded text-xs hover:bg-gray-300"
              >
                取消选择
              </button>
            </div>
          )}
        </div>
      )}

      <div className="bg-white rounded-lg shadow overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-gray-600">
            <tr>
              <th className="px-4 py-3 text-left">名称</th>
              <th className="px-4 py-3 text-left">Base URL</th>
              <th className="px-4 py-3 text-left">API Key</th>
              <th className="px-4 py-3 text-left">状态</th>
              <th className="px-4 py-3 text-left">操作</th>
            </tr>
          </thead>
          <tbody>
            {providers.map((p) => (
              <tr key={p.name} className="border-b hover:bg-gray-50">
                <td className="px-4 py-3 font-medium">{p.name}</td>
                <td className="px-4 py-3">{p.base_url || '-'}</td>
                <td className="px-4 py-3">{p.api_key ? '••••••••' : '-'}</td>
                <td className="px-4 py-3">
                  <span className={`px-2 py-0.5 rounded text-xs ${p.enabled ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
                    {p.enabled ? '启用' : '禁用'}
                  </span>
                </td>
                <td className="px-4 py-3 space-x-2">
                  <button onClick={() => startEdit(p)} className="text-blue-500 hover:underline text-xs">编辑</button>
                  <button
                    onClick={() => handleSyncBalance(p.name)}
                    disabled={syncingBalance === p.name}
                    className="text-purple-500 hover:underline text-xs disabled:opacity-50"
                  >
                    {syncingBalance === p.name ? '查询中...' : '查询余额'}
                  </button>
                  <button
                    onClick={() => handleFetchModels(p.name)}
                    disabled={fetchingModels === p.name}
                    className="text-green-500 hover:underline text-xs disabled:opacity-50"
                  >
                    {fetchingModels === p.name ? '查询中...' : '查询模型'}
                  </button>
                  <button onClick={() => handleDelete(p.name)} disabled={deleting} className="text-red-500 hover:underline text-xs disabled:opacity-50">删除</button>
                </td>
              </tr>
            ))}
            {providers.length === 0 && (
              <tr><td colSpan={5} className="px-4 py-6 text-center text-gray-400">暂无供应商</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
