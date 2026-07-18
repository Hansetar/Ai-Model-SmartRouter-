import { useState, useEffect } from 'react';
import { createApi } from '../api';

interface ConfigPageProps {
  token: string;
}

interface ConfigInfo {
  config_path: string;
  config_exists: boolean;
  config_size: number;
  config_modified_at: string;
  env_overrides: string[];
  database: {
    backend: string;
    url: string;
    path: string | null;
  };
  models_dir: string;
  models_dir_exists: boolean;
  currency: string;
  total_models: number;
  total_providers: number;
  total_tenants: number;
}

export default function ConfigPage({ token }: ConfigPageProps) {
  const api = createApi(token);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [configInfo, setConfigInfo] = useState<ConfigInfo | null>(null);

  const [form, setForm] = useState({
    currency: 'CNY',
    default_model: '',
    fallback_model: '',
    cache_ttl_seconds: 300,
    log_retention_days: 90,
  });

  useEffect(() => {
    loadConfig();
    loadConfigInfo();
  }, []);

  const loadConfig = async () => {
    setLoading(true);
    try {
      const result = await api.getConfig();
      setForm({
        currency: String(result.currency || 'CNY'),
        default_model: String(result.default_model || ''),
        fallback_model: String(result.fallback_model || ''),
        cache_ttl_seconds: Number(result.cache_ttl_seconds || 300),
        log_retention_days: Number(result.log_retention_days || 90),
      });
    } catch (err) {
      console.error('Failed to load config:', err);
    } finally {
      setLoading(false);
    }
  };

  const loadConfigInfo = async () => {
    try {
      const result = await api.getConfigInfo();
      setConfigInfo(result as ConfigInfo);
    } catch (err) {
      console.error('Failed to load config info:', err);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.updateBasicSettings(form);
      alert('基本设置已保存');
    } catch (err) {
      alert('保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleReload = async () => {
    try {
      await api.reloadConfig();
      await loadConfig();
      await loadConfigInfo();
      alert('配置已重新加载');
    } catch (err) {
      alert('重新加载失败');
    }
  };

  const formatFileSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  if (loading) return <div className="text-gray-500">加载中...</div>;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold">基本设置</h2>
        <button onClick={handleReload} className="px-4 py-2 bg-gray-200 text-gray-700 rounded hover:bg-gray-300 text-sm">
          重新加载配置
        </button>
      </div>

      {/* Configuration file info */}
      {configInfo && (
        <div className="bg-white rounded-lg shadow p-6 mb-6">
          <h3 className="font-semibold mb-4">配置文件信息</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 text-sm">
            <div>
              <span className="text-gray-500">配置文件路径</span>
              <div className="font-mono text-gray-800 mt-1 break-all">{configInfo.config_path}</div>
            </div>
            <div>
              <span className="text-gray-500">文件状态</span>
              <div className="mt-1">
                {configInfo.config_exists ? (
                  <span className="text-green-600">存在</span>
                ) : (
                  <span className="text-red-600">不存在</span>
                )}
              </div>
            </div>
            <div>
              <span className="text-gray-500">文件大小</span>
              <div className="mt-1">{formatFileSize(configInfo.config_size)}</div>
            </div>
            <div>
              <span className="text-gray-500">最后修改</span>
              <div className="mt-1">{configInfo.config_modified_at || '未知'}</div>
            </div>
            <div>
              <span className="text-gray-500">数据库类型</span>
              <div className="mt-1">{configInfo.database.backend}</div>
            </div>
            <div>
              <span className="text-gray-500">数据库路径</span>
              <div className="font-mono mt-1 break-all">{configInfo.database.path || configInfo.database.url}</div>
            </div>
            <div>
              <span className="text-gray-500">模型文件目录</span>
              <div className="mt-1">
                <span className="font-mono break-all">{configInfo.models_dir}</span>
                {configInfo.models_dir_exists ? (
                  <span className="ml-2 text-green-600">存在</span>
                ) : (
                  <span className="ml-2 text-red-600">不存在</span>
                )}
              </div>
            </div>
            <div>
              <span className="text-gray-500">环境变量覆盖</span>
              <div className="mt-1">
                {configInfo.env_overrides.length > 0 ? (
                  <div className="space-y-1">
                    {configInfo.env_overrides.map((key) => (
                      <div key={key} className="text-yellow-600 font-mono text-xs">{key}</div>
                    ))}
                  </div>
                ) : (
                  <span className="text-gray-400">无</span>
                )}
              </div>
            </div>
            <div>
              <span className="text-gray-500">统计</span>
              <div className="mt-1">
                模型: {configInfo.total_models} | 供应商: {configInfo.total_providers} | 租户: {configInfo.total_tenants}
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="bg-white rounded-lg shadow p-6">
        <h3 className="font-semibold mb-4">基本设置</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          <div>
            <label className="block text-xs text-gray-500 mb-1">货币</label>
            <select
              className="w-full border rounded px-2 py-1 text-sm"
              value={form.currency}
              onChange={(e) => setForm({ ...form, currency: e.target.value })}
            >
              <option value="CNY">CNY (人民币)</option>
              <option value="USD">USD (美元)</option>
              <option value="EUR">EUR (欧元)</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">默认模型</label>
            <input
              className="w-full border rounded px-2 py-1 text-sm"
              value={form.default_model}
              onChange={(e) => setForm({ ...form, default_model: e.target.value })}
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">回退模型</label>
            <input
              className="w-full border rounded px-2 py-1 text-sm"
              value={form.fallback_model}
              onChange={(e) => setForm({ ...form, fallback_model: e.target.value })}
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">缓存TTL(秒)</label>
            <input
              type="number"
              className="w-full border rounded px-2 py-1 text-sm"
              value={form.cache_ttl_seconds}
              onChange={(e) => setForm({ ...form, cache_ttl_seconds: Number(e.target.value) })}
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">日志保留天数</label>
            <input
              type="number"
              className="w-full border rounded px-2 py-1 text-sm"
              value={form.log_retention_days}
              onChange={(e) => setForm({ ...form, log_retention_days: Number(e.target.value) })}
            />
          </div>
        </div>
        <button
          onClick={handleSave}
          disabled={saving}
          className="mt-4 px-4 py-2 bg-blue-500 text-white rounded text-sm hover:bg-blue-600 disabled:opacity-50"
        >
          {saving ? '保存中...' : '保存设置'}
        </button>
      </div>
    </div>
  );
}
