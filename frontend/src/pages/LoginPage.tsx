import { useState } from 'react';

type LoginMode = 'admin' | 'user' | 'guest';

interface LoginResult {
  access_token: string;
  token_type: string;
  role: string;
  username?: string;
}

interface LoginPageProps {
  onLogin: (token: string, role: string, username?: string) => void;
}

export default function LoginPage({ onLogin }: LoginPageProps) {
  const [mode, setMode] = useState<LoginMode>('admin');
  const [password, setPassword] = useState('');
  const [username, setUsername] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleAdminLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const resp = await fetch('/admin/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      });
      if (!resp.ok) {
        setError(resp.status === 401 ? '密码错误' : '登录失败');
        return;
      }
      const data: LoginResult = await resp.json();
      onLogin(data.access_token, data.role, 'admin');
    } catch {
      setError('连接失败');
    } finally {
      setLoading(false);
    }
  };

  const handleUserLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    if (!username.trim()) {
      setError('请输入用户名');
      return;
    }
    setLoading(true);
    try {
      const resp = await fetch('/admin/api/login/user-auth', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      if (!resp.ok) {
        setError(resp.status === 401 ? '用户名或密码错误' : '登录失败');
        return;
      }
      const data: LoginResult = await resp.json();
      onLogin(data.access_token, data.role, data.username || username);
    } catch {
      setError('连接失败');
    } finally {
      setLoading(false);
    }
  };

  const handleGuestLogin = async () => {
    setError('');
    setLoading(true);
    try {
      const resp = await fetch('/admin/api/login/guest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      if (!resp.ok) {
        setError('访客登录失败');
        return;
      }
      const data: LoginResult = await resp.json();
      onLogin(data.access_token, data.role, 'guest');
    } catch {
      setError('连接失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-100">
      <div className="bg-white p-8 rounded-lg shadow-md w-96">
        <h1 className="text-xl font-bold mb-6 text-center">SmartRouter</h1>

        {/* Mode tabs */}
        <div className="flex mb-6 border-b">
          {([
            { key: 'admin' as LoginMode, label: '管理员' },
            { key: 'user' as LoginMode, label: '用户' },
            { key: 'guest' as LoginMode, label: '访客' },
          ]).map((tab) => (
            <button
              key={tab.key}
              type="button"
              onClick={() => { setMode(tab.key); setError(''); }}
              className={`flex-1 pb-2 text-sm font-medium border-b-2 transition-colors ${
                mode === tab.key
                  ? 'border-blue-500 text-blue-600'
                  : 'border-transparent text-gray-400 hover:text-gray-600'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {error && <div className="mb-4 text-red-500 text-sm text-center">{error}</div>}

        {/* Admin login form */}
        {mode === 'admin' && (
          <form onSubmit={handleAdminLogin}>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="管理密码"
              className="w-full px-3 py-2 border rounded mb-4"
              autoFocus
            />
            <button
              type="submit"
              disabled={loading}
              className="w-full bg-blue-500 text-white py-2 rounded hover:bg-blue-600 disabled:bg-gray-400 disabled:cursor-not-allowed"
            >
              {loading ? '登录中...' : '登录'}
            </button>
          </form>
        )}

        {/* User login form */}
        {mode === 'user' && (
          <form onSubmit={handleUserLogin}>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="用户名"
              className="w-full px-3 py-2 border rounded mb-3"
              autoFocus
            />
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="密码"
              className="w-full px-3 py-2 border rounded mb-4"
            />
            <button
              type="submit"
              disabled={loading}
              className="w-full bg-blue-500 text-white py-2 rounded hover:bg-blue-600 disabled:bg-gray-400 disabled:cursor-not-allowed"
            >
              {loading ? '登录中...' : '登录'}
            </button>
          </form>
        )}

        {/* Guest login */}
        {mode === 'guest' && (
          <div>
            <p className="text-sm text-gray-500 mb-4 text-center">
              访客模式仅可查看部分数据，无法修改任何配置
            </p>
            <button
              type="button"
              onClick={handleGuestLogin}
              disabled={loading}
              className="w-full bg-green-500 text-white py-2 rounded hover:bg-green-600 disabled:bg-gray-400 disabled:cursor-not-allowed"
            >
              {loading ? '登录中...' : '以访客身份进入'}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
