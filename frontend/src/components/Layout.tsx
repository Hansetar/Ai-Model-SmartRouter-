import { useState, ReactNode, useEffect, useMemo } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { createApi } from '../api';

interface LayoutProps {
  children: ReactNode;
  onLogout: () => void;
  token: string;
  role: string;
  username: string;
}

interface NavGroup {
  label: string;
  icon: string;
  items: { to: string; label: string; roles: string[] }[];
}

const navGroups: NavGroup[] = [
  {
    label: '概览',
    icon: '📊',
    items: [
      { to: '/dashboard', label: '仪表盘', roles: ['admin', 'user', 'guest'] },
      { to: '/big-screen', label: '大屏模式', roles: ['admin', 'user', 'guest'] },
    ],
  },
  {
    label: '智能路由',
    icon: '🤖',
    items: [
      { to: '/models', label: '模型管理', roles: ['admin', 'user', 'guest'] },
      { to: '/providers', label: '供应商管理', roles: ['admin'] },
      { to: '/training-samples', label: '训练样本', roles: ['admin', 'user'] },
      { to: '/tuning', label: '模型调优', roles: ['admin'] },
      { to: '/request-logs', label: '请求日志', roles: ['admin', 'user'] },
      { to: '/balance', label: '余额查询', roles: ['admin', 'user'] },
    ],
  },
  {
    label: '系统配置',
    icon: '⚙️',
    items: [
      { to: '/route-config', label: '路由配置', roles: ['admin'] },
      { to: '/difficulty', label: '难度范围', roles: ['admin'] },
      { to: '/exchange-rates', label: '汇率管理', roles: ['admin'] },
      { to: '/model-aliases', label: '模型别名', roles: ['admin'] },
      { to: '/health-config', label: '健康检查', roles: ['admin'] },
      { to: '/storage-config', label: '存储与框架', roles: ['admin'] },
      { to: '/notifications', label: '通知配置', roles: ['admin'] },
    ],
  },
  {
    label: '多租户',
    icon: '👥',
    items: [
      { to: '/tenants', label: '租户管理', roles: ['admin'] },
      { to: '/tenant-usage', label: '租户消耗统计', roles: ['admin'] },
    ],
  },
  {
    label: '反馈',
    icon: '📝',
    items: [{ to: '/feedback', label: '反馈管理', roles: ['admin', 'user'] }],
  },
  {
    label: '用户与权限',
    icon: '🔐',
    items: [
      { to: '/users', label: '用户管理', roles: ['admin'] },
      { to: '/permissions', label: '权限配置', roles: ['admin'] },
    ],
  },
];

const ROLE_LABELS: Record<string, string> = {
  admin: '管理员',
  user: '用户',
  guest: '访客',
};

