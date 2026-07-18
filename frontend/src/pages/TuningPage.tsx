import { useState, useEffect, useRef } from 'react';
import { createApi } from '../api';
import { useToast } from '../components/Toast';

interface TuningPageProps {
  token: string;
}

export default function TuningPage({ token }: TuningPageProps) {
  const api = createApi(token);
  const toast = useToast();
  const [status, setStatus] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const [rlParams, setRlParams] = useState({
    learning_rate: 0.1,
    exploration_rate: 0.2,
    discount_factor: 0.9,
  });
  const [autoTune, setAutoTune] = useState(false);
  const [retraining, setRetraining] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [retrainProgress, setRetrainProgress] = useState<string>('');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    loadStatus();
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, []);

  const loadStatus = async () => {
    setLoading(true);
    try {
      const result = await api.getTuningStatus();
      setStatus(result);
      const rl = result.rl_params as Record<string, unknown> | undefined;
      if (rl) {
        setRlParams({
          learning_rate: Number(rl.learning_rate || 0.1),
          exploration_rate: Number(rl.exploration_rate || 0.2),
          discount_factor: Number(rl.discount_factor || 0.9),
        });
      }
      // auto_tune_enabled is inside the 'ml' sub-object returned by getTuningStatus
      const ml = result.ml as Record<string, unknown> | undefined;
      setAutoTune(Boolean(ml?.auto_tune_enabled));
    } catch (err) {
      console.error('Failed to load tuning status:', err);
    } finally {
      setLoading(false);
    }
  };

  const pollRetrainStatus = (taskId: string) => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const result = await api.getRetrainStatus(taskId);
        setRetrainProgress(result.progress || '');
        if (result.status === 'completed') {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          setRetraining(false);
          setRetrainProgress('');
          toast.addToast('重训练完成', 'success', `训练结果: ${JSON.stringify(result.result || {})}`);
          await loadStatus();
        } else if (result.status === 'failed') {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          setRetraining(false);
          setRetrainProgress('');
          toast.addToast('重训练失败', 'error', result.error || '未知错误');
        }
      } catch {
        if (pollRef.current) clearInterval(pollRef.current);
        pollRef.current = null;
        setRetraining(false);
        setRetrainProgress('');
      }
    }, 2000);
  };

  const handleRetrain = async () => {
    setRetraining(true);
    setRetrainProgress('启动中...');
    try {
      const result = await api.triggerRetrain();
      if (result.task_id) {
        setRetrainProgress('后台训练中...');
        toast.addToast('重训练已启动', 'info', '训练在后台进行，请稍候');
        pollRetrainStatus(result.task_id);
      } else {
        setRetraining(false);
        toast.addToast('重训练完成', 'success');
        await loadStatus();
      }
    } catch (err) {
      setRetraining(false);
      setRetrainProgress('');
      toast.addToast('触发重训练失败', 'error');
    }
  };

  const handleUpdateRLParams = async () => {
    try {
      await api.updateRLParams(rlParams);
      toast.addToast('RL参数已更新', 'success');
      await loadStatus();
    } catch (err) {
      toast.addToast('更新RL参数失败', 'error');
    }
  };

  const handleToggleAutoTune = async () => {
    try {
      const res = await api.toggleAutoTune(!autoTune);
      setAutoTune(res.auto_tune_enabled ?? !autoTune);
      toast.addToast(res.auto_tune_enabled ? '自动调优已开启' : '自动调优已关闭', 'success');
    } catch (err) {
      toast.addToast('切换自动调优失败', 'error');
    }
  };

  const handleReset = async () => {
    if (!confirm('确定重置所有模型？此操作不可恢复！')) return;
    setResetting(true);
    try {
      await api.resetModels();
      toast.addToast('模型已重置', 'success');
      await loadStatus();
    } catch (err) {
      toast.addToast('重置模型失败', 'error');
    } finally {
      setResetting(false);
    }
  };

  if (loading) return <div className="text-gray-500">加载中...</div>;

  const ml = (status?.ml as Record<string, unknown>) || {};
  const rlPolicy = (status?.rl_policy as Record<string, unknown>) || {};
  const qTable = (rlPolicy.policy as Record<string, Record<string, number>>) || {};
  const taskTypes = Object.keys(qTable);
  const modelNames = taskTypes.length > 0 ? Object.keys(qTable[taskTypes[0]!] || {}) : [];

  return (
    <div>
      <h2 className="text-2xl font-bold mb-6">模型调优</h2>

      {/* ML Status Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
        <div className="bg-white rounded-lg shadow p-4">
          <div className="text-sm text-gray-500">ML 就绪</div>
          <div className={`text-2xl font-bold mt-1 ${ml.is_ready ? 'text-green-600' : 'text-red-600'}`}>
            {ml.is_ready ? '是' : '否'}
          </div>
        </div>
        <div className="bg-white rounded-lg shadow p-4">
          <div className="text-sm text-gray-500">训练样本数</div>
          <div className="text-2xl font-bold mt-1 text-blue-600">{String(ml.total_trained || 0)}</div>
        </div>
        <div className="bg-white rounded-lg shadow p-4">
          <div className="text-sm text-gray-500">ONNX 状态</div>
          <div className={`text-2xl font-bold mt-1 ${ml.has_onnx ? 'text-green-600' : 'text-gray-400'}`}>
            {ml.has_onnx ? '已加载' : '未加载'}
          </div>
        </div>
      </div>

      {/* Retrain Progress */}
      {retraining && retrainProgress && (
        <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-6">
          <div className="flex items-center gap-2">
            <div className="animate-spin h-4 w-4 border-2 border-blue-500 border-t-transparent rounded-full" />
            <span className="text-blue-700 text-sm font-medium">后台训练中: {retrainProgress}</span>
          </div>
        </div>
      )}

      {/* Q-Value Table */}
      <div className="bg-white rounded-lg shadow p-4 mb-6">
        <h3 className="font-semibold mb-3">RL 策略详情（Q 值表）</h3>
        {taskTypes.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-3 py-2 text-left">任务类型 \\ 模型</th>
                  {modelNames.map((m) => (
                    <th key={m} className="px-3 py-2 text-left">{m}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {taskTypes.map((t) => (
                  <tr key={t} className="border-b">
                    <td className="px-3 py-2 font-medium">{t}</td>
                    {modelNames.map((m) => (
                      <td key={m} className="px-3 py-2">
                        {qTable[t]?.[m] !== undefined ? Number(qTable[t][m]).toFixed(3) : '-'}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="text-gray-400 text-sm">暂无 Q 值数据</div>
        )}
      </div>

      {/* RL Parameters */}
      <div className="bg-white rounded-lg shadow p-4 mb-6">
        <h3 className="font-semibold mb-3">RL 参数调整</h3>
        <div className="space-y-4">
          <div>
            <label className="flex items-center justify-between text-sm">
              <span>学习率</span>
              <span className="text-blue-600 font-medium">{rlParams.learning_rate.toFixed(3)}</span>
            </label>
            <input
              type="range"
              min="0.001"
              max="1"
              step="0.001"
              value={rlParams.learning_rate}
              onChange={(e) => setRlParams({ ...rlParams, learning_rate: Number(e.target.value) })}
              className="w-full"
            />
          </div>
          <div>
            <label className="flex items-center justify-between text-sm">
              <span>探索率</span>
              <span className="text-blue-600 font-medium">{rlParams.exploration_rate.toFixed(3)}</span>
            </label>
            <input
              type="range"
              min="0"
              max="1"
              step="0.001"
              value={rlParams.exploration_rate}
              onChange={(e) => setRlParams({ ...rlParams, exploration_rate: Number(e.target.value) })}
              className="w-full"
            />
          </div>
          <div>
            <label className="flex items-center justify-between text-sm">
              <span>折扣因子</span>
              <span className="text-blue-600 font-medium">{rlParams.discount_factor.toFixed(3)}</span>
            </label>
            <input
              type="range"
              min="0"
              max="1"
              step="0.001"
              value={rlParams.discount_factor}
              onChange={(e) => setRlParams({ ...rlParams, discount_factor: Number(e.target.value) })}
              className="w-full"
            />
          </div>
          <button
            onClick={handleUpdateRLParams}
            className="px-4 py-2 bg-blue-500 text-white rounded text-sm hover:bg-blue-600"
          >
            保存参数
          </button>
        </div>
      </div>

      {/* Actions */}
      <div className="bg-white rounded-lg shadow p-4">
        <h3 className="font-semibold mb-3">操作</h3>
        <div className="flex flex-wrap gap-3">
          <button
            onClick={handleRetrain}
            disabled={retraining}
            className="px-4 py-2 bg-green-500 text-white rounded text-sm hover:bg-green-600 disabled:opacity-50"
          >
            {retraining ? '训练中...' : '手动触发重训练'}
          </button>
          <button
            onClick={handleToggleAutoTune}
            className={`px-4 py-2 rounded text-sm ${autoTune ? 'bg-orange-500 text-white hover:bg-orange-600' : 'bg-gray-200 text-gray-700 hover:bg-gray-300'}`}
          >
            自动调优: {autoTune ? '已开启' : '已关闭'}
          </button>
          <button
            onClick={handleReset}
            disabled={resetting}
            className="px-4 py-2 bg-red-500 text-white rounded text-sm hover:bg-red-600 disabled:opacity-50"
          >
            {resetting ? '重置中...' : '重置模型'}
          </button>
        </div>
      </div>
    </div>
  );
}
