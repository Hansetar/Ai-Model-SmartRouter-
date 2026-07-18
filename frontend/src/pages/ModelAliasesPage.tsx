import { useState, useEffect } from 'react';
import { createApi } from '../api';

interface ModelAliasesPageProps {
  token: string;
}

interface AliasEntry {
  alias: string;
  model: string;
}

export default function ModelAliasesPage({ token }: ModelAliasesPageProps) {
  const api = createApi(token);
  const [aliases, setAliases] = useState<AliasEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editIndex, setEditIndex] = useState<number | null>(null);
  const [form, setForm] = useState<AliasEntry>({ alias: '', model: '' });

  useEffect(() => {
    loadConfig();
  }, []);

  const loadConfig = async () => {
    setLoading(true);
    try {
      const result = await api.getConfig();
      const aliasesMap = result.model_aliases as Record<string, string> || {};
      const aliasesList = Object.entries(aliasesMap).map(([alias, model]) => ({ alias, model }));
      setAliases(aliasesList);
    } catch (err) {
      console.error('Failed to load config:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    if (!form.alias || !form.model) { alert('别名和模型名称不能为空'); return; }
    try {
      let newAliases: AliasEntry[];
      if (editIndex !== null) {
        newAliases = aliases.map((a, i) => (i === editIndex ? { alias: form.alias, model: form.model } : a));
      } else {
        newAliases = [...aliases, { alias: form.alias, model: form.model }];
      }
      const aliasesMap: Record<string, string> = {};
      newAliases.forEach((a) => { aliasesMap[a.alias] = a.model; });
      await api.updateModelAliases(aliasesMap);
      setAliases(newAliases);
      setShowForm(false);
      setEditIndex(null);
      setForm({ alias: '', model: '' });
      alert('模型别名已保存');
    } catch (err) {
      alert('保存失败');
    }
  };

  const handleDelete = async (index: number) => {
    if (!confirm('确定删除此别名?')) return;
    try {
      const newAliases = aliases.filter((_, i) => i !== index);
      const aliasesMap: Record<string, string> = {};
      newAliases.forEach((a) => { aliasesMap[a.alias] = a.model; });
      await api.updateModelAliases(aliasesMap);
      setAliases(newAliases);
    } catch (err) {
      alert('删除失败');
    }
  };

  const startEdit = (index: number) => {
    setEditIndex(index);
    const a = aliases[index];
    if (a) setForm({ alias: a.alias, model: a.model });
    setShowForm(true);
  };

  if (loading) return <div className="text-gray-500">加载中...</div>;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold">模型别名管理</h2>
        <button
          onClick={() => { setShowForm(true); setEditIndex(null); setForm({ alias: '', model: '' }); }}
          className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600 text-sm"
        >
          + 添加别名
        </button>
      </div>

      {showForm && (
        <div className="bg-white rounded-lg shadow p-4 mb-4">
          <h3 className="font-semibold mb-3">{editIndex !== null ? '编辑别名' : '添加别名'}</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">别名</label>
              <input
                className="w-full border rounded px-2 py-1 text-sm"
                value={form.alias}
                onChange={(e) => setForm({ ...form, alias: e.target.value })}
                disabled={editIndex !== null}
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">目标模型</label>
              <input
                className="w-full border rounded px-2 py-1 text-sm"
                value={form.model}
                onChange={(e) => setForm({ ...form, model: e.target.value })}
              />
            </div>
          </div>
          <div className="flex gap-2 mt-3">
            <button onClick={handleSave} className="px-4 py-2 bg-blue-500 text-white rounded text-sm hover:bg-blue-600">保存</button>
            <button onClick={() => { setShowForm(false); setEditIndex(null); }} className="px-4 py-2 bg-gray-200 text-gray-700 rounded text-sm hover:bg-gray-300">取消</button>
          </div>
        </div>
      )}

      <div className="bg-white rounded-lg shadow overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-gray-600">
            <tr>
              <th className="px-4 py-3 text-left">别名</th>
              <th className="px-4 py-3 text-left">目标模型</th>
              <th className="px-4 py-3 text-left">操作</th>
            </tr>
          </thead>
          <tbody>
            {aliases.map((a, i) => (
              <tr key={a.alias} className="border-b hover:bg-gray-50">
                <td className="px-4 py-3 font-medium">{a.alias}</td>
                <td className="px-4 py-3">{a.model}</td>
                <td className="px-4 py-3 space-x-2">
                  <button onClick={() => startEdit(i)} className="text-blue-500 hover:underline text-xs">编辑</button>
                  <button onClick={() => handleDelete(i)} className="text-red-500 hover:underline text-xs">删除</button>
                </td>
              </tr>
            ))}
            {aliases.length === 0 && (
              <tr><td colSpan={3} className="px-4 py-6 text-center text-gray-400">暂无模型别名</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
