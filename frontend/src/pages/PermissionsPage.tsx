import { useState, useEffect } from 'react';
import { createApi, translateError } from '../api';
import { useToast } from '../components/Toast';

interface PermissionsPageProps {
  token: string;
}

interface PermissionConfig {
  dashboard_view: string;
  balance_view: string;
  balance_edit: boolean;
  models_view: boolean;
  models_edit: boolean;
  providers_view: boolean;
  providers_edit: boolean;
  request_logs_view: string;
  config_view: boolean;
  config_edit: boolean;
  users_view: boolean;
  users_edit: boolean;
  tenants_view: boolean;
  tenants_edit: boolean;
}

interface RolePermissions {
  admin: PermissionConfig;
  user: PermissionConfig;
  guest: PermissionConfig;
}

const ROLE_LABELS: Record<string, string> = {
  admin: '管理员',
  user: '普通用户',
  guest: '访客',
};

const VIEW_OPTIONS = [
  { value: 'none', label: '不可见' },
  { value: 'self', label: '仅自己' },
  { value: 'all', label: '全部' },
];

const PERMISSION_ITEMS: {
  key: keyof PermissionConfig;
  label: string;
  type: 'view-select' | 'boolean';
}[] = [
  { key: 'dashboard_view', label: '仪表盘', type: 'view-select' },
  { key: 'balance_view', label: '余额查看', type: 'view-select' },
  { key: 'balance_edit', label: '余额编辑', type: 'boolean' },
  { key: 'models_view', label: '模型查看', type: 'boolean' },
  { key: 'models_edit', label: '模型编辑', type: 'boolean' },
  { key: 'providers_view', label: '供应商查看', type: 'boolean' },
  { key: 'providers_edit', label: '供应商编辑', type: 'boolean' },
  { key: 'request_logs_view', label: '请求日志', type: 'view-select' },
  { key: 'config_view', label: '配置查看', type: 'boolean' },
  { key: 'config_edit', label: '配置编辑', type: 'boolean' },
  { key: 'users_view', label: '用户查看', type: 'boolean' },
  { key: 'users_edit', label: '用户编辑', type: 'boolean' },
  { key: 'tenants_view', label: '租户查看', type: 'boolean' },
  { key: 'tenants_edit', label: '租户编辑', type: 'boolean' },
];

export default function PermissionsPage({ token }: PermissionsPageProps) {
  const api = createApi(token);
  const toast = useToast();
  const [permissions, setPermissions] = useState<RolePermissions | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [activeTab, setActiveTab] = useState<string>('user');

  useEffect(() => {
    loadPermissions();
  }, []);

  const loadPermissions = async () => {
    setLoading(true);
    try {
      const result = await api.getPermissions();
      setPermissions(result as RolePermissions);
    } catch (err) {
      const { message, detail } = translateError(err);
      toast.addToast(message, 'error', detail);
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    if (!permissions) return;
    setSaving(true);
    try {
      await api.updatePermissions(permissions as unknown as Record<string, unknown>);
      toast.addToast('权限配置已保存', 'success');
    } catch (err) {
      const { message, detail } = translateError(err);
      toast.addToast(`保存失败: ${message}`, 'error', detail);
    } finally {
      setSaving(false);
    }
  };

  const updatePermission = (role: string, key: keyof PermissionConfig, value: string | boolean) => {
    if (!permissions) return;
    setPermissions({
      ...permissions,
      [role]: {
        ...permissions[role as keyof RolePermissions],
        [key]: value,
      },
    });
  };

  if (loading) return <div className="text-gray-500">加载中...</div>;
  if (!permissions) return <div className="text-gray-500">无法加载权限配置</div>;

  const currentRole = activeTab as keyof RolePermissions;
  const currentPerms = permissions[currentRole];

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold">权限配置</h2>
        <button
          onClick={handleSave}
          disabled={saving}
          className={`px-4 py-2 rounded text-sm font-medium transition-colors ${
            saving ? 'bg-gray-400 text-white cursor-not-allowed' : 'bg-blue-500 text-white hover:bg-blue-600'
          }`}
        >
          {saving ? '保存中...' : '保存配置'}
        </button>
      </div>

      <p className="text-sm text-gray-500 mb-4">
        配置不同角色的访问权限。管理员拥有所有权限，此处配置对普通用户和访客生效。
      </p>

      {/* Role tabs */}
      <div className="flex mb-4 border-b">
        {(['admin', 'user', 'guest'] as const).map((role) => (
          <button
            key={role}
            type="button"
            onClick={() => setActiveTab(role)}
            className={`px-4 pb-2 text-sm font-medium border-b-2 transition-colors ${
              activeTab === role
                ? 'border-blue-500 text-blue-600'
                : 'border-transparent text-gray-400 hover:text-gray-600'
            }`}
          >
            {ROLE_LABELS[role]}
          </button>
        ))}
      </div>

      {/* Permission table */}
      <div className="bg-white rounded-lg shadow">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-gray-600">
            <tr>
              <th className="px-4 py-3 text-left">功能</th>
              <th className="px-4 py-3 text-left">权限设置</th>
            </tr>
          </thead>
          <tbody>
            {PERMISSION_ITEMS.map((item) => (
              <tr key={item.key} className="border-b hover:bg-gray-50">
                <td className="px-4 py-3 font-medium">{item.label}</td>
                <td className="px-4 py-3">
                  {item.type === 'view-select' ? (
                    <select
                      className="border rounded px-2 py-1 text-sm"
                      value={String(currentPerms[item.key])}
                      onChange={(e) => updatePermission(currentRole, item.key, e.target.value)}
                      disabled={currentRole === 'admin'}
                    >
                      {VIEW_OPTIONS.map((opt) => (
                        <option key={opt.value} value={opt.value}>{opt.label}</option>
                      ))}
                    </select>
                  ) : (
                    <label className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        checked={Boolean(currentPerms[item.key])}
                        onChange={(e) => updatePermission(currentRole, item.key, e.target.checked)}
                        disabled={currentRole === 'admin'}
                      />
                      <span className="text-xs text-gray-500">
                        {currentPerms[item.key] ? '允许' : '禁止'}
                      </span>
                    </label>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {currentRole === 'admin' && (
        <p className="text-xs text-gray-400 mt-2">
          管理员角色拥有所有权限，无法修改。
        </p>
      )}
    </div>
  );
}
