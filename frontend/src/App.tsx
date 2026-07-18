import { useState, useEffect, lazy, Suspense } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import LoginPage from './pages/LoginPage';
import Layout from './components/Layout';
import { ToastProvider } from './components/Toast';

// 路由懒加载 - 按需加载页面组件
const DashboardPage = lazy(() => import('./pages/DashboardPage'));
const ModelsPage = lazy(() => import('./pages/ModelsPage'));
const ProvidersPage = lazy(() => import('./pages/ProvidersPage'));
const TrainingSamplesPage = lazy(() => import('./pages/TrainingSamplesPage'));
const TuningPage = lazy(() => import('./pages/TuningPage'));
const RouteConfigPage = lazy(() => import('./pages/RouteConfigPage'));
const DifficultyPage = lazy(() => import('./pages/DifficultyPage'));
const ExchangeRatesPage = lazy(() => import('./pages/ExchangeRatesPage'));
const ModelAliasesPage = lazy(() => import('./pages/ModelAliasesPage'));
const HealthConfigPage = lazy(() => import('./pages/HealthConfigPage'));
const StorageConfigPage = lazy(() => import('./pages/StorageConfigPage'));
const TenantsPage = lazy(() => import('./pages/TenantsPage'));
const RequestLogsPage = lazy(() => import('./pages/RequestLogsPage'));
const FeedbackPage = lazy(() => import('./pages/FeedbackPage'));
const ConfigPage = lazy(() => import('./pages/ConfigPage'));
const HealthPage = lazy(() => import('./pages/HealthPage'));
const BalancePage = lazy(() => import('./pages/BalancePage'));
const TenantUsagePage = lazy(() => import('./pages/TenantUsagePage'));
const BigScreenPage = lazy(() => import('./pages/BigScreenPage'));
const NotificationsPage = lazy(() => import('./pages/NotificationsPage'));
const UsersPage = lazy(() => import('./pages/UsersPage'));
const PermissionsPage = lazy(() => import('./pages/PermissionsPage'));

function PageLoader() {
  return (
    <div className="flex items-center justify-center h-64">
      <div className="text-gray-400 text-sm">加载中...</div>
    </div>
  );
}

// Route permission map: which roles can access which routes
const ROUTE_PERMISSIONS: Record<string, string[]> = {
  '/dashboard': ['admin', 'user', 'guest'],
  '/big-screen': ['admin', 'user', 'guest'],
  '/models': ['admin', 'user', 'guest'],
  '/providers': ['admin'],
  '/training-samples': ['admin', 'user'],
  '/tuning': ['admin'],
  '/request-logs': ['admin', 'user'],
  '/balance': ['admin', 'user'],
  '/route-config': ['admin'],
  '/difficulty': ['admin'],
  '/exchange-rates': ['admin'],
  '/model-aliases': ['admin'],
  '/health-config': ['admin'],
  '/storage-config': ['admin'],
  '/notifications': ['admin'],
  '/tenants': ['admin'],
  '/tenant-usage': ['admin'],
  '/feedback': ['admin', 'user'],
  '/config': ['admin'],
  '/health': ['admin'],
  '/users': ['admin'],
  '/permissions': ['admin'],
};

function canAccess(path: string, role: string): boolean {
  const allowed = ROUTE_PERMISSIONS[path];
  if (!allowed) return role === 'admin'; // unknown routes: admin only
  return allowed.includes(role);
}

export default function App() {
  const [token, setToken] = useState<string>(() => localStorage.getItem('sr_token') || '');
  const [role, setRole] = useState<string>(() => localStorage.getItem('sr_role') || '');
  const [username, setUsername] = useState<string>(() => localStorage.getItem('sr_username') || '');

  useEffect(() => {
    if (token) {
      localStorage.setItem('sr_token', token);
      localStorage.setItem('sr_role', role);
      localStorage.setItem('sr_username', username);
    } else {
      localStorage.removeItem('sr_token');
      localStorage.removeItem('sr_role');
      localStorage.removeItem('sr_username');
    }
  }, [token, role, username]);

  const handleLogin = (newToken: string, newRole: string, newUser?: string) => {
    setToken(newToken);
    setRole(newRole);
    setUsername(newUser || '');
  };

  const handleLogout = () => {
    setToken('');
    setRole('');
    setUsername('');
  };

  if (!token) {
    return <LoginPage onLogin={handleLogin} />;
  }

  return (
    <ToastProvider>
      <Layout onLogout={handleLogout} token={token} role={role} username={username}>
        <Suspense fallback={<PageLoader />}>
          <Routes>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<DashboardPage token={token} />} />
            <Route path="/big-screen" element={<BigScreenPage token={token} />} />
            {canAccess('/models', role) && <Route path="/models" element={<ModelsPage token={token} />} />}
            {canAccess('/providers', role) && <Route path="/providers" element={<ProvidersPage token={token} />} />}
            {canAccess('/training-samples', role) && <Route path="/training-samples" element={<TrainingSamplesPage token={token} />} />}
            {canAccess('/tuning', role) && <Route path="/tuning" element={<TuningPage token={token} />} />}
            {canAccess('/request-logs', role) && <Route path="/request-logs" element={<RequestLogsPage token={token} />} />}
            {canAccess('/balance', role) && <Route path="/balance" element={<BalancePage token={token} />} />}
            {canAccess('/route-config', role) && <Route path="/route-config" element={<RouteConfigPage token={token} />} />}
            {canAccess('/difficulty', role) && <Route path="/difficulty" element={<DifficultyPage token={token} />} />}
            {canAccess('/exchange-rates', role) && <Route path="/exchange-rates" element={<ExchangeRatesPage token={token} />} />}
            {canAccess('/model-aliases', role) && <Route path="/model-aliases" element={<ModelAliasesPage token={token} />} />}
            {canAccess('/health-config', role) && <Route path="/health-config" element={<HealthConfigPage token={token} />} />}
            {canAccess('/storage-config', role) && <Route path="/storage-config" element={<StorageConfigPage token={token} />} />}
            {canAccess('/notifications', role) && <Route path="/notifications" element={<NotificationsPage token={token} />} />}
            {canAccess('/tenants', role) && <Route path="/tenants" element={<TenantsPage token={token} />} />}
            {canAccess('/tenant-usage', role) && <Route path="/tenant-usage" element={<TenantUsagePage token={token} />} />}
            {canAccess('/feedback', role) && <Route path="/feedback" element={<FeedbackPage token={token} />} />}
            {canAccess('/config', role) && <Route path="/config" element={<ConfigPage token={token} />} />}
            {canAccess('/health', role) && <Route path="/health" element={<HealthPage token={token} />} />}
            {canAccess('/users', role) && <Route path="/users" element={<UsersPage token={token} />} />}
            {canAccess('/permissions', role) && <Route path="/permissions" element={<PermissionsPage token={token} />} />}
            {/* Catch-all: redirect unknown routes to dashboard */}
            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Routes>
        </Suspense>
      </Layout>
    </ToastProvider>
  );
}
