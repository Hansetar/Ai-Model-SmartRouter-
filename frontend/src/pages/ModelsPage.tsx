import { useState, useEffect, useRef } from 'react';
import { createApi } from '../api';
import { useNavigate } from 'react-router-dom';
import { useToast } from '../components/Toast';

interface ModelsPageProps {
  token: string;
}

interface ModelData {
  name: string;
  provider: string;
  api_key: string;
  api_type: string;
  base_url: string;
  litellm_name: string;
  params_b: number;
  capability: string;
  task_types: string[];
  modalities: string[];
  pending_modalities: string[] | null;
  capability_tags: string[];
  price_input: number;
  price_output: number;
  price_currency: string;
  price_script: string;
  enabled: boolean;
  active_hours: string[] | null;
  schedule_rules: ScheduleRuleData[];
  [key: string]: unknown;
}

interface ScheduleRuleData {
  time_ranges: string[];
  days_of_week: number[];
  days_of_month: number[];
  start_date: string;
  end_date: string;
  include_holidays: boolean;
}

const emptyScheduleRule: ScheduleRuleData = {
  time_ranges: [],
  days_of_week: [],
  days_of_month: [],
  start_date: '',
  end_date: '',
  include_holidays: true,
};

const emptyModel: ModelData = {
  name: '',
  provider: '',
  api_key: '',
  api_type: 'openai',
  base_url: '',
  litellm_name: '',
  params_b: 0,
  capability: '',
  task_types: [],
  modalities: ['text'],
  pending_modalities: null,
  capability_tags: [],
  price_input: 0,
  price_output: 0,
  price_currency: 'CNY',
  price_script: '',
  enabled: true,
  active_hours: null,
  schedule_rules: [],
};

