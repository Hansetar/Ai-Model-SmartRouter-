import { useState, useEffect } from 'react';
import { createApi } from '../api';

interface NotificationsPageProps {
  token: string;
}

interface NotificationChannel {
  type: string;
  name: string;
  url?: string;
  bot_token?: string;
  chat_id?: string;
  smtp_host?: string;
  smtp_port?: number;
  smtp_user?: string;
  smtp_pass?: string;
  from?: string;
  to?: string;
  [key: string]: unknown;
}

const CHANNEL_TYPES = [
  { value: 'webhook', label: 'Webhook' },
  { value: 'dingtalk', label: '钉钉机器人' },
  { value: 'wecom', label: '企业微信机器人' },
  { value: 'feishu', label: '飞书机器人' },
  { value: 'telegram', label: 'Telegram' },
  { value: 'slack', label: 'Slack' },
  { value: 'email', label: '邮件' },
];

const emptyChannel: NotificationChannel = { type: 'webhook', name: '' };

export default function NotificationsPage({ token }: NotificationsPageProps) {
  const api = createApi(token);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [enabled, setEnabled] = useState(false);
  const [minSeverity, setMinSeverity] = useState('warning');
  const [channels, setChannels] = useState<NotificationChannel[]>([]);
  const [testing, setTesting] = useState<number | null>(null);

  useEffect(() => {
    loadConfig();
  }, []);

  const loadConfig = async () => {
    setLoading(true);
    try {
      const result = await api.getNotifications();
      setEnabled(Boolean(result.enabled));
      setMinSeverity(String(result.min_severity || 'warning'));
      setChannels((result.channels as NotificationChannel[]) || []);
    } catch (err) {
      console.error('Failed to load notifications config:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.updateNotifications({ enabled, min_severity: minSeverity, channels });
      alert('通知配置已保存');
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      alert('保存通知配置失败: ' + msg);
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async (index: number) => {
    setTesting(index);
    try {
      const ch = channels[index];
      // Transform channel object to API format: { channel: "type", config: {...} }
      const { type, name, ...config } = ch;
      const resp = await api.testNotification({ channel: type, config });
      if (resp.success) {
        alert('测试通知发送成功');
      } else {
        alert('测试通知发送失败: ' + (resp.error || '未知错误'));
      }
    } catch (err) {
      alert('测试通知发送失败');
    } finally {
      setTesting(null);
    }
  };

  const addChannel = () => {
    setChannels([...channels, { ...emptyChannel, name: `渠道 ${channels.length + 1}` }]);
  };

  const removeChannel = (index: number) => {
    setChannels(channels.filter((_, i) => i !== index));
  };

  const updateChannel = (index: number, field: string, value: unknown) => {
    const updated = [...channels];
    updated[index] = { ...updated[index], [field]: value };
    setChannels(updated);
  };

  if (loading) return <div className="text-gray-500">加载中...</div>;

  return (
    <div>
      <h2 className="text-2xl font-bold mb-6">通知配置</h2>

      <div className="bg-white rounded-lg shadow p-4 mb-6">
        <div className="flex items-center gap-4 mb-4">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
            启用通知
          </label>
          <div>
            <label className="block text-xs text-gray-500 mb-1">最低通知级别</label>
            <select
              className="border rounded px-2 py-1 text-sm"
              value={minSeverity}
              onChange={(e) => setMinSeverity(e.target.value)}
            >
              <option value="info">Info (所有通知)</option>
              <option value="warning">Warning (警告及以上)</option>
              <option value="critical">Critical (仅严重)</option>
            </select>
          </div>
        </div>

        <h3 className="font-semibold mb-3">通知渠道</h3>
        {channels.map((ch, idx) => (
          <div key={idx} className="border rounded p-3 mb-3">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <select
                  className="border rounded px-2 py-1 text-sm"
                  value={ch.type}
                  onChange={(e) => updateChannel(idx, 'type', e.target.value)}
                >
                  {CHANNEL_TYPES.map((ct) => (
                    <option key={ct.value} value={ct.value}>{ct.label}</option>
                  ))}
                </select>
                <input
                  className="border rounded px-2 py-1 text-sm w-32"
                  value={ch.name || ''}
                  onChange={(e) => updateChannel(idx, 'name', e.target.value)}
                  placeholder="渠道名称"
                />
              </div>
              <div className="flex gap-2">
                <button
                  onClick={() => handleTest(idx)}
                  disabled={testing === idx}
                  className="px-3 py-1 bg-green-500 text-white rounded text-xs hover:bg-green-600 disabled:opacity-50"
                >
                  {testing === idx ? '发送中...' : '测试'}
                </button>
                <button
                  onClick={() => removeChannel(idx)}
                  className="px-3 py-1 bg-red-500 text-white rounded text-xs hover:bg-red-600"
                >
                  删除
                </button>
              </div>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {/* Webhook / 钉钉 / 企业微信 / 飞书 / Slack */}
              {['webhook', 'dingtalk', 'wecom', 'feishu', 'slack'].includes(ch.type) && (
                <div className="col-span-2">
                  <label className="block text-xs text-gray-500 mb-1">Webhook URL</label>
                  <input
                    className="w-full border rounded px-2 py-1 text-sm"
                    value={ch.url || ''}
                    onChange={(e) => updateChannel(idx, 'url', e.target.value)}
                    placeholder="https://..."
                  />
                </div>
              )}
              {/* Telegram */}
              {ch.type === 'telegram' && (
                <>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">Bot Token</label>
                    <input
                      className="w-full border rounded px-2 py-1 text-sm"
                      value={ch.bot_token || ''}
                      onChange={(e) => updateChannel(idx, 'bot_token', e.target.value)}
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">Chat ID</label>
                    <input
                      className="w-full border rounded px-2 py-1 text-sm"
                      value={ch.chat_id || ''}
                      onChange={(e) => updateChannel(idx, 'chat_id', e.target.value)}
                    />
                  </div>
                </>
              )}
              {/* Email */}
              {ch.type === 'email' && (
                <>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">SMTP 服务器</label>
                    <input
                      className="w-full border rounded px-2 py-1 text-sm"
                      value={ch.smtp_host || ''}
                      onChange={(e) => updateChannel(idx, 'smtp_host', e.target.value)}
                      placeholder="smtp.example.com"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">SMTP 端口</label>
                    <input
                      type="number"
                      className="w-full border rounded px-2 py-1 text-sm"
                      value={ch.smtp_port || 587}
                      onChange={(e) => updateChannel(idx, 'smtp_port', Number(e.target.value))}
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">SMTP 用户</label>
                    <input
                      className="w-full border rounded px-2 py-1 text-sm"
                      value={ch.smtp_user || ''}
                      onChange={(e) => updateChannel(idx, 'smtp_user', e.target.value)}
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">SMTP 密码</label>
                    <input
                      type="password"
                      className="w-full border rounded px-2 py-1 text-sm"
                      value={ch.smtp_pass || ''}
                      onChange={(e) => updateChannel(idx, 'smtp_pass', e.target.value)}
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">发件人</label>
                    <input
                      className="w-full border rounded px-2 py-1 text-sm"
                      value={ch.from || ''}
                      onChange={(e) => updateChannel(idx, 'from', e.target.value)}
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">收件人(逗号分隔)</label>
                    <input
                      className="w-full border rounded px-2 py-1 text-sm"
                      value={ch.to || ''}
                      onChange={(e) => updateChannel(idx, 'to', e.target.value)}
                    />
                  </div>
                </>
              )}
            </div>
          </div>
        ))}
        <button
          onClick={addChannel}
          className="px-4 py-2 bg-gray-100 text-gray-700 rounded text-sm hover:bg-gray-200"
        >
          + 添加通知渠道
        </button>
      </div>

      <button
        onClick={handleSave}
        disabled={saving}
        className="px-6 py-2 bg-blue-500 text-white rounded hover:bg-blue-600 disabled:opacity-50"
      >
        {saving ? '保存中...' : '保存通知配置'}
      </button>
    </div>
  );
}
