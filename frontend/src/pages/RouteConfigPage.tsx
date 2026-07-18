import { useState, useEffect } from 'react';
import { createApi } from '../api';

interface RouteConfigPageProps {
  token: string;
}

export default function RouteConfigPage({ token }: RouteConfigPageProps) {
  const api = createApi(token);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);

  // Route weights form
  const [routeWeights, setRouteWeights] = useState({
    predictor_weight: 0.5,
    score_weight: 0.5,
    fusion_weight: 0.5,
  });

  // RL config form
  const [rlConfig, setRlConfig] = useState({
    learning_rate: 0.1,
    exploration_rate: 0.2,
    discount_factor: 0.9,
    retrain_interval: 100,
    min_samples: 50,
  });

  // Health check config form
  const [healthConfig, setHealthConfig] = useState({
    check_interval: 60,
    timeout: 10,
    unhealthy_threshold: 3,
    healthy_threshold: 2,
  });

  // Route strategy form
  const [routeStrategy, setRouteStrategy] = useState({
    mode: 'difficulty_match',  // difficulty_match / cost_first / quality_first / custom
    priority_order: 'modality,balance,tag,difficulty,price',  // 逗号分隔的优先级顺序
    cost_weight: 0.3,
    quality_weight: 0.7,
    price_ceiling: 0,  // 0=不限
    time_based_rules: '',  // JSON格式的时段规则
  });

  useEffect(() => {
    loadConfig();
  }, []);

  const loadConfig = async () => {
    setLoading(true);
    try {
      const result = await api.getConfig();
      const rw = result.route_weights as Record<string, unknown> | undefined;
      if (rw) {
        setRouteWeights({
          predictor_weight: Number(rw.predictor_weight || 0.5),
          score_weight: Number(rw.score_weight || 0.5),
          fusion_weight: Number(rw.fusion_weight || 0.5),
        });
      }
      const rl = result.rl_config as Record<string, unknown> | undefined;
      if (rl) {
        setRlConfig({
          learning_rate: Number(rl.learning_rate || 0.1),
          exploration_rate: Number(rl.exploration_rate || 0.2),
          discount_factor: Number(rl.discount_factor || 0.9),
          retrain_interval: Number(rl.retrain_interval || 100),
          min_samples: Number(rl.min_samples || 50),
        });
      }
      const hc = result.health_check as Record<string, unknown> | undefined;
      if (hc) {
        setHealthConfig({
          check_interval: Number(hc.check_interval || 60),
          timeout: Number(hc.timeout || 10),
          unhealthy_threshold: Number(hc.unhealthy_threshold || 3),
          healthy_threshold: Number(hc.healthy_threshold || 2),
        });
      }
      const rs = result.route_strategy as Record<string, unknown> | undefined;
      if (rs) {
        setRouteStrategy({
          mode: String(rs.mode || 'difficulty_match'),
          priority_order: String(rs.priority_order || 'modality,balance,tag,difficulty,price'),
          cost_weight: Number(rs.cost_weight || 0.3),
          quality_weight: Number(rs.quality_weight || 0.7),
          price_ceiling: Number(rs.price_ceiling || 0),
          time_based_rules: String(rs.time_based_rules || ''),
        });
      }
    } catch (err) {
      console.error('Failed to load config:', err);
    } finally {
      setLoading(false);
    }
  };

  const saveRouteWeights = async () => {
    setSaving('route');
    try {
      await api.updateRouteWeights(routeWeights);
      alert('路由权重已保存');
    } catch (err) {
      alert('保存路由权重失败');
    } finally {
      setSaving(null);
    }
  };

  const saveRLConfig = async () => {
    setSaving('rl');
    try {
      await api.updateRLConfig(rlConfig);
      alert('RL配置已保存');
    } catch (err) {
      alert('保存RL配置失败');
    } finally {
      setSaving(null);
    }
  };

  const saveHealthConfig = async () => {
    setSaving('health');
    try {
      await api.updateHealthCheckConfig(healthConfig);
      alert('健康检查配置已保存');
    } catch (err) {
      alert('保存健康检查配置失败');
    } finally {
      setSaving(null);
    }
  };

  const saveRouteStrategy = async () => {
    setSaving('strategy');
    try {
      await api.updateBasicSettings({ route_strategy: routeStrategy });
      alert('路由策略已保存');
    } catch (err) {
      alert('保存路由策略失败');
    } finally {
      setSaving(null);
    }
  };

  if (loading) return <div className="text-gray-500">加载中...</div>;

  return (
    <div>
      <h2 className="text-2xl font-bold mb-6">路由配置</h2>

      {/* Route Strategy */}
      <div className="bg-white rounded-lg shadow p-4 mb-6">
        <h3 className="font-semibold mb-3">路由策略</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="block text-xs text-gray-500 mb-1">策略模式</label>
            <select
              className="w-full border rounded px-2 py-1 text-sm"
              value={routeStrategy.mode}
              onChange={(e) => setRouteStrategy({ ...routeStrategy, mode: e.target.value })}
            >
              <option value="difficulty_match">难度匹配（简单任务用便宜模型，复杂任务用强模型）</option>
              <option value="cost_first">成本优先（优先免费/便宜模型）</option>
              <option value="quality_first">质量优先（优先高能力模型）</option>
              <option value="custom">自定义（手动配置优先级）</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">筛选优先级顺序（逗号分隔）</label>
            <input
              className="w-full border rounded px-2 py-1 text-sm"
              value={routeStrategy.priority_order}
              onChange={(e) => setRouteStrategy({ ...routeStrategy, priority_order: e.target.value })}
              placeholder="modality,balance,tag,difficulty,price"
            />
            <div className="text-xs text-gray-400 mt-1">可选: modality, balance, tag, difficulty, price</div>
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">成本权重 (0-1)</label>
            <input
              type="number"
              step="0.01"
              min="0"
              max="1"
              className="w-full border rounded px-2 py-1 text-sm"
              value={routeStrategy.cost_weight}
              onChange={(e) => setRouteStrategy({ ...routeStrategy, cost_weight: Number(e.target.value) })}
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">质量权重 (0-1)</label>
            <input
              type="number"
              step="0.01"
              min="0"
              max="1"
              className="w-full border rounded px-2 py-1 text-sm"
              value={routeStrategy.quality_weight}
              onChange={(e) => setRouteStrategy({ ...routeStrategy, quality_weight: Number(e.target.value) })}
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">价格上限 (0=不限)</label>
            <input
              type="number"
              step="0.01"
              min="0"
              className="w-full border rounded px-2 py-1 text-sm"
              value={routeStrategy.price_ceiling}
              onChange={(e) => setRouteStrategy({ ...routeStrategy, price_ceiling: Number(e.target.value) })}
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">时段规则 (JSON)</label>
            <textarea
              className="w-full border rounded px-2 py-1 text-sm font-mono"
              rows={2}
              value={routeStrategy.time_based_rules}
              onChange={(e) => setRouteStrategy({ ...routeStrategy, time_based_rules: e.target.value })}
              placeholder='[{"hours":"18:00-9:00","mode":"cost_first"}]'
            />
          </div>
        </div>
        <button
          onClick={saveRouteStrategy}
          disabled={saving === 'strategy'}
          className="mt-3 px-4 py-2 bg-blue-500 text-white rounded text-sm hover:bg-blue-600 disabled:opacity-50"
        >
          {saving === 'strategy' ? '保存中...' : '保存路由策略'}
        </button>
      </div>

      {/* Route Weights */}
      <div className="bg-white rounded-lg shadow p-4 mb-6">
        <h3 className="font-semibold mb-3">路由权重</h3>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div>
            <label className="block text-xs text-gray-500 mb-1">预测器权重</label>
            <input
              type="number"
              step="0.01"
              min="0"
              max="1"
              className="w-full border rounded px-2 py-1 text-sm"
              value={routeWeights.predictor_weight}
              onChange={(e) => setRouteWeights({ ...routeWeights, predictor_weight: Number(e.target.value) })}
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">评分权重</label>
            <input
              type="number"
              step="0.01"
              min="0"
              max="1"
              className="w-full border rounded px-2 py-1 text-sm"
              value={routeWeights.score_weight}
              onChange={(e) => setRouteWeights({ ...routeWeights, score_weight: Number(e.target.value) })}
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">融合权重</label>
            <input
              type="number"
              step="0.01"
              min="0"
              max="1"
              className="w-full border rounded px-2 py-1 text-sm"
              value={routeWeights.fusion_weight}
              onChange={(e) => setRouteWeights({ ...routeWeights, fusion_weight: Number(e.target.value) })}
            />
          </div>
        </div>
        <button
          onClick={saveRouteWeights}
          disabled={saving === 'route'}
          className="mt-3 px-4 py-2 bg-blue-500 text-white rounded text-sm hover:bg-blue-600 disabled:opacity-50"
        >
          {saving === 'route' ? '保存中...' : '保存路由权重'}
        </button>
      </div>

      {/* RL Config */}
      <div className="bg-white rounded-lg shadow p-4 mb-6">
        <h3 className="font-semibold mb-3">RL 配置</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          <div>
            <label className="block text-xs text-gray-500 mb-1">学习率</label>
            <input
              type="number"
              step="0.001"
              min="0"
              max="1"
              className="w-full border rounded px-2 py-1 text-sm"
              value={rlConfig.learning_rate}
              onChange={(e) => setRlConfig({ ...rlConfig, learning_rate: Number(e.target.value) })}
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">探索率</label>
            <input
              type="number"
              step="0.001"
              min="0"
              max="1"
              className="w-full border rounded px-2 py-1 text-sm"
              value={rlConfig.exploration_rate}
              onChange={(e) => setRlConfig({ ...rlConfig, exploration_rate: Number(e.target.value) })}
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">折扣因子</label>
            <input
              type="number"
              step="0.001"
              min="0"
              max="1"
              className="w-full border rounded px-2 py-1 text-sm"
              value={rlConfig.discount_factor}
              onChange={(e) => setRlConfig({ ...rlConfig, discount_factor: Number(e.target.value) })}
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">重训练间隔</label>
            <input
              type="number"
              className="w-full border rounded px-2 py-1 text-sm"
              value={rlConfig.retrain_interval}
              onChange={(e) => setRlConfig({ ...rlConfig, retrain_interval: Number(e.target.value) })}
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">最小样本数</label>
            <input
              type="number"
              className="w-full border rounded px-2 py-1 text-sm"
              value={rlConfig.min_samples}
              onChange={(e) => setRlConfig({ ...rlConfig, min_samples: Number(e.target.value) })}
            />
          </div>
        </div>
        <button
          onClick={saveRLConfig}
          disabled={saving === 'rl'}
          className="mt-3 px-4 py-2 bg-blue-500 text-white rounded text-sm hover:bg-blue-600 disabled:opacity-50"
        >
          {saving === 'rl' ? '保存中...' : '保存RL配置'}
        </button>
      </div>

      {/* Health Check Config */}
      <div className="bg-white rounded-lg shadow p-4">
        <h3 className="font-semibold mb-3">健康检查配置</h3>
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
          disabled={saving === 'health'}
          className="mt-3 px-4 py-2 bg-blue-500 text-white rounded text-sm hover:bg-blue-600 disabled:opacity-50"
        >
          {saving === 'health' ? '保存中...' : '保存健康检查配置'}
        </button>
      </div>
    </div>
  );
}
