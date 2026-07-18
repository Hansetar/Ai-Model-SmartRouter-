import { useState, useEffect } from 'react';
import { createApi } from '../api';

interface DifficultyPageProps {
  token: string;
}

interface DifficultyRange {
  min_tokens: number;
  max_tokens: number;
  difficulty: number;
}

const difficultyLabel = (d: number): string => {
  if (d <= 20) return '简单';
  if (d <= 40) return '较简单';
  if (d <= 60) return '中等';
  if (d <= 80) return '较困难';
  return '困难';
};

const difficultyColor = (d: number): string => {
  if (d <= 20) return 'bg-green-100 text-green-700';
  if (d <= 40) return 'bg-lime-100 text-lime-700';
  if (d <= 60) return 'bg-yellow-100 text-yellow-700';
  if (d <= 80) return 'bg-orange-100 text-orange-700';
  return 'bg-red-100 text-red-700';
};

export default function DifficultyPage({ token }: DifficultyPageProps) {
  const api = createApi(token);
  const [ranges, setRanges] = useState<DifficultyRange[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState<DifficultyRange>({ min_tokens: 0, max_tokens: 100, difficulty: 10 });

  useEffect(() => {
    loadConfig();
  }, []);

  const loadConfig = async () => {
    setLoading(true);
    try {
      const result = await api.getConfig();
      setRanges((result.difficulty_ranges as DifficultyRange[]) || []);
    } catch (err) {
      console.error('Failed to load config:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    try {
      const newRanges = editingIndex !== null
        ? ranges.map((r, i) => (i === editingIndex ? { ...form } : r))
        : [...ranges, { ...form }];
      await api.updateDifficultyRanges(newRanges);
      await loadConfig(); // Reload from backend to confirm
      setShowAdd(false);
      setEditingIndex(null);
      setForm({ min_tokens: 0, max_tokens: 100, difficulty: 10 });
    } catch (err) {
      alert('保存失败: ' + (err as any)?.response?.data?.detail || String(err));
    }
  };

  const handleDelete = async (index: number) => {
    if (!confirm('确定删除此难度范围?')) return;
    try {
      const newRanges = ranges.filter((_, i) => i !== index);
      await api.updateDifficultyRanges(newRanges);
      await loadConfig();
    } catch (err) {
      alert('删除失败');
    }
  };

  const startEdit = (index: number) => {
    setEditingIndex(index);
    const r = ranges[index];
    if (r) setForm({ min_tokens: r.min_tokens, max_tokens: r.max_tokens, difficulty: r.difficulty });
    setShowAdd(true);
  };

  if (loading) return <div className="text-gray-500">加载中...</div>;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold">难度范围管理</h2>
        <button
          onClick={() => { setShowAdd(true); setEditingIndex(null); setForm({ min_tokens: 0, max_tokens: 100, difficulty: 10 }); }}
          className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600 text-sm"
        >
          + 添加范围
        </button>
      </div>

      {showAdd && (
        <div className="bg-white rounded-lg shadow p-4 mb-4">
          <h3 className="font-semibold mb-3">{editingIndex !== null ? '编辑难度范围' : '添加难度范围'}</h3>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">最小Token</label>
              <input
                type="number"
                className="w-full border rounded px-2 py-1 text-sm"
                value={form.min_tokens}
                onChange={(e) => setForm({ ...form, min_tokens: Number(e.target.value) })}
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">最大Token</label>
              <input
                type="number"
                className="w-full border rounded px-2 py-1 text-sm"
                value={form.max_tokens}
                onChange={(e) => setForm({ ...form, max_tokens: Number(e.target.value) })}
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">难度等级 (1-100)</label>
              <input
                type="number"
                min={1}
                max={100}
                className="w-full border rounded px-2 py-1 text-sm"
                value={form.difficulty}
                onChange={(e) => setForm({ ...form, difficulty: Number(e.target.value) })}
              />
              <div className="text-xs text-gray-400 mt-1">
                {difficultyLabel(form.difficulty)} ({form.difficulty})
              </div>
            </div>
          </div>
          <div className="flex gap-2 mt-3">
            <button onClick={handleSave} className="px-4 py-2 bg-blue-500 text-white rounded text-sm hover:bg-blue-600">保存</button>
            <button onClick={() => { setShowAdd(false); setEditingIndex(null); }} className="px-4 py-2 bg-gray-200 text-gray-700 rounded text-sm hover:bg-gray-300">取消</button>
          </div>
        </div>
      )}

      <div className="bg-white rounded-lg shadow overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-gray-600">
            <tr>
              <th className="px-4 py-3 text-left">最小Token</th>
              <th className="px-4 py-3 text-left">最大Token</th>
              <th className="px-4 py-3 text-left">难度等级</th>
              <th className="px-4 py-3 text-left">操作</th>
            </tr>
          </thead>
          <tbody>
            {ranges.map((r, i) => (
              <tr key={i} className="border-b hover:bg-gray-50">
                <td className="px-4 py-3">{r.min_tokens}</td>
                <td className="px-4 py-3">{r.max_tokens}</td>
                <td className="px-4 py-3">
                  <span className={`px-2 py-0.5 rounded text-xs ${difficultyColor(r.difficulty)}`}>
                    {difficultyLabel(r.difficulty)} ({r.difficulty})
                  </span>
                </td>
                <td className="px-4 py-3 space-x-2">
                  <button onClick={() => startEdit(i)} className="text-blue-500 hover:underline text-xs">编辑</button>
                  <button onClick={() => handleDelete(i)} className="text-red-500 hover:underline text-xs">删除</button>
                </td>
              </tr>
            ))}
            {ranges.length === 0 && (
              <tr><td colSpan={4} className="px-4 py-6 text-center text-gray-400">暂无难度范围</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
