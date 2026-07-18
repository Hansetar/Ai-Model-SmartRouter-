import { useState, useEffect } from 'react';
import { createApi, translateError } from '../api';
import { useToast } from '../components/Toast';

interface StorageConfigPageProps {
  token: string;
}

interface DbCheckResult {
  backend: string;
  url: string;
  path: string | null;
  exists: boolean;
  writable: boolean;
  valid: boolean;
  size: number;
  error: string | null;
  tables?: string[];
  parent_exists?: boolean;
}

export default function StorageConfigPage({ token }: StorageConfigPageProps) {
  const api = createApi(token);
  const toast = useToast();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dbCheck, setDbCheck] = useState<DbCheckResult | null>(null);
  const [checking, setChecking] = useState(false);
  const [repairing, setRepairing] = useState(false);
  const [showRepairConfirm, setShowRepairConfirm] = useState(false);

  const [storageConfig, setStorageConfig] = useState({
    type: 'sqlite',
    path: '',
    host: '',
    port: 0,
    database: '',
    username: '',
    password: '',
  });

  const [webConfig, setWebConfig] = useState({
    host: '0.0.0.0',
    port: 8000,
    workers: 1,
    cors_origins: '',
  });

  useEffect(() => {
    loadConfig();
    checkDatabase();
  }, []);

  const loadConfig = async () => {
    setLoading(true);
    try {
      const result = await api.getConfig();
      const sc = result.storage as Record<string, unknown> | undefined;
      if (sc) {
        setStorageConfig({
          type: String(sc.type || sc.backend || 'sqlite'),
          path: String(sc.path || ''),
          host: String(sc.host || ''),
          port: Number(sc.port || 0),
          database: String(sc.database || ''),
          username: String(sc.username || ''),
          password: String(sc.password || ''),
        });
      }
      const wc = result.web as Record<string, unknown> | undefined;
      if (wc) {
        setWebConfig({
          host: String(wc.host || '0.0.0.0'),
          port: Number(wc.port || 8000),
          workers: Number(wc.workers || 1),
          cors_origins: String(wc.cors_origins || ''),
        });
      }
    } catch (err) {
      const { message, detail } = translateError(err);
      toast.addToast(message, 'error', detail);
    } finally {
      setLoading(false);
    }
  };

  const checkDatabase = async () => {
    setChecking(true);
    try {
      const result = await api.checkDatabase();
      setDbCheck(result as DbCheckResult);

      // If database has issues, show repair prompt
      if (!result.exists || !result.valid) {
        setShowRepairConfirm(true);
      }
    } catch (err) {
      const { message, detail } = translateError(err);
      toast.addToast(message, 'error', detail);
    } finally {
      setChecking(false);
    }
  };

  const handleRepair = async () => {
    setRepairing(true);
    setShowRepairConfirm(false);
    try {
      await api.repairDatabase();
      toast.addToast('数据库修复成功', 'success');
      await checkDatabase();
    } catch (err) {
      const { message, detail } = translateError(err);
      toast.addToast(message, 'error', detail);
    } finally {
      setRepairing(false);
    }
  };

  const saveStorageConfig = async () => {
    setSaving(true);
    try {
      await api.updateStorageConfig(storageConfig);
      toast.addToast('存储配置已保存', 'success');
    } catch (err) {
      const { message, detail } = translateError(err);
      toast.addToast(message, 'error', detail);
    } finally {
      setSaving(false);
    }
  };

  const saveWebConfig = async () => {
    setSaving(true);
    try {
      await api.updateWebConfig(webConfig);
      toast.addToast('Web框架配置已保存', 'success');
    } catch (err) {
      const { message, detail } = translateError(err);
      toast.addToast(message, 'error', detail);
    } finally {
      setSaving(false);
    }
  };

  const formatFileSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  if (loading) return <div className="text-gray-500">加载中...</div>;

  return (
    <div>
      <h2 className="text-2xl font-bold mb-6">存储与框架配置</h2>

      {/* Database Health Check */}
      <div className="bg-white rounded-lg shadow p-4 mb-6">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold">数据库健康检查</h3>
          <button
            onClick={checkDatabase}
            disabled={checking}
            className="px-3 py-1 bg-gray-200 text-gray-700 rounded text-sm hover:bg-gray-300 disabled:opacity-50"
          >
            {checking ? '检测中...' : '重新检测'}
          </button>
        </div>

        {dbCheck && (
          <div className="space-y-2">
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
              <div>
                <span className="text-gray-500">类型</span>
                <div className="font-medium">{dbCheck.backend}</div>
              </div>
              <div>
                <span className="text-gray-500">路径</span>
                <div className="font-mono text-xs break-all">{dbCheck.path || dbCheck.url}</div>
              </div>
              <div>
                <span className="text-gray-500">文件状态</span>
                <div>
                  {dbCheck.exists ? (
                    <span className="text-green-600">存在 ({formatFileSize(dbCheck.size)})</span>
                  ) : (
                    <span className="text-red-600">不存在</span>
                  )}
                </div>
              </div>
              <div>
                <span className="text-gray-500">可写</span>
                <div>
                  {dbCheck.writable ? (
                    <span className="text-green-600">是</span>
                  ) : (
                    <span className="text-red-600">否</span>
                  )}
                </div>
              </div>
              <div>
                <span className="text-gray-500">格式有效</span>
                <div>
                  {dbCheck.valid ? (
                    <span className="text-green-600">有效</span>
                  ) : (
                    <span className="text-red-600">无效</span>
                  )}
                </div>
              </div>
              {dbCheck.tables && (
                <div className="col-span-2">
                  <span className="text-gray-500">数据表</span>
                  <div className="text-xs font-mono">{dbCheck.tables.join(', ')}</div>
                </div>
              )}
              {dbCheck.error && (
                <div className="col-span-4">
                  <span className="text-gray-500">错误信息</span>
                  <div className="text-red-600 text-xs">{dbCheck.error}</div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Repair confirmation dialog */}
        {showRepairConfirm && dbCheck && (!dbCheck.exists || !dbCheck.valid) && (
          <div className="mt-4 bg-yellow-50 border border-yellow-200 rounded-lg p-4">
            <div className="flex items-start gap-2">
              <svg className="w-5 h-5 text-yellow-500 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
              </svg>
              <div>
                <h4 className="font-semibold text-yellow-800">数据库问题检测</h4>
                <p className="text-sm text-yellow-700 mt-1">
                  {!dbCheck.exists
                    ? '数据库文件不存在，需要创建新的数据库文件。'
                    : '数据库格式无效，可能已损坏。'}
                </p>
                <p className="text-xs text-yellow-600 mt-1">
                  修复操作将备份现有文件并重新创建数据库。现有数据将丢失。
                </p>
                <div className="flex gap-2 mt-3">
                  <button
                    onClick={handleRepair}
                    disabled={repairing}
                    className="px-3 py-1.5 bg-yellow-500 text-white rounded text-sm hover:bg-yellow-600 disabled:opacity-50"
                  >
                    {repairing ? '修复中...' : '确认修复'}
                  </button>
                  <button
                    onClick={() => setShowRepairConfirm(false)}
                    className="px-3 py-1.5 bg-gray-200 text-gray-700 rounded text-sm hover:bg-gray-300"
                  >
                    取消
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Storage Config */}
      <div className="bg-white rounded-lg shadow p-4 mb-6">
        <h3 className="font-semibold mb-3">存储配置</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          <div>
            <label className="block text-xs text-gray-500 mb-1">存储类型</label>
            <select
              className="w-full border rounded px-2 py-1 text-sm"
              value={storageConfig.type}
              onChange={(e) => setStorageConfig({ ...storageConfig, type: e.target.value })}
            >
              <option value="sqlite">SQLite</option>
              <option value="postgresql">PostgreSQL</option>
              <option value="mysql">MySQL</option>
            </select>
          </div>
          {storageConfig.type === 'sqlite' ? (
            <div>
              <label className="block text-xs text-gray-500 mb-1">数据库路径</label>
              <input
                className="w-full border rounded px-2 py-1 text-sm"
                value={storageConfig.path}
                onChange={(e) => setStorageConfig({ ...storageConfig, path: e.target.value })}
                placeholder="data/smart_router.db"
              />
              <div className="text-xs text-gray-400 mt-1">默认: data/smart_router.db (容器内 /app/data/smart_router.db)</div>
            </div>
          ) : (
            <>
              <div>
                <label className="block text-xs text-gray-500 mb-1">主机</label>
                <input
                  className="w-full border rounded px-2 py-1 text-sm"
                  value={storageConfig.host}
                  onChange={(e) => setStorageConfig({ ...storageConfig, host: e.target.value })}
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">端口</label>
                <input
                  type="number"
                  className="w-full border rounded px-2 py-1 text-sm"
                  value={storageConfig.port}
                  onChange={(e) => setStorageConfig({ ...storageConfig, port: Number(e.target.value) })}
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">数据库名</label>
                <input
                  className="w-full border rounded px-2 py-1 text-sm"
                  value={storageConfig.database}
                  onChange={(e) => setStorageConfig({ ...storageConfig, database: e.target.value })}
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">用户名</label>
                <input
                  className="w-full border rounded px-2 py-1 text-sm"
                  value={storageConfig.username}
                  onChange={(e) => setStorageConfig({ ...storageConfig, username: e.target.value })}
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">密码</label>
                <input
                  type="password"
                  className="w-full border rounded px-2 py-1 text-sm"
                  value={storageConfig.password}
                  onChange={(e) => setStorageConfig({ ...storageConfig, password: e.target.value })}
                />
              </div>
            </>
          )}
        </div>
        <button
          onClick={saveStorageConfig}
          disabled={saving}
          className="mt-3 px-4 py-2 bg-blue-500 text-white rounded text-sm hover:bg-blue-600 disabled:opacity-50"
        >
          {saving ? '保存中...' : '保存存储配置'}
        </button>
      </div>

      {/* Web Config */}
      <div className="bg-white rounded-lg shadow p-4">
        <h3 className="font-semibold mb-3">Web 框架配置</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div>
            <label className="block text-xs text-gray-500 mb-1">监听地址</label>
            <input
              className="w-full border rounded px-2 py-1 text-sm"
              value={webConfig.host}
              onChange={(e) => setWebConfig({ ...webConfig, host: e.target.value })}
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">端口</label>
            <input
              type="number"
              className="w-full border rounded px-2 py-1 text-sm"
              value={webConfig.port}
              onChange={(e) => setWebConfig({ ...webConfig, port: Number(e.target.value) })}
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Worker数</label>
            <input
              type="number"
              className="w-full border rounded px-2 py-1 text-sm"
              value={webConfig.workers}
              onChange={(e) => setWebConfig({ ...webConfig, workers: Number(e.target.value) })}
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">CORS来源(逗号分隔)</label>
            <input
              className="w-full border rounded px-2 py-1 text-sm"
              value={webConfig.cors_origins}
              onChange={(e) => setWebConfig({ ...webConfig, cors_origins: e.target.value })}
            />
          </div>
        </div>
        <button
          onClick={saveWebConfig}
          disabled={saving}
          className="mt-3 px-4 py-2 bg-blue-500 text-white rounded text-sm hover:bg-blue-600 disabled:opacity-50"
        >
          {saving ? '保存中...' : '保存Web配置'}
        </button>
      </div>
    </div>
  );
}
