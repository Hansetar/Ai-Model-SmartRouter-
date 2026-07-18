import { useState, useEffect } from 'react';
import { createApi } from '../api';
import { useToast } from '../components/Toast';

interface TrainingSamplesPageProps {
  token: string;
}

interface SampleData {
  id: string;
  prompt: string;
  difficulty: number;
  token_count: number;
  task_type: string;
  model_name: string;
  source: string;
  [key: string]: unknown;
}

const emptySample: Partial<SampleData> = {
  prompt: '',
  difficulty: 0,
  token_count: 0,
  task_type: '',
  model_name: '',
  source: 'manual',
};

const emptyBatchEdit = {
  difficulty: '' as string | number,
  task_type: '',
  model_name: '',
  source: '',
};

export default function TrainingSamplesPage({ token }: TrainingSamplesPageProps) {
  const api = createApi(token);
  const toast = useToast();
  const [samples, setSamples] = useState<SampleData[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [filterSource, setFilterSource] = useState('');
  const [filterTaskType, setFilterTaskType] = useState('');
  const [showForm, setShowForm] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [form, setForm] = useState<Partial<SampleData>>({ ...emptySample });
  const pageSize = 20;

  // Batch selection state
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [showBatchEdit, setShowBatchEdit] = useState(false);
  const [batchEditForm, setBatchEditForm] = useState({ ...emptyBatchEdit });
  const [batchOperating, setBatchOperating] = useState(false);

  useEffect(() => {
    loadSamples();
  }, [page, filterSource, filterTaskType]);

  // Clear selection when page or filters change
  useEffect(() => {
    setSelectedIds(new Set());
  }, [page, filterSource, filterTaskType]);

  const loadSamples = async () => {
    setLoading(true);
    try {
      const params: Record<string, unknown> = { page, page_size: pageSize };
      if (filterSource) params.source = filterSource;
      if (filterTaskType) params.task_type = filterTaskType;
      const result = await api.listTrainingSamples(params);
      setSamples(result.samples || result.items || []);
      setTotal(result.total || 0);
    } catch (err) {
      console.error('Failed to load samples:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = async () => {
    try {
      await api.createTrainingSample(form as Record<string, unknown>);
      setShowForm(false);
      setForm({ ...emptySample });
      await loadSamples();
    } catch (err) {
      toast.addToast('新增样本失败', 'error');
    }
  };

  const handleUpdate = async () => {
    if (!editId) return;
    try {
      await api.updateTrainingSample(editId, form as Record<string, unknown>);
      setEditId(null);
      setForm({ ...emptySample });
      await loadSamples();
    } catch (err) {
      toast.addToast('更新样本失败', 'error');
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm('确定删除此样本?')) return;
    try {
      await api.deleteTrainingSample(id);
      await loadSamples();
    } catch (err) {
      toast.addToast('删除失败', 'error');
    }
  };

  const startEdit = (s: SampleData) => {
    setEditId(s.id);
    setForm({ ...s });
    setShowForm(true);
  };

  // Batch operations
  const toggleSelect = (id: number) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (selectedIds.size === samples.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(samples.map(s => Number(s.id))));
    }
  };

  const handleBatchDelete = async () => {
    if (selectedIds.size === 0) return;
    if (!confirm(`确定删除选中的 ${selectedIds.size} 条样本?`)) return;
    setBatchOperating(true);
    try {
      const result = await api.batchDeleteTrainingSamples([...selectedIds]);
      toast.addToast(`已删除 ${result.deleted || selectedIds.size} 条样本`, 'success');
      setSelectedIds(new Set());
      await loadSamples();
    } catch (err) {
      toast.addToast('批量删除失败', 'error');
    } finally {
      setBatchOperating(false);
    }
  };

  const handleBatchUpdate = async () => {
    if (selectedIds.size === 0) return;
    const updates: Record<string, unknown> = {};
    if (batchEditForm.difficulty !== '') updates.difficulty = Number(batchEditForm.difficulty);
    if (batchEditForm.task_type) updates.task_type = batchEditForm.task_type;
    if (batchEditForm.model_name) updates.model_name = batchEditForm.model_name;
    if (batchEditForm.source) updates.source = batchEditForm.source;

    if (Object.keys(updates).length === 0) {
      toast.addToast('请至少填写一个要修改的字段', 'error');
      return;
    }

    setBatchOperating(true);
    try {
      const result = await api.batchUpdateTrainingSamples([...selectedIds], updates);
      toast.addToast(`已更新 ${result.updated || selectedIds.size} 条样本`, 'success');
      setShowBatchEdit(false);
      setBatchEditForm({ ...emptyBatchEdit });
      setSelectedIds(new Set());
      await loadSamples();
    } catch (err) {
      toast.addToast('批量修改失败', 'error');
    } finally {
      setBatchOperating(false);
    }
  };

  const totalPages = Math.ceil(total / pageSize) || 1;
  const allSelected = samples.length > 0 && selectedIds.size === samples.length;

  return (
    <div>
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between mb-6 gap-3">
        <h2 className="text-2xl font-bold">训练样本</h2>
        <button
          onClick={() => { setShowForm(true); setEditId(null); setForm({ ...emptySample }); }}
          className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600 text-sm"
        >
          + 新增样本
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3 mb-4">
        <select
          value={filterSource}
          onChange={(e) => { setFilterSource(e.target.value); setPage(1); }}
          className="border rounded px-2 py-1 text-sm"
        >
          <option value="">全部来源</option>
          <option value="manual">手动</option>
          <option value="auto">自动</option>
          <option value="log">日志</option>
        </select>
        <select
          value={filterTaskType}
          onChange={(e) => { setFilterTaskType(e.target.value); setPage(1); }}
          className="border rounded px-2 py-1 text-sm"
        >
          <option value="">全部任务类型</option>
          <option value="chat">对话</option>
          <option value="code">代码</option>
          <option value="reasoning">推理</option>
          <option value="creative">创意</option>
        </select>
      </div>

      {/* Batch operation bar */}
      {selectedIds.size > 0 && (
        <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 mb-4 flex flex-wrap items-center gap-3">
          <span className="text-sm text-blue-700 font-medium">已选 {selectedIds.size} 条</span>
          <button
            onClick={() => setShowBatchEdit(true)}
            disabled={batchOperating}
            className="px-3 py-1 bg-orange-500 text-white rounded text-sm hover:bg-orange-600 disabled:opacity-50"
          >
            批量修改
          </button>
          <button
            onClick={handleBatchDelete}
            disabled={batchOperating}
            className="px-3 py-1 bg-red-500 text-white rounded text-sm hover:bg-red-600 disabled:opacity-50"
          >
            批量删除
          </button>
          <button
            onClick={() => setSelectedIds(new Set())}
            className="px-3 py-1 bg-gray-200 text-gray-700 rounded text-sm hover:bg-gray-300"
          >
            取消选择
          </button>
        </div>
      )}

      {/* Batch edit form */}
      {showBatchEdit && (
        <div className="bg-white rounded-lg shadow p-4 mb-4">
          <h3 className="font-semibold mb-3">批量修改 ({selectedIds.size} 条)</h3>
          <p className="text-xs text-gray-500 mb-3">仅填写需要修改的字段，留空的字段不会被修改</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">难度</label>
              <input
                type="number"
                step="0.1"
                className="w-full border rounded px-2 py-1 text-sm"
                placeholder="不修改"
                value={batchEditForm.difficulty}
                onChange={(e) => setBatchEditForm({ ...batchEditForm, difficulty: e.target.value })}
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">任务类型</label>
              <input
                className="w-full border rounded px-2 py-1 text-sm"
                placeholder="不修改"
                value={batchEditForm.task_type}
                onChange={(e) => setBatchEditForm({ ...batchEditForm, task_type: e.target.value })}
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">模型</label>
              <input
                className="w-full border rounded px-2 py-1 text-sm"
                placeholder="不修改"
                value={batchEditForm.model_name}
                onChange={(e) => setBatchEditForm({ ...batchEditForm, model_name: e.target.value })}
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">来源</label>
              <select
                className="w-full border rounded px-2 py-1 text-sm"
                value={batchEditForm.source}
                onChange={(e) => setBatchEditForm({ ...batchEditForm, source: e.target.value })}
              >
                <option value="">不修改</option>
                <option value="manual">手动</option>
                <option value="auto">自动</option>
                <option value="log">日志</option>
              </select>
            </div>
          </div>
          <div className="flex gap-2 mt-3">
            <button
              onClick={handleBatchUpdate}
              disabled={batchOperating}
              className="px-4 py-2 bg-blue-500 text-white rounded text-sm hover:bg-blue-600 disabled:opacity-50"
            >
              {batchOperating ? '修改中...' : '确认修改'}
            </button>
            <button
              onClick={() => { setShowBatchEdit(false); setBatchEditForm({ ...emptyBatchEdit }); }}
              className="px-4 py-2 bg-gray-200 text-gray-700 rounded text-sm hover:bg-gray-300"
            >
              取消
            </button>
          </div>
        </div>
      )}

      {/* Form */}
      {showForm && (
        <div className="bg-white rounded-lg shadow p-4 mb-4">
          <h3 className="font-semibold mb-3">{editId ? '编辑样本' : '新增样本'}</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            <div className="sm:col-span-2 lg:col-span-3">
              <label className="block text-xs text-gray-500 mb-1">Prompt</label>
              <textarea className="w-full border rounded px-2 py-1 text-sm" rows={3} value={form.prompt || ''} onChange={(e) => setForm({ ...form, prompt: e.target.value })} />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">难度</label>
              <input type="number" step="0.1" className="w-full border rounded px-2 py-1 text-sm" value={form.difficulty || 0} onChange={(e) => setForm({ ...form, difficulty: Number(e.target.value) })} />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Token数</label>
              <input type="number" className="w-full border rounded px-2 py-1 text-sm" value={form.token_count || 0} onChange={(e) => setForm({ ...form, token_count: Number(e.target.value) })} />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">任务类型</label>
              <input className="w-full border rounded px-2 py-1 text-sm" value={form.task_type || ''} onChange={(e) => setForm({ ...form, task_type: e.target.value })} />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">模型</label>
              <input className="w-full border rounded px-2 py-1 text-sm" value={form.model_name || ''} onChange={(e) => setForm({ ...form, model_name: e.target.value })} />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">来源</label>
              <select className="w-full border rounded px-2 py-1 text-sm" value={form.source || 'manual'} onChange={(e) => setForm({ ...form, source: e.target.value })}>
                <option value="manual">手动</option>
                <option value="auto">自动</option>
                <option value="log">日志</option>
              </select>
            </div>
          </div>
          <div className="flex gap-2 mt-3">
            <button onClick={editId ? handleUpdate : handleCreate} className="px-4 py-2 bg-blue-500 text-white rounded text-sm hover:bg-blue-600">保存</button>
            <button onClick={() => { setShowForm(false); setEditId(null); }} className="px-4 py-2 bg-gray-200 text-gray-700 rounded text-sm hover:bg-gray-300">取消</button>
          </div>
        </div>
      )}

      {loading ? (
        <div className="text-gray-500">加载中...</div>
      ) : (
        <>
          <div className="bg-white rounded-lg shadow overflow-x-auto">
            <table className="w-full text-sm min-w-[700px]">
              <thead className="bg-gray-50 text-gray-600">
                <tr>
                  <th className="px-4 py-3 text-left w-10">
                    <input
                      type="checkbox"
                      checked={allSelected}
                      onChange={toggleSelectAll}
                      className="rounded"
                    />
                  </th>
                  <th className="px-4 py-3 text-left">Prompt</th>
                  <th className="px-4 py-3 text-left">难度</th>
                  <th className="px-4 py-3 text-left">Token数</th>
                  <th className="px-4 py-3 text-left">任务类型</th>
                  <th className="px-4 py-3 text-left">模型</th>
                  <th className="px-4 py-3 text-left">来源</th>
                  <th className="px-4 py-3 text-left">操作</th>
                </tr>
              </thead>
              <tbody>
                {samples.map((s) => (
                  <tr key={s.id} className={`border-b hover:bg-gray-50 ${selectedIds.has(Number(s.id)) ? 'bg-blue-50' : ''}`}>
                    <td className="px-4 py-3">
                      <input
                        type="checkbox"
                        checked={selectedIds.has(Number(s.id))}
                        onChange={() => toggleSelect(Number(s.id))}
                        className="rounded"
                      />
                    </td>
                    <td className="px-4 py-3 max-w-[200px] truncate" title={s.prompt}>{s.prompt}</td>
                    <td className="px-4 py-3">{s.difficulty}</td>
                    <td className="px-4 py-3">{s.token_count}</td>
                    <td className="px-4 py-3">{s.task_type}</td>
                    <td className="px-4 py-3">{s.model_name}</td>
                    <td className="px-4 py-3">
                      <span className="px-2 py-0.5 rounded text-xs bg-gray-100 text-gray-600">{s.source}</span>
                    </td>
                    <td className="px-4 py-3 space-x-2">
                      <button onClick={() => startEdit(s)} className="text-blue-500 hover:underline text-xs">编辑</button>
                      <button onClick={() => handleDelete(s.id)} className="text-red-500 hover:underline text-xs">删除</button>
                    </td>
                  </tr>
                ))}
                {samples.length === 0 && (
                  <tr><td colSpan={8} className="px-4 py-6 text-center text-gray-400">暂无训练样本</td></tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-between mt-4 text-sm">
            <span className="text-gray-500">共 {total} 条</span>
            <div className="flex gap-2">
              <button
                onClick={() => setPage(Math.max(1, page - 1))}
                disabled={page <= 1}
                className="px-3 py-1 border rounded disabled:opacity-50 hover:bg-gray-100"
              >
                上一页
              </button>
              <span className="px-3 py-1">{page} / {totalPages}</span>
              <button
                onClick={() => setPage(Math.min(totalPages, page + 1))}
                disabled={page >= totalPages}
                className="px-3 py-1 border rounded disabled:opacity-50 hover:bg-gray-100"
              >
                下一页
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
