import { useState, useEffect } from 'react';
import { createApi } from '../api';

interface HealthPageProps {
  token: string;
}

export default function HealthPage({ token }: HealthPageProps) {
  const api = createApi(token);
  const [health, setHealth] = useState<Record<string, string>>({});
  const [routingStatus, setRoutingStatus] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setLoading(true);
    try {
      const [healthResult, routingResult] = await Promise.all([
        api.getModelsHealth(),
        api.getRoutingStatus(),
      ]);
      setHealth(healthResult.models || {});
      setRoutingStatus(routingResult);
    } catch (err) {
      console.error('Failed to load health data:', err);
    } finally {
      setLoading(false);
    }
  };

  const statusColor = (status: string) => {
    switch (status) {
      case 'healthy': return 'bg-green-100 text-green-700';
      case 'degraded': return 'bg-yellow-100 text-yellow-700';
      case 'unhealthy': return 'bg-red-100 text-red-700';
      default: return 'bg-gray-100 text-gray-700';
    }
  };

  const statusLabel = (status: string) => {
    switch (status) {
      case 'healthy': return '健康';
      case 'degraded': return '降级';
      case 'unhealthy': return '不可用';
      default: return status;
    }
  };

  if (loading) return <div className="text-gray-500">加载中...</div>;

  const ml = (routingStatus?.ml as Record<string, unknown>) || {};

  return (
    <div>
      <h2 className="text-2xl font-bold mb-6">健康检查</h2>

      {/* Model health */}
      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <h3 className="font-semibold mb-4">模型健康状态</h3>
        <div className="space-y-2">
          {Object.entries(health).map(([name, status]) => (
            <div key={name} className="flex items-center justify-between py-2 border-b last:border-0">
              <span className="font-medium">{name}</span>
              <span className={`px-2 py-0.5 rounded text-xs ${statusColor(status)}`}>
                {statusLabel(status)}
              </span>
            </div>
          ))}
          {Object.keys(health).length === 0 && (
            <div className="text-gray-400 text-sm">暂无健康检查数据</div>
          )}
        </div>
      </div>

      {/* Routing engine status */}
      {routingStatus && (
        <div className="bg-white rounded-lg shadow p-6">
          <h3 className="font-semibold mb-4">路由引擎状态</h3>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <div className="bg-gray-50 rounded p-3">
              <div className="text-xs text-gray-500">ML就绪</div>
              <div className={`text-lg font-bold mt-1 ${ml.is_ready ? 'text-green-600' : 'text-red-600'}`}>
                {ml.is_ready ? '是' : '否'}
              </div>
            </div>
            <div className="bg-gray-50 rounded p-3">
              <div className="text-xs text-gray-500">ONNX加载</div>
              <div className={`text-lg font-bold mt-1 ${ml.has_onnx ? 'text-green-600' : 'text-gray-400'}`}>
                {ml.has_onnx ? '是' : '否'}
              </div>
            </div>
            <div className="bg-gray-50 rounded p-3">
              <div className="text-xs text-gray-500">训练样本</div>
              <div className="text-lg font-bold mt-1 text-blue-600">{String(ml.total_trained || 0)}</div>
            </div>
            <div className="bg-gray-50 rounded p-3">
              <div className="text-xs text-gray-500">RL策略数</div>
              <div className="text-lg font-bold mt-1 text-blue-600">{String(ml.rl_policy_size || 0)}</div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
