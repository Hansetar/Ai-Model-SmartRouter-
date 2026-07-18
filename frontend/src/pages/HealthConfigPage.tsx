import { useState, useEffect } from 'react';
import { createApi } from '../api';

interface HealthConfigPageProps {
  token: string;
}

export default function HealthConfigPage({ token }: HealthConfigPageProps) {
  const api = createApi(token);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const [healthConfig, setHealthConfig] = useState({
    check_interval: 60,
    timeout: 10,
    unhealthy_threshold: 3,
    healthy_threshold: 2,
  });

  useEffect(() => {
    loadConfig();
  }, []);

  const loadConfig = async () => {
    setLoading(true);
    try {
      const result = await api.getConfig();
      const hc = result.health_check as Record<string, unknown> | undefined;
      if (hc) {
        setHealthConfig({
          check_interval: Number(hc.check_interval || 60),
          timeout: Number(hc.timeout || 10),
          unhealthy_threshold: Number(hc.unhealthy_threshold || 3),
          healthy_threshold: Number(hc.healthy_threshold || 2),
        });
      }
    } catch (err) {
      console.error('Failed to load config:', err);
    } finally {
      setLoading(false);
    }
  };

  const saveHealthConfig = async () => {
    setSaving(true);
    try {
      await api.updateHealthCheckConfig(healthConfig);
      alert('健康检查配置已保存');
    } catch (err) {
      alert('保存失败');
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <div className="text-gray-500">加载中...</div>;

  return (
    <div>
      <h2 className="text-2xl font-bold mb-6">健康检查配置</h2>

      <div className="bg-white rounded-lg shadow p-4 mb-6">
        <h3 className="font-semibold mb-3">检查参数</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div>
            <label className="block text-xs text-gray-500 mb-1">检查间隔(秒)</label>
            <input
              type="number"
              className="w-full border rounded px-2 py-1 text-sm"
              value={healthConfig.check_interval}
              onChange={(e) => setHealthConfig({ ...healthConfig, check_interval: Number(e.target.value) })}
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">超时(秒)</label>
            <input
              type="number"
              className="w-full border rounded px-2 py-1 text-sm"
              value={healthConfig.timeout}
              onChange={(e) => setHealthConfig({ ...healthConfig, timeout: Number(e.target.value) })}
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">不健康阈值</label>
            <input
              type="number"
              className="w-full border rounded px-2 py-1 text-sm"
              value={healthConfig.unhealthy_threshold}
              onChange={(e) => setHealthConfig({ ...healthConfig, unhealthy_threshold: Number(e.target.value) })}
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">健康阈值</label>
            <input
              type="number"
              className="w-full border rounded px-2 py-1 text-sm"
              value={healthConfig.healthy_threshold}
              onChange={(e) => setHealthConfig({ ...healthConfig, healthy_threshold: Number(e.target.value) })}
            />
          </div>
        </div>
        <button
          onClick={saveHealthConfig}
          disabled={saving}
          className="mt-3 px-4 py-2 bg-blue-500 text-white rounded text-sm hover:bg-blue-600 disabled:opacity-50"
        >
          {saving ? '保存中...' : '保存健康检查配置'}
        </button>
      </div>
    </div>
  );
}