export default function Layout({ children, onLogout, token, role, username }: LayoutProps) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({});
  const [configInfo, setConfigInfo] = useState<Record<string, unknown> | null>(null);
  const location = useLocation();

  useEffect(() => {
    // Load config info once
    if (token && !configInfo) {
      const api = createApi(token);
      api.getConfigInfo().then(setConfigInfo).catch(() => {});
    }
  }, [token, configInfo]);

  const toggleGroup = (label: string) => {
    setCollapsedGroups((prev) => ({ ...prev, [label]: !prev[label] }));
  };

  const closeMobile = () => setMobileOpen(false);

  // Filter nav groups based on role
  const filteredNavGroups = useMemo(() => {
    return navGroups
      .map((group) => ({
        ...group,
        items: group.items.filter((item) => item.roles.includes(role)),
      }))
      .filter((group) => group.items.length > 0);
  }, [role]);

  // Auto-expand group containing current route
  const currentPath = location.pathname;

  return (
    <div className="min-h-screen flex">
      {/* Mobile overlay */}
      {mobileOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-40 lg:hidden"
          onClick={closeMobile}
        />
      )}

      {/* Sidebar */}
      <aside
        className={`fixed lg:static inset-y-0 left-0 z-50 w-60 bg-gray-900 text-white flex flex-col transform transition-transform duration-200 ${
          mobileOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'
        }`}
      >
        {/* Header */}
        <div className="p-4 text-lg font-bold border-b border-gray-700 flex items-center justify-between">
          <span>SmartRouter v2</span>
          <button
            className="lg:hidden text-gray-400 hover:text-white"
            onClick={closeMobile}
          >
            ✕
          </button>
        </div>

        {/* User info */}
        <div className="px-4 py-2 border-b border-gray-700 text-xs text-gray-400">
          <div className="flex items-center justify-between">
            <span className="truncate">{username || ROLE_LABELS[role] || role}</span>
            <span className="px-1.5 py-0.5 rounded text-xs bg-gray-700 text-gray-300">
              {ROLE_LABELS[role] || role}
            </span>
          </div>
        </div>

        {/* Navigation */}
        <nav className="flex-1 overflow-y-auto p-2 space-y-1">
          {filteredNavGroups.map((group) => {
            const isCollapsed = collapsedGroups[group.label] ?? false;
            const isActive = group.items.some((item) => currentPath === item.to);

            return (
              <div key={group.label}>
                <button
                  onClick={() => toggleGroup(group.label)}
                  className={`w-full flex items-center justify-between px-3 py-2 rounded text-sm font-medium ${
                    isActive
                      ? 'bg-gray-800 text-white'
                      : 'text-gray-300 hover:bg-gray-800 hover:text-white'
                  }`}
                >
                  <span>
                    {group.icon} {group.label}
                  </span>
                  <span
                    className={`transform transition-transform text-xs ${
                      isCollapsed ? '' : 'rotate-90'
                    }`}
                  >
                    ▶
                  </span>
                </button>
                {!isCollapsed && (
                  <div className="ml-4 mt-1 space-y-1">
                    {group.items.map((item) => (
                      <NavLink
                        key={item.to}
                        to={item.to}
                        onClick={closeMobile}
                        className={({ isActive }) =>
                          `block px-3 py-1.5 rounded text-sm ${
                            isActive
                              ? 'bg-blue-600 text-white'
                              : 'text-gray-400 hover:bg-gray-800 hover:text-white'
                          }`
                        }
                      >
                        {item.label}
                      </NavLink>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </nav>

        {/* Logout */}
        <div className="p-4 border-t border-gray-700">
          <button
            onClick={onLogout}
            className="w-full px-3 py-2 text-sm text-gray-300 hover:text-white hover:bg-gray-800 rounded"
          >
            退出登录
          </button>
        </div>
      </aside>

      {/* Main content */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Config status bar */}
        {configInfo && (
          <div className="bg-gray-800 text-gray-300 text-xs px-4 py-1.5 flex items-center gap-4 overflow-x-auto">
            <span className="flex items-center gap-1 whitespace-nowrap">
              <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              配置: {String(configInfo.config_path || '默认')}
            </span>
            <span className="whitespace-nowrap">
              DB: {(configInfo.database as Record<string, string>)?.backend || 'sqlite'}
              {(configInfo.database as Record<string, string>)?.path ? ` (${(configInfo.database as Record<string, string>).path})` : ''}
            </span>
            <span className="whitespace-nowrap">
              模型: {String(configInfo.total_models || 0)} | 供应商: {String(configInfo.total_providers || 0)}
            </span>
            {(configInfo.env_overrides as string[])?.length > 0 && (
              <span className="text-yellow-400 whitespace-nowrap">
                环境变量覆盖: {(configInfo.env_overrides as string[]).length}项
              </span>
            )}
          </div>
        )}

        {/* Mobile header */}
        <header className="lg:hidden bg-white shadow-sm px-4 py-3 flex items-center">
          <button
            onClick={() => setMobileOpen(true)}
            className="text-gray-600 hover:text-gray-900 mr-3"
          >
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>
          <span className="font-bold text-gray-800">SmartRouter</span>
        </header>

        <main className="flex-1 p-4 lg:p-6 overflow-auto bg-gray-50">{children}</main>
      </div>
    </div>
  );
}