export default function ModelsPage({ token }: ModelsPageProps) {
  const api = createApi(token);
  const [models, setModels] = useState<ModelData[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [editName, setEditName] = useState<string | null>(null);
  const [form, setForm] = useState<ModelData>({ ...emptyModel });
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [showImport, setShowImport] = useState(false);
  const [importText, setImportText] = useState('');
  const [importing, setImporting] = useState(false);
  const [batchProvider, setBatchProvider] = useState('');
  const [batchApiKey, setBatchApiKey] = useState('');
  const [showBatchProvider, setShowBatchProvider] = useState(false);
  const [showBatchApiKey, setShowBatchApiKey] = useState(false);
  // 筛选状态
  const [filterProvider, setFilterProvider] = useState('');
  const [filterModality, setFilterModality] = useState('');
  const [filterTag, setFilterTag] = useState('');
  const [filterFreeOnly, setFilterFreeOnly] = useState(false);
  const [filterDifficulty, setFilterDifficulty] = useState('');
  const [filterKeyword, setFilterKeyword] = useState('');
  const [filterPriceMax, setFilterPriceMax] = useState('');
  const [showFilters, setShowFilters] = useState(false);

  useEffect(() => {
    loadModels();
  }, []);

  const loadModels = async () => {
    setLoading(true);
    try {
      const result = await api.getModels();
      setModels(result.models || []);
    } catch (err) {
      console.error('Failed to load models:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleAdd = async () => {
    if (!form.name?.trim()) { alert('模型名称不能为空'); return; }
    if (!form.provider?.trim()) { alert('提供商不能为空'); return; }
    try {
      await api.createModel(form);
      setShowAdd(false);
      setForm({ ...emptyModel });
      await loadModels();
    } catch (err) {
      alert('新增模型失败');
    }
  };

  const handleUpdate = async (name: string) => {
    try {
      await api.updateModel(name, form);
      setEditName(null);
      await loadModels();
    } catch (err) {
      alert('更新模型失败');
    }
  };

  const handleClone = async (name: string) => {
    const newName = prompt(`克隆模型 "${name}" 为新名称:`, `${name}-copy`);
    if (!newName) return;
    try {
      await api.cloneModel(name, newName);
      await loadModels();
    } catch (err) {
      alert('克隆失败');
    }
  };

  const handleDelete = async (name: string) => {
    if (!confirm(`确定删除模型 "${name}"?`)) return;
    try {
      await api.deleteModel(name);
      await loadModels();
    } catch (err) {
      alert('删除失败');
    }
  };

  const startEdit = (m: ModelData) => {
    setEditName(m.name);
    setForm({ ...m });
  };

  const toggleSelect = (name: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const toggleSelectAll = () => {
    const visibleNames = filteredModels.map((m) => m.name);
    const allVisibleSelected = visibleNames.length > 0 && visibleNames.every((n) => selected.has(n));
    if (allVisibleSelected) {
      setSelected(new Set());
    } else {
      setSelected(new Set(visibleNames));
    }
  };

  const handleBatchDelete = async () => {
    const names = Array.from(selected);
    if (!confirm(`确定批量删除 ${names.length} 个模型?`)) return;
    try {
      await api.batchModelOperation('delete', names);
      setSelected(new Set());
      await loadModels();
      alert('批量删除成功');
    } catch (err) {
      alert('批量删除失败');
    }
  };

  const handleBatchProvider = async () => {
    const names = Array.from(selected);
    if (!batchProvider.trim()) { alert('请输入提供商名称'); return; }
    try {
      await api.batchModelOperation('update_provider', names, { provider: batchProvider });
      setSelected(new Set());
      setShowBatchProvider(false);
      setBatchProvider('');
      await loadModels();
      alert('批量修改提供商成功');
    } catch (err) {
      alert('批量修改提供商失败');
    }
  };

  const handleBatchApiKey = async () => {
    const names = Array.from(selected);
    if (!batchApiKey.trim()) { alert('请输入密钥'); return; }
    try {
      await api.batchModelOperation('update_api_key', names, { api_key: batchApiKey });
      setSelected(new Set());
      setShowBatchApiKey(false);
      setBatchApiKey('');
      await loadModels();
      alert('批量修改密钥成功');
    } catch (err) {
      alert('批量修改密钥失败');
    }
  };

  const [detectingModalities, setDetectingModalities] = useState(false);
  const [detectMethods, setDetectMethods] = useState<string[]>([]); // empty = all methods
  const [showDetectOptions, setShowDetectOptions] = useState(false);

  const DETECT_METHOD_OPTIONS = [
    { value: 'query', label: 'API查询', desc: '查询上游 /models/{name} 接口' },
    { value: 'name_infer', label: '名称推断', desc: '基于模型名称关键词推断' },
    { value: 'probe_image', label: '图片探测', desc: '发送最小图片请求测试' },
    { value: 'probe_audio', label: '音频探测', desc: '发送最小音频请求测试' },
    { value: 'probe_video', label: '视频探测', desc: '发送最小视频请求测试' },
    { value: 'probe_file', label: '文件探测', desc: '发送最小文件请求测试' },
    { value: 'structured_test', label: '结构化测试', desc: '多模态组合请求测试' },
  ];

  const handleDetectModalities = async () => {
    const names = Array.from(selected);
    if (!names.length) { alert('请先选择模型'); return; }
    if (!confirm(`确定检测 ${names.length} 个模型的模态支持？检测结果将进入待确认状态。`)) return;
    setDetectingModalities(true);
    try {
      const result = await api.detectModalities(names, true, detectMethods.length > 0 ? detectMethods : undefined);
      const detected = Object.keys(result.results || {});
      await loadModels();
      alert(`模态检测完成，${detected.length} 个模型已更新待确认状态`);
    } catch (err) {
      alert('模态检测失败');
    } finally {
      setDetectingModalities(false);
    }
  };

  const handleConfirmModalities = async (discard: boolean) => {
    const names = Array.from(selected);
    if (!names.length) { alert('请先选择模型'); return; }
    const action = discard ? '拒绝' : '确认';
    if (!confirm(`确定${action} ${names.length} 个模型的待确认模态？`)) return;
    try {
      const result = await api.confirmModalities(names, discard);
      await loadModels();
      alert(`${action}成功，${result.updated?.length || 0} 个模型已更新`);
    } catch (err) {
      alert(`${action}失败`);
    }
  };

  // Single model confirm/reject modality (inline click)
  const handleSingleConfirmModality = async (modelName: string, discard: boolean) => {
    const action = discard ? '拒绝' : '确认';
    try {
      const result = await api.confirmModalities([modelName], discard);
      await loadModels();
      if (result.updated?.length > 0) {
        // Success - no alert needed, UI updates immediately
      } else {
        alert(`该模型没有待确认的模态`);
      }
    } catch (err) {
      alert(`${action}模态失败`);
    }
  };

  const handleImport = async () => {
    if (!importText.trim()) { alert('请输入 JSON 数据'); return; }
    try {
      const parsed = JSON.parse(importText);
      const modelList = Array.isArray(parsed) ? parsed : parsed.models || [parsed];
      setImporting(true);
      await api.importModels(modelList);
      setShowImport(false);
      setImportText('');
      await loadModels();
      alert('导入成功');
    } catch (err) {
      if (err instanceof SyntaxError) {
        alert('JSON 格式错误，请检查输入');
      } else {
        alert('导入失败');
      }
    } finally {
      setImporting(false);
    }
  };

  if (loading) return <div className="text-gray-500">加载中...</div>;

  // 筛选逻辑
  const filteredModels = models.filter((m) => {
    if (filterProvider && m.provider !== filterProvider) return false;
    if (filterModality) {
      const mods = (m.modalities as string[]) || [];
      if (!mods.includes(filterModality)) return false;
    }
    if (filterTag) {
      const tags = (m.task_types as string[]) || [];
      if (!tags.includes(filterTag)) return false;
    }
    if (filterFreeOnly && (m.price_input > 0 || m.price_output > 0)) return false;
    if (filterDifficulty) {
      const cap = Number(m.capability) || 0;
      if (filterDifficulty === 'simple' && cap > 40) return false;
      if (filterDifficulty === 'medium' && (cap <= 40 || cap > 70)) return false;
      if (filterDifficulty === 'hard' && cap <= 70) return false;
    }
    if (filterKeyword) {
      const kw = filterKeyword.toLowerCase();
      if (!m.name.toLowerCase().includes(kw) && !(m.provider || '').toLowerCase().includes(kw)) return false;
    }
    if (filterPriceMax) {
      const maxP = Number(filterPriceMax);
      if (m.price_input > maxP || m.price_output > maxP) return false;
    }
    return true;
  });

  // 提取所有可选的筛选值
  const allProviders = [...new Set(models.map((m) => m.provider).filter(Boolean))];
  const allModalities = [...new Set(models.flatMap((m) => (m.modalities as string[]) || []))];
  const allTags = [...new Set(models.flatMap((m) => (m.task_types as string[]) || []))];

  const clearFilters = () => {
    setFilterProvider('');
    setFilterModality('');
    setFilterTag('');
    setFilterFreeOnly(false);
    setFilterDifficulty('');
    setFilterKeyword('');
    setFilterPriceMax('');
  };

  const hasActiveFilter = filterProvider || filterModality || filterTag || filterFreeOnly || filterDifficulty || filterKeyword || filterPriceMax;

  return (
    <div>
      {/* Auto Smart Routing Tip */}
      <div className="mb-4 bg-gradient-to-r from-blue-50 to-indigo-50 border border-blue-200 rounded-lg p-3 flex items-center gap-3">
        <div className="w-7 h-7 bg-blue-500 rounded-lg flex items-center justify-center flex-shrink-0">
          <svg className="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
          </svg>
        </div>
        <div className="flex-1 min-w-0">
          <span className="text-sm text-blue-800">
            调用接口时设置 <code className="px-1.5 py-0.5 bg-blue-100 rounded font-mono text-blue-800 font-bold">model="auto"</code> 即可启用智能路由，系统将根据请求内容自动选择最优模型
          </span>
        </div>
      </div>

      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between mb-6 gap-3">
        <h2 className="text-2xl font-bold">模型管理</h2>
        <div className="flex gap-2 flex-wrap">
          <button
            onClick={() => setShowFilters(!showFilters)}
            className={`px-4 py-2 rounded text-sm ${showFilters ? 'bg-blue-600 text-white' : 'bg-gray-200 text-gray-700 hover:bg-gray-300'}`}
          >
            筛选 {hasActiveFilter ? `(${filteredModels.length}/${models.length})` : ''}
          </button>
          <button
            onClick={() => { setShowAdd(true); setForm({ ...emptyModel }); }}
            className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600 text-sm"
          >
            + 新增模型
          </button>
          <button
            onClick={() => setShowImport(true)}
            className="px-4 py-2 bg-purple-500 text-white rounded hover:bg-purple-600 text-sm"
          >
            导入模型
          </button>
        </div>
      </div>

      {/* Filter panel */}
      {showFilters && (
        <div className="bg-white rounded-lg shadow p-4 mb-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">关键词搜索</label>
              <input
                className="w-full border rounded px-2 py-1 text-sm"
                placeholder="模型名称/提供商"
                value={filterKeyword}
                onChange={(e) => setFilterKeyword(e.target.value)}
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">提供商</label>
              <select
                className="w-full border rounded px-2 py-1 text-sm"
                value={filterProvider}
                onChange={(e) => setFilterProvider(e.target.value)}
              >
                <option value="">全部</option>
                {allProviders.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">模态</label>
              <select
                className="w-full border rounded px-2 py-1 text-sm"
                value={filterModality}
                onChange={(e) => setFilterModality(e.target.value)}
              >
                <option value="">全部</option>
                {allModalities.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">标签/任务类型</label>
              <select
                className="w-full border rounded px-2 py-1 text-sm"
                value={filterTag}
                onChange={(e) => setFilterTag(e.target.value)}
              >
                <option value="">全部</option>
                {allTags.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">难度等级</label>
              <select
                className="w-full border rounded px-2 py-1 text-sm"
                value={filterDifficulty}
                onChange={(e) => setFilterDifficulty(e.target.value)}
              >
                <option value="">全部</option>
                <option value="simple">简单 (cap≤40)</option>
                <option value="medium">中等 (40&lt;cap≤70)</option>
                <option value="hard">困难 (cap&gt;70)</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">最高价格</label>
              <input
                type="number"
                step="0.01"
                className="w-full border rounded px-2 py-1 text-sm"
                placeholder="输入/输出价格上限"
                value={filterPriceMax}
                onChange={(e) => setFilterPriceMax(e.target.value)}
              />
            </div>
            <div className="flex items-end">
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={filterFreeOnly} onChange={(e) => setFilterFreeOnly(e.target.checked)} />
                仅免费模型
              </label>
            </div>
            <div className="flex items-end">
              <button onClick={clearFilters} className="px-3 py-1.5 bg-gray-200 text-gray-700 rounded text-xs hover:bg-gray-300">清除筛选</button>
            </div>
          </div>
        </div>
      )}

      {/* Batch operation bar */}
      {selected.size > 0 && (
        <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 mb-4 flex flex-col sm:flex-row items-start sm:items-center gap-3">
          <span className="text-sm text-blue-700 font-medium">已选择 {selected.size} 个模型</span>
          <div className="flex gap-2 flex-wrap">
            <button
              onClick={() => { setShowBatchProvider(true); setShowBatchApiKey(false); }}
              className="px-3 py-1.5 bg-blue-500 text-white rounded text-xs hover:bg-blue-600"
            >
              批量修改提供商
            </button>
            <button
              onClick={() => { setShowBatchApiKey(true); setShowBatchProvider(false); }}
              className="px-3 py-1.5 bg-blue-500 text-white rounded text-xs hover:bg-blue-600"
            >
              批量修改密钥
            </button>
            <button
              onClick={handleBatchDelete}
              className="px-3 py-1.5 bg-red-500 text-white rounded text-xs hover:bg-red-600"
            >
              批量删除
            </button>
            <button
              onClick={handleDetectModalities}
              disabled={detectingModalities}
              className={`px-3 py-1.5 text-white rounded text-xs ${detectingModalities ? 'bg-purple-300 cursor-not-allowed' : 'bg-purple-500 hover:bg-purple-600'}`}
            >
              {detectingModalities ? '检测中...' : '检测模态'}
            </button>
            <button
              onClick={() => setShowDetectOptions(!showDetectOptions)}
              className="px-2 py-1.5 bg-purple-100 text-purple-700 rounded text-xs hover:bg-purple-200"
              title="选择检测方法"
            >
              方法 {detectMethods.length > 0 ? `(${detectMethods.length})` : '(全部)'}
            </button>
            <button
              onClick={() => handleConfirmModalities(false)}
              className="px-3 py-1.5 bg-green-500 text-white rounded text-xs hover:bg-green-600"
            >
              确认模态
            </button>
            <button
              onClick={() => handleConfirmModalities(true)}
              className="px-3 py-1.5 bg-yellow-500 text-white rounded text-xs hover:bg-yellow-600"
            >
              拒绝模态
            </button>
            <button
              onClick={() => setSelected(new Set())}
              className="px-3 py-1.5 bg-gray-200 text-gray-700 rounded text-xs hover:bg-gray-300"
            >
              取消选择
            </button>
          </div>
        </div>
      )}

      {/* Detect methods selection panel */}
      {showDetectOptions && (
        <div className="bg-purple-50 border border-purple-200 rounded-lg p-4 mb-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-purple-800">模态检测方法选择</h3>
            <div className="flex gap-2">
              <button
                onClick={() => setDetectMethods([])}
                className={`px-2 py-1 rounded text-xs ${detectMethods.length === 0 ? 'bg-purple-500 text-white' : 'bg-purple-100 text-purple-700 hover:bg-purple-200'}`}
              >
                全部方法
              </button>
              <button
                onClick={() => setShowDetectOptions(false)}
                className="px-2 py-1 bg-gray-200 text-gray-700 rounded text-xs hover:bg-gray-300"
              >
                关闭
              </button>
            </div>
          </div>
          <p className="text-xs text-purple-600 mb-3">选择用于检测模型模态支持的方法。不选择则使用全部方法。多种方法交叉验证可提高准确性。</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2">
            {DETECT_METHOD_OPTIONS.map((opt) => (
              <label
                key={opt.value}
                className={`flex items-start gap-2 p-2 rounded border cursor-pointer ${
                  detectMethods.includes(opt.value) ? 'bg-purple-100 border-purple-300' : 'bg-white border-gray-200 hover:border-purple-200'
                }`}
              >
                <input
                  type="checkbox"
                  checked={detectMethods.includes(opt.value)}
                  onChange={(e) => {
                    if (e.target.checked) {
                      setDetectMethods([...detectMethods, opt.value]);
                    } else {
                      setDetectMethods(detectMethods.filter((m) => m !== opt.value));
                    }
                  }}
                  className="mt-0.5 rounded"
                />
                <div>
                  <div className="text-xs font-medium text-gray-800">{opt.label}</div>
                  <div className="text-xs text-gray-500">{opt.desc}</div>
                </div>
              </label>
            ))}
          </div>
        </div>
      )}

      {/* Batch provider form */}
      {showBatchProvider && (
        <div className="bg-white rounded-lg shadow p-4 mb-4">
          <h3 className="font-semibold mb-3">批量修改提供商</h3>
          <div className="flex gap-3 items-end">
            <div className="flex-1">
              <label className="block text-xs text-gray-500 mb-1">新提供商名称</label>
              <input
                className="w-full border rounded px-2 py-1 text-sm"
                value={batchProvider}
                onChange={(e) => setBatchProvider(e.target.value)}
                placeholder="输入提供商名称"
              />
            </div>
            <button onClick={handleBatchProvider} className="px-4 py-1.5 bg-blue-500 text-white rounded text-sm hover:bg-blue-600">确认</button>
            <button onClick={() => setShowBatchProvider(false)} className="px-4 py-1.5 bg-gray-200 text-gray-700 rounded text-sm hover:bg-gray-300">取消</button>
          </div>
        </div>
      )}

      {/* Batch api key form */}
      {showBatchApiKey && (
        <div className="bg-white rounded-lg shadow p-4 mb-4">
          <h3 className="font-semibold mb-3">批量修改密钥</h3>
          <div className="flex gap-3 items-end">
            <div className="flex-1">
              <label className="block text-xs text-gray-500 mb-1">新密钥</label>
              <input
                type="password"
                className="w-full border rounded px-2 py-1 text-sm"
                value={batchApiKey}
                onChange={(e) => setBatchApiKey(e.target.value)}
                placeholder="输入新密钥"
              />
            </div>
            <button onClick={handleBatchApiKey} className="px-4 py-1.5 bg-blue-500 text-white rounded text-sm hover:bg-blue-600">确认</button>
            <button onClick={() => setShowBatchApiKey(false)} className="px-4 py-1.5 bg-gray-200 text-gray-700 rounded text-sm hover:bg-gray-300">取消</button>
          </div>
        </div>
      )}

      {/* Import form */}
      {showImport && (
        <div className="bg-white rounded-lg shadow p-4 mb-4">
          <h3 className="font-semibold mb-3">导入模型</h3>
          <p className="text-xs text-gray-500 mb-2">粘贴 JSON 格式的模型数据，支持数组或包含 models 字段的对象</p>
          <textarea
            className="w-full border rounded px-3 py-2 text-sm font-mono h-48"
            placeholder='[{"name":"model-1","provider":"openai","capability":"chat","price_input":0.01,"price_output":0.03,"enabled":true}]'
            value={importText}
            onChange={(e) => setImportText(e.target.value)}
          />
          <div className="flex gap-2 mt-3">
            <button onClick={handleImport} disabled={importing} className="px-4 py-2 bg-blue-500 text-white rounded text-sm hover:bg-blue-600 disabled:opacity-50">
              {importing ? '导入中...' : '导入'}
            </button>
            <button onClick={() => { setShowImport(false); setImportText(''); }} className="px-4 py-2 bg-gray-200 text-gray-700 rounded text-sm hover:bg-gray-300">取消</button>
          </div>
        </div>
      )}

      {/* Add form */}
      {showAdd && (
        <div className="bg-white rounded-lg shadow p-4 mb-4">
          <h3 className="font-semibold mb-3">新增模型</h3>
          <ModelForm form={form} setForm={setForm} token={token} />
          <div className="flex gap-2 mt-3">
            <button onClick={handleAdd} className="px-4 py-2 bg-blue-500 text-white rounded text-sm hover:bg-blue-600">保存</button>
            <button onClick={() => setShowAdd(false)} className="px-4 py-2 bg-gray-200 text-gray-700 rounded text-sm hover:bg-gray-300">取消</button>
          </div>
        </div>
      )}

      <div className="bg-white rounded-lg shadow overflow-x-auto">
        <table className="w-full text-sm min-w-[700px]">
          <thead className="bg-gray-50 text-gray-600">
            <tr>
              <th className="px-4 py-3 text-left w-10">
                <input
                  type="checkbox"
                  checked={filteredModels.length > 0 && filteredModels.every((m) => selected.has(m.name))}
                  onChange={toggleSelectAll}
                  className="rounded"
                />
              </th>
              <th className="px-4 py-3 text-left">模型名称</th>
              <th className="px-4 py-3 text-left">Provider</th>
              <th className="px-4 py-3 text-left">能力</th>
              <th className="px-4 py-3 text-left">模态</th>
              <th className="px-4 py-3 text-left">输入价格</th>
              <th className="px-4 py-3 text-left">输出价格</th>
              <th className="px-4 py-3 text-left">状态</th>
              <th className="px-4 py-3 text-left">操作</th>
            </tr>
          </thead>
          <tbody>
            {filteredModels.map((m) => (
              <ModelRow
                key={m.name}
                model={m}
                selected={selected.has(m.name)}
                onToggleSelect={() => toggleSelect(m.name)}
                editing={editName === m.name}
                form={form}
                setForm={setForm}
                token={token}
                onEdit={() => startEdit(m)}
                onSave={() => handleUpdate(m.name)}
                onCancel={() => setEditName(null)}
                onClone={() => handleClone(m.name)}
                onDelete={() => handleDelete(m.name)}
                onConfirmModality={() => handleSingleConfirmModality(m.name, false)}
                onRejectModality={() => handleSingleConfirmModality(m.name, true)}
              />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ModelRow({
  model,
  selected,
  onToggleSelect,
  editing,
  form,
  setForm,
  token,
  onEdit,
  onSave,
  onCancel,
  onClone,
  onDelete,
  onConfirmModality,
  onRejectModality,
}: {
  model: ModelData;
  selected: boolean;
  onToggleSelect: () => void;
  editing: boolean;
  form: ModelData;
  setForm: (f: ModelData) => void;
  token: string;
  onEdit: () => void;
  onSave: () => void;
  onCancel: () => void;
  onClone: () => void;
  onDelete: () => void;
  onConfirmModality: () => void;
  onRejectModality: () => void;
}) {
  const pendingMods = (model as Record<string, unknown>).pending_modalities as string[] | undefined;
  return (
    <>
      <tr className={`border-b hover:bg-gray-50 ${selected ? 'bg-blue-50' : ''}`}>
        <td className="px-4 py-3">
          <input
            type="checkbox"
            checked={selected}
            onChange={onToggleSelect}
            className="rounded"
          />
        </td>
        <td className="px-4 py-3 font-medium">{model.name}</td>
        <td className="px-4 py-3">{model.provider || '-'}</td>
        <td className="px-4 py-3">{model.capability || '-'}</td>
        <td className="px-4 py-3">
          <div className="flex flex-wrap gap-1">
            {((model.modalities as string[]) || []).map((mod) => (
              <span key={mod} className={`px-1.5 py-0.5 rounded text-xs ${mod === 'text' ? 'bg-gray-100 text-gray-600' : 'bg-purple-100 text-purple-700'}`}>
                {mod}
              </span>
            ))}
            {pendingMods && pendingMods.length > 0 && (
              <div className="flex items-center gap-1 ml-1">
                <span className="px-1.5 py-0.5 rounded text-xs bg-yellow-100 text-yellow-700" title={`待确认: ${pendingMods.join(', ')}`}>
                  待确认({pendingMods.length})
                </span>
                <button
                  onClick={onConfirmModality}
                  className="px-1.5 py-0.5 rounded text-xs bg-green-500 text-white hover:bg-green-600 cursor-pointer"
                  title="确认应用待确认模态"
                >
                  确认
                </button>
                <button
                  onClick={onRejectModality}
                  className="px-1.5 py-0.5 rounded text-xs bg-yellow-500 text-white hover:bg-yellow-600 cursor-pointer"
                  title="拒绝待确认模态"
                >
                  拒绝
                </button>
              </div>
            )}
          </div>
        </td>
        <td className="px-4 py-3">{model.price_input}</td>
        <td className="px-4 py-3">{model.price_output}</td>
        <td className="px-4 py-3">
          <span className={`px-2 py-0.5 rounded text-xs ${model.enabled ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
            {model.enabled ? '启用' : '禁用'}
          </span>
        </td>
        <td className="px-4 py-3 space-x-2 whitespace-nowrap">
          <button onClick={onEdit} className="text-blue-500 hover:underline text-xs">编辑</button>
          <button onClick={onClone} className="text-blue-500 hover:underline text-xs">克隆</button>
          <button onClick={onDelete} className="text-red-500 hover:underline text-xs">删除</button>
        </td>
      </tr>
      {editing && (
        <tr>
          <td colSpan={9} className="px-4 py-3 bg-gray-50">
            <ModelForm form={form} setForm={setForm} token={token} />
            <div className="flex gap-2 mt-3">
              <button onClick={onSave} className="px-4 py-2 bg-blue-500 text-white rounded text-sm hover:bg-blue-600">保存</button>
              <button onClick={onCancel} className="px-4 py-2 bg-gray-200 text-gray-700 rounded text-sm hover:bg-gray-300">取消</button>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function ModelForm({ form, setForm, token }: { form: ModelData; setForm: (f: ModelData) => void; token: string }) {
  const update = (key: keyof ModelData, value: unknown) => setForm({ ...form, [key]: value });
  const api = createApi(token);
  const toast = useToast();
  const navigate = useNavigate();
  const [providers, setProviders] = useState<string[]>([]);
  const [providerInput, setProviderInput] = useState(form.provider);
  const [showDropdown, setShowDropdown] = useState(false);
  const [highlightIndex, setHighlightIndex] = useState(-1);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const [detecting, setDetecting] = useState(false);
  const [formErrors, setFormErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    setProviderInput(form.provider);
  }, [form.provider]);

  useEffect(() => {
    const loadProviders = async () => {
      try {
        const result = await api.listProviders();
        const names = (result.providers || []).map((p: { name: string }) => p.name);
        setProviders(names);
      } catch { /* ignore */ }
    };
    loadProviders();
  }, []);

  const filteredProviders = providers.filter(
    (p) => p.toLowerCase().includes(providerInput.toLowerCase()) && p !== providerInput
  );

  const isNewProvider = providerInput && !providers.includes(providerInput);

  const handleProviderSelect = (name: string) => {
    setProviderInput(name);
    update('provider', name);
    setShowDropdown(false);
    setHighlightIndex(-1);
  };

  const handleProviderChange = (value: string) => {
    setProviderInput(value);
    update('provider', value);
    setShowDropdown(true);
    setHighlightIndex(-1);
    if (formErrors.provider) setFormErrors({ ...formErrors, provider: '' });
  };

  const handleProviderKeyDown = (e: React.KeyboardEvent) => {
    if (!showDropdown || filteredProviders.length === 0) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setHighlightIndex((i) => Math.min(i + 1, filteredProviders.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setHighlightIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === 'Enter' && highlightIndex >= 0) {
      e.preventDefault();
      handleProviderSelect(filteredProviders[highlightIndex]);
    } else if (e.key === 'Escape') {
      setShowDropdown(false);
    }
  };

  // 自动探测功能
  const handleAutoDetect = async () => {
    if (!form.name || !form.provider) {
      toast.addToast('请先填写模型名称和提供商', 'warning');
      return;
    }
    setDetecting(true);
    try {
      const result = await api.detectModalities([form.name], true);
      const detected = result.results?.[form.name];
      if (detected) {
        // 更新模态
        if (detected.modalities && detected.modalities.length > 0) {
          update('modalities', detected.modalities);
        }
        if (detected.capability_tags && detected.capability_tags.length > 0) {
          update('capability_tags', detected.capability_tags);
        }
        toast.addToast('自动探测完成', 'success', '模态和标签已更新，请确认');
      } else {
        // 探测失败，使用默认值
        if (!form.modalities || form.modalities.length === 0) {
          update('modalities', ['text']);
        }
        toast.addToast('自动探测未返回结果', 'warning', '已使用默认值（文本模态），请手动修改');
      }
    } catch (err) {
      // 探测失败，使用默认值
      if (!form.modalities || form.modalities.length === 0) {
        update('modalities', ['text']);
      }
      toast.addToast('自动探测失败', 'warning', '已使用默认值，请手动修改');
    } finally {
      setDetecting(false);
    }
  };

  // 必填项验证
  const validateRequired = (): boolean => {
    const errors: Record<string, string> = {};
    if (!form.name?.trim()) {
      errors.name = '模型名称不能为空';
    }
    if (!form.provider?.trim()) {
      errors.provider = '提供商不能为空';
    }
    setFormErrors(errors);
    return Object.keys(errors).length === 0;
  };

  // 暴露验证方法给父组件
  useEffect(() => {
    (window as unknown as Record<string, unknown>).__modelFormValidate = validateRequired;
    return () => { delete (window as unknown as Record<string, unknown>).__modelFormValidate; };
  }, [form.name, form.provider]);

  // 必填标识组件
  const RequiredMark = () => <span className="text-red-500 ml-0.5">*</span>;

  // 问号提示组件
  const HelpTip = ({ text }: { text: string }) => (
    <span className="relative group ml-1 inline-flex items-center">
      <svg className="w-3.5 h-3.5 text-gray-400 cursor-help" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
      <span className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 px-2 py-1 bg-gray-800 text-white text-xs rounded whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-20">
        {text}
      </span>
    </span>
  );

  return (
    <>
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
      <div>
        <label className="block text-xs text-gray-500 mb-1">模型名称<RequiredMark /><HelpTip text="模型的唯一标识名称，如 gpt-4o、deepseek-chat" /></label>
        <input
          className={`w-full border rounded px-2 py-1 text-sm ${formErrors.name ? 'border-red-500' : ''}`}
          value={form.name}
          onChange={(e) => { update('name', e.target.value); if (formErrors.name) setFormErrors({ ...formErrors, name: '' }); }}
          placeholder="如: gpt-4o, deepseek-chat"
        />
        {formErrors.name && <div className="text-xs text-red-500 mt-0.5">{formErrors.name}</div>}
      </div>
      <div className="relative" ref={dropdownRef}>
        <label className="block text-xs text-gray-500 mb-1">Provider<RequiredMark /><HelpTip text="模型所属的供应商，可从下拉选择或手动输入新名称" /></label>
        <input
          className={`w-full border rounded px-2 py-1 text-sm ${formErrors.provider ? 'border-red-500' : ''}`}
          value={providerInput}
          onChange={(e) => handleProviderChange(e.target.value)}
          onFocus={() => setShowDropdown(true)}
          onBlur={() => setTimeout(() => setShowDropdown(false), 200)}
          onKeyDown={handleProviderKeyDown}
          placeholder="选择或输入提供商名称"
        />
        {formErrors.provider && <div className="text-xs text-red-500 mt-0.5">{formErrors.provider}</div>}
        {showDropdown && filteredProviders.length > 0 && (
          <div className="absolute z-10 w-full mt-1 bg-white border rounded shadow-lg max-h-40 overflow-y-auto">
            {filteredProviders.map((p, i) => (
              <div
                key={p}
                className={`px-3 py-1.5 text-sm cursor-pointer ${i === highlightIndex ? 'bg-blue-50 text-blue-700' : 'hover:bg-gray-50'}`}
                onMouseDown={() => handleProviderSelect(p)}
                onMouseEnter={() => setHighlightIndex(i)}
              >
                {p}
              </div>
            ))}
          </div>
        )}
        {isNewProvider && (
          <div className="mt-1 text-xs text-amber-600">
            新提供商 "{providerInput}" 尚未创建，
            <button
              type="button"
              className="text-blue-500 hover:underline"
              onClick={() => navigate('/providers')}
            >
              前往创建
            </button>
          </div>
        )}
      </div>
      <div>
        <label className="block text-xs text-gray-500 mb-1">能力<HelpTip text="模型能力等级(0-100)，数值越高能力越强。可自动探测" /></label>
        <input className="w-full border rounded px-2 py-1 text-sm" value={form.capability} onChange={(e) => update('capability', e.target.value)} placeholder="如: 50 (0-100，可留空自动探测)" />
      </div>
      <div>
        <label className="block text-xs text-gray-500 mb-1">输入价格<HelpTip text="每百万/千token的输入价格，可留空自动探测" /></label>
        <input type="number" step="0.000001" className="w-full border rounded px-2 py-1 text-sm" value={form.price_input} onChange={(e) => update('price_input', Number(e.target.value))} placeholder="如: 0.01 (可留空自动探测)" />
      </div>
      <div>
        <label className="block text-xs text-gray-500 mb-1">输出价格<HelpTip text="每百万/千token的输出价格，可留空自动探测" /></label>
        <input type="number" step="0.000001" className="w-full border rounded px-2 py-1 text-sm" value={form.price_output} onChange={(e) => update('price_output', Number(e.target.value))} placeholder="如: 0.03 (可留空自动探测)" />
      </div>
      <div>
        <label className="block text-xs text-gray-500 mb-1">最大Token<HelpTip text="模型支持的最大上下文长度" /></label>
        <input type="number" className="w-full border rounded px-2 py-1 text-sm" value={form.max_tokens} onChange={(e) => update('max_tokens', Number(e.target.value))} placeholder="如: 128000" />
      </div>
      <div>
        <label className="block text-xs text-gray-500 mb-1">回退模型<HelpTip text="当此模型不可用时的备选模型名称" /></label>
        <input className="w-full border rounded px-2 py-1 text-sm" value={form.fallback_model} onChange={(e) => update('fallback_model', e.target.value)} placeholder="如: gpt-3.5-turbo (可选)" />
      </div>
      <div>
        <label className="block text-xs text-gray-500 mb-1">任务类型<HelpTip text="模型擅长的任务类型，逗号分隔" /></label>
        <input className="w-full border rounded px-2 py-1 text-sm" value={(form.task_types || []).join(',')} onChange={(e) => update('task_types', e.target.value.split(',').map((s) => s.trim()).filter(Boolean))} placeholder="如: chat,code,translation (可选)" />
      </div>
      <div>
        <label className="block text-xs text-gray-500 mb-1">
          模态支持<HelpTip text="模型支持的输入输出模态，可自动探测" />
        </label>
        <div className="flex flex-wrap gap-2 mt-1">
          {['text', 'image', 'audio', 'video', 'file'].map((mod) => {
            const currentMods = (form.modalities as string[]) || [];
            const isActive = currentMods.includes(mod);
            return (
              <button
                key={mod}
                type="button"
                className={`px-2 py-1 rounded text-xs border ${isActive ? 'bg-blue-100 text-blue-700 border-blue-300' : 'bg-gray-50 text-gray-500 border-gray-200 hover:bg-gray-100'}`}
                onClick={() => {
                  const newMods = isActive
                    ? currentMods.filter((m: string) => m !== mod)
                    : [...currentMods, mod];
                  update('modalities', newMods);
                }}
              >
                {mod}
              </button>
            );
          })}
          <button
            type="button"
            onClick={handleAutoDetect}
            disabled={detecting}
            className="px-2 py-1 rounded text-xs border bg-purple-50 text-purple-700 border-purple-200 hover:bg-purple-100 disabled:opacity-50"
            title="自动探测模态、标签等"
          >
            {detecting ? '探测中...' : '自动探测'}
          </button>
        </div>
      </div>
      <div className="col-span-2">
        <label className="block text-xs text-gray-500 mb-1">单价查询脚本<HelpTip text="可选，留空则使用litellm或手动设定。返回价格信息JSON" /></label>
        <textarea
          className="w-full border rounded px-2 py-1 text-sm font-mono"
          rows={3}
          value={String(form.price_script || '')}
          onChange={(e) => update('price_script', e.target.value)}
          placeholder="# 可选，留空自动获取&#10;# 返回: result = {&quot;price_input&quot;: 0.0, &quot;price_output&quot;: 0.0, &quot;price_currency&quot;: &quot;CNY&quot;}"
        />
      </div>
      <div>
        <label className="block text-xs text-gray-500 mb-1">标签<HelpTip text="模型能力标签，可自动探测或手动添加" /></label>
        <div className="flex flex-wrap gap-1 mb-1">
          {(form.capability_tags || []).map((tag) => (
            <span key={tag} className="inline-flex items-center gap-1 px-2 py-0.5 bg-blue-50 text-blue-700 rounded text-xs">
              {tag}
              <button type="button" className="text-blue-400 hover:text-blue-600" onClick={() => update('capability_tags', (form.capability_tags || []).filter((t) => t !== tag))}>x</button>
            </span>
          ))}
        </div>
        <div className="flex gap-1">
          <input
            id="tag-input"
            className="flex-1 border rounded px-2 py-1 text-sm"
            placeholder="输入标签后回车"
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                const input = e.target as HTMLInputElement;
                const val = input.value.trim();
                if (val && !(form.capability_tags || []).includes(val)) {
                  update('capability_tags', [...(form.capability_tags || []), val]);
                }
                input.value = '';
              }
            }}
          />
          <select
            className="border rounded px-1 py-1 text-xs"
            value=""
            onChange={(e) => {
              if (e.target.value && !(form.capability_tags || []).includes(e.target.value)) {
                update('capability_tags', [...(form.capability_tags || []), e.target.value]);
              }
            }}
          >
            <option value="">预置...</option>
            <option value="免费">免费</option>
            <option value="付费">付费</option>
            <option value="高能力">高能力</option>
            <option value="低成本">低成本</option>
            <option value="快速">快速</option>
            <option value="多模态">多模态</option>
            <option value="代码">代码</option>
            <option value="推理">推理</option>
            <option value="对话">对话</option>
            <option value="翻译">翻译</option>
            <option value="嵌入">嵌入</option>
            <option value="国产">国产</option>
            <option value="开源">开源</option>
          </select>
        </div>
      </div>
      <div className="flex items-end">
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={form.enabled} onChange={(e) => update('enabled', e.target.checked)} />
          启用
        </label>
      </div>
    </div>

    {/* Schedule Rules */}
    <div className="mt-4 border-t pt-4">
      <div className="flex items-center justify-between mb-2">
        <h4 className="text-sm font-medium">生效时间规则</h4>
        <button
          type="button"
          onClick={() => update('schedule_rules', [...(form.schedule_rules || []), { ...emptyScheduleRule }])}
          className="px-2 py-1 bg-gray-100 text-gray-700 rounded text-xs hover:bg-gray-200"
        >
          + 添加规则
        </button>
      </div>
      {(form.schedule_rules || []).length === 0 && (
        <p className="text-xs text-gray-400">未设置规则，模型全天候可用</p>
      )}
      {(form.schedule_rules || []).map((rule, idx) => (
        <div key={idx} className="border rounded p-3 mb-2 bg-gray-50">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium text-gray-600">规则 {idx + 1}</span>
            <button
              type="button"
              onClick={() => {
                const rules = [...(form.schedule_rules || [])];
                rules.splice(idx, 1);
                update('schedule_rules', rules);
              }}
              className="text-red-500 text-xs hover:underline"
            >
              删除
            </button>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            <div className="col-span-2">
              <label className="block text-xs text-gray-500 mb-1">时间段 (逗号分隔，如 09:00-18:00,22:00-06:00)</label>
              <input
                className="w-full border rounded px-2 py-1 text-sm"
                value={(rule.time_ranges || []).join(',')}
                onChange={(e) => {
                  const rules = [...(form.schedule_rules || [])];
                  rules[idx] = { ...rules[idx], time_ranges: e.target.value.split(',').map(s => s.trim()).filter(Boolean) };
                  update('schedule_rules', rules);
                }}
                placeholder="09:00-18:00"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">星期几 (1=周一,7=周日，逗号分隔，空=每天)</label>
              <input
                className="w-full border rounded px-2 py-1 text-sm"
                value={(rule.days_of_week || []).join(',')}
                onChange={(e) => {
                  const rules = [...(form.schedule_rules || [])];
                  rules[idx] = { ...rules[idx], days_of_week: e.target.value.split(',').map(s => parseInt(s.trim())).filter(n => !isNaN(n) && n >= 1 && n <= 7) };
                  update('schedule_rules', rules);
                }}
                placeholder="1,2,3,4,5"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">每月几号 (1-31，逗号分隔，空=每天)</label>
              <input
                className="w-full border rounded px-2 py-1 text-sm"
                value={(rule.days_of_month || []).join(',')}
                onChange={(e) => {
                  const rules = [...(form.schedule_rules || [])];
                  rules[idx] = { ...rules[idx], days_of_month: e.target.value.split(',').map(s => parseInt(s.trim())).filter(n => !isNaN(n) && n >= 1 && n <= 31) };
                  update('schedule_rules', rules);
                }}
                placeholder="1,15"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">开始日期 (YYYY-MM-DD)</label>
              <input
                type="date"
                className="w-full border rounded px-2 py-1 text-sm"
                value={rule.start_date || ''}
                onChange={(e) => {
                  const rules = [...(form.schedule_rules || [])];
                  rules[idx] = { ...rules[idx], start_date: e.target.value };
                  update('schedule_rules', rules);
                }}
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">结束日期 (YYYY-MM-DD)</label>
              <input
                type="date"
                className="w-full border rounded px-2 py-1 text-sm"
                value={rule.end_date || ''}
                onChange={(e) => {
                  const rules = [...(form.schedule_rules || [])];
                  rules[idx] = { ...rules[idx], end_date: e.target.value };
                  update('schedule_rules', rules);
                }}
              />
            </div>
            <div className="col-span-2">
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={rule.include_holidays !== false}
                  onChange={(e) => {
                    const rules = [...(form.schedule_rules || [])];
                    rules[idx] = { ...rules[idx], include_holidays: e.target.checked };
                    update('schedule_rules', rules);
                  }}
                />
                节假日也生效（取消勾选则节假日不激活）
              </label>
            </div>
          </div>
        </div>
      ))}
    </div>
    </>
  );
}
