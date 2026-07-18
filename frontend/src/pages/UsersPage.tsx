import { useState, useEffect } from 'react';
import { createApi, translateError } from '../api';
import { useToast } from '../components/Toast';

interface UsersPageProps {
  token: string;
}

interface UserData {
  username: string;
  role: string;
  tenant_id: string;
  [key: string]: unknown;
}

const ROLE_OPTIONS = [
  { value: 'admin', label: '管理员' },
  { value: 'user', label: '普通用户' },
  { value: 'guest', label: '访客' },
];

export default function UsersPage({ token }: UsersPageProps) {
  const api = createApi(token);
  const toast = useToast();
  const [users, setUsers] = useState<UserData[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editUsername, setEditUsername] = useState<string | null>(null);
  const [form, setForm] = useState({ username: '', password: '', role: 'user', tenant_id: '' });
  const [saving, setSaving] = useState(false);
  const [showTransfer, setShowTransfer] = useState(false);
  const [transferTarget, setTransferTarget] = useState('');

  useEffect(() => {
    loadUsers();
  }, []);

  const loadUsers = async () => {
    setLoading(true);
    try {
      const result = await api.getUsers();
      setUsers(result.users || []);
    } catch (err) {
      const { message, detail } = translateError(err);
      toast.addToast(message, 'error', detail);
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = async () => {
    if (!form.username.trim()) {
      toast.addToast('用户名不能为空', 'error');
      return;
    }
    if (!form.password || form.password.length < 6) {
      toast.addToast('密码至少6位', 'error');
      return;
    }
    setSaving(true);
    try {
      await api.createUser(form);
      toast.addToast('用户创建成功', 'success', `已添加用户: ${form.username}`);
      setShowForm(false);
      setForm({ username: '', password: '', role: 'user', tenant_id: '' });
      await loadUsers();
    } catch (err) {
      const { message, detail } = translateError(err);
      toast.addToast(`创建失败: ${message}`, 'error', detail);
    } finally {
      setSaving(false);
    }
  };

  const handleUpdate = async () => {
    if (!editUsername) return;
    setSaving(true);
    try {
      const updateData: Record<string, unknown> = { role: form.role, tenant_id: form.tenant_id };
      if (form.password) {
        updateData.password = form.password;
      }
      await api.updateUser(editUsername, updateData);
      toast.addToast('用户更新成功', 'success');
      setEditUsername(null);
      setForm({ username: '', password: '', role: 'user', tenant_id: '' });
      await loadUsers();
    } catch (err) {
      const { message, detail } = translateError(err);
      toast.addToast(`更新失败: ${message}`, 'error', detail);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (username: string) => {
    if (!confirm(`确定删除用户 "${username}"?`)) return;
    try {
      await api.deleteUser(username);
      toast.addToast('用户已删除', 'success');
      await loadUsers();
    } catch (err) {
      const { message, detail } = translateError(err);
      toast.addToast(`删除失败: ${message}`, 'error', detail);
    }
  };

  const handleTransferSuperadmin = async () => {
    if (!transferTarget.trim()) {
      toast.addToast('请输入目标用户名', 'error');
      return;
    }
    if (!confirm(`确定将超级管理员权限转移给 "${transferTarget}"? 此操作不可撤销。`)) return;
    setSaving(true);
    try {
      await api.transferSuperadmin({ username: transferTarget });
      toast.addToast('超级管理员已转移', 'success', `已转移给: ${transferTarget}`);
      setShowTransfer(false);
      setTransferTarget('');
      await loadUsers();
    } catch (err) {
      const { message, detail } = translateError(err);
      toast.addToast(`转移失败: ${message}`, 'error', detail);
    } finally {
      setSaving(false);
    }
  };

  const startEdit = (u: UserData) => {
    setEditUsername(u.username);
    setForm({ username: u.username, password: '', role: u.role, tenant_id: u.tenant_id || '' });
    setShowForm(true);
  };

  if (loading) return <div className="text-gray-500">加载中...</div>;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold">用户管理</h2>
        <div className="flex gap-2">
          <button
            onClick={() => setShowTransfer(true)}
            className="px-4 py-2 bg-orange-500 text-white rounded hover:bg-orange-600 text-sm"
          >
            转移超管权限
          </button>
          <button
            onClick={() => { setShowForm(true); setEditUsername(null); setForm({ username: '', password: '', role: 'user', tenant_id: '' }); }}
            className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600 text-sm"
          >
            + 新增用户
          </button>
        </div>
      </div>

      {/* Create/Edit form */}
      {showForm && (
        <div className="bg-white rounded-lg shadow p-4 mb-4">
          <h3 className="font-semibold mb-3">{editUsername ? '编辑用户' : '新增用户'}</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">用户名</label>
              <input
                className="w-full border rounded px-2 py-1 text-sm"
                value={form.username}
                onChange={(e) => setForm({ ...form, username: e.target.value })}
                disabled={!!editUsername}
                placeholder="至少2个字符"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">
                密码 {editUsername && '(留空不修改)'}
              </label>
              <input
                type="password"
                className="w-full border rounded px-2 py-1 text-sm"
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
                placeholder={editUsername ? '留空不修改' : '至少6位'}
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">角色</label>
              <select
                className="w-full border rounded px-2 py-1 text-sm"
                value={form.role}
                onChange={(e) => setForm({ ...form, role: e.target.value })}
              >
                {ROLE_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">关联租户ID (可选)</label>
              <input
                className="w-full border rounded px-2 py-1 text-sm"
                value={form.tenant_id}
                onChange={(e) => setForm({ ...form, tenant_id: e.target.value })}
                placeholder="关联的租户ID"
              />
            </div>
          </div>
          <div className="flex gap-2 mt-3">
            <button
              onClick={editUsername ? handleUpdate : handleCreate}
              disabled={saving}
              className={`px-4 py-2 rounded text-sm font-medium transition-colors ${
                saving ? 'bg-gray-400 text-white cursor-not-allowed' : 'bg-blue-500 text-white hover:bg-blue-600'
              }`}
            >
              {saving ? '保存中...' : '保存'}
            </button>
            <button
              onClick={() => { setShowForm(false); setEditUsername(null); }}
              disabled={saving}
              className="px-4 py-2 bg-gray-200 text-gray-700 rounded text-sm hover:bg-gray-300 disabled:opacity-50"
            >
              取消
            </button>
          </div>
        </div>
      )}

      {/* Transfer superadmin dialog */}
      {showTransfer && (
        <div className="bg-white rounded-lg shadow p-4 mb-4 border-l-4 border-orange-500">
          <h3 className="font-semibold mb-2 text-orange-700">转移超级管理员权限</h3>
          <p className="text-sm text-gray-500 mb-3">
            将超级管理员权限转移给其他用户。转移后，当前账号将降为普通管理员。此操作不可撤销。
          </p>
          <div className="flex gap-2">
            <input
              className="flex-1 border rounded px-2 py-1 text-sm"
              value={transferTarget}
              onChange={(e) => setTransferTarget(e.target.value)}
              placeholder="输入目标用户名"
            />
            <button
              onClick={handleTransferSuperadmin}
              disabled={saving}
              className="px-4 py-1 bg-orange-500 text-white rounded text-sm hover:bg-orange-600 disabled:bg-gray-400"
            >
              {saving ? '转移中...' : '确认转移'}
            </button>
            <button
              onClick={() => { setShowTransfer(false); setTransferTarget(''); }}
              className="px-4 py-1 bg-gray-200 text-gray-700 rounded text-sm hover:bg-gray-300"
            >
              取消
            </button>
          </div>
        </div>
      )}

      {/* Users table */}
      <div className="bg-white rounded-lg shadow overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-gray-600">
            <tr>
              <th className="px-4 py-3 text-left">用户名</th>
              <th className="px-4 py-3 text-left">角色</th>
              <th className="px-4 py-3 text-left">关联租户</th>
              <th className="px-4 py-3 text-left">操作</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.username} className="border-b hover:bg-gray-50">
                <td className="px-4 py-3 font-medium">{u.username}</td>
                <td className="px-4 py-3">
                  <span className={`px-2 py-0.5 rounded text-xs ${
                    u.role === 'admin' ? 'bg-purple-100 text-purple-700' :
                    u.role === 'user' ? 'bg-blue-100 text-blue-700' :
                    'bg-gray-100 text-gray-700'
                  }`}>
                    {ROLE_OPTIONS.find((o) => o.value === u.role)?.label || u.role}
                  </span>
                </td>
                <td className="px-4 py-3 text-xs text-gray-500">{u.tenant_id || '-'}</td>
                <td className="px-4 py-3 space-x-2">
                  <button onClick={() => startEdit(u)} className="text-blue-500 hover:underline text-xs">编辑</button>
                  <button onClick={() => handleDelete(u.username)} className="text-red-500 hover:underline text-xs">删除</button>
                </td>
              </tr>
            ))}
            {users.length === 0 && (
              <tr><td colSpan={4} className="px-4 py-6 text-center text-gray-400">暂无用户</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
