import { useState, useEffect } from 'react';
import { createApi } from '../api';

interface RequestLogsPageProps {
  token: string;
}

interface LogEntry {
  id: string;
  timestamp: string;
  model: string;
  provider: string;
  latency_ms: number;
  success: boolean;
  task_type: string;
  difficulty: number;
  input_tokens: number;
  output_tokens: number;
  cost: number;
  cost_currency: string;
  route_source: string;
  tenant_id: string;
  error_message: string;
  [key: string]: unknown;
}

export default function RequestLogsPage({ token }: RequestLogsPageProps) {
  const api = createApi(token);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [filterModel, setFilterModel] = useState('');
  const [filterProvider, setFilterProvider] = useState('');
  const [filterTaskType, setFilterTaskType] = useState('');
  const [filterSuccess, setFilterSuccess] = useState('');
  const [filterDateFrom, setFilterDateFrom] = useState('');
  const [filterDateTo, setFilterDateTo] = useState('');
  const [expandedRow, setExpandedRow] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const pageSize = 20;

  useEffect(() => {
    loadLogs();
  }, [page, filterModel, filterProvider, filterTaskType, filterSuccess]);

  const loadLogs = async () => {
    setLoading(true);
    try {
      const params: Record<string, unknown> = { page, page_size: pageSize };
      if (filterModel) params.model = filterModel;
      if (filterProvider) params.provider = filterProvider;
      if (filterTaskType) params.task_type = filterTaskType;
      if (filterSuccess) params.success = filterSuccess === 'true';
      if (filterDateFrom) params.date_from = filterDateFrom;
      if (filterDateTo) params.date_to = filterDateTo;
      const result = await api.listRequestLogs(params);
      setLogs(result.logs || result.items || []);
      setTotal(result.total || 0);
    } catch (err) {
      console.error('Failed to load logs:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleExport = async () => {
    setExporting(true);
    try {
      const params: Record<string, unknown> = { page: 1, page_size: 10000 };
      if (filterModel) params.model = filterModel;
      if (filterProvider) params.provider = filterProvider;
      if (filterTaskType) params.task_type = filterTaskType;
      if (filterSuccess) params.success = filterSuccess === 'true';
      const result = await api.listRequestLogs(params);
      const exportLogs = result.logs || result.items || [];
      // 生成CSV
      const headers = ['时间', '模型', '供应商', '延迟(ms)', '状态', '任务类型', '难度', '输入Token', '输出Token', '费用', '路由来源', '租户ID', '错误信息'];
      const rows = exportLogs.map((log: LogEntry) => [
        log.timestamp, log.model, log.provider || '', log.latency_ms,
        log.success ? '成功' : '失败', log.task_type || '',
        log.difficulty !== undefined ? log.difficulty.toFixed(2) : '',
        log.input_tokens || '', log.output_tokens || '',
        log.cost !== undefined ? log.cost.toFixed(6) : '',
        log.route_source || '', log.tenant_id || '',
        (log.error_message || '').replace(/"/g, '""'),
      ]);
      const csv = [headers.join(','), ...rows.map((r: (string | number)[]) => r.map((v) => `"${v}"`).join(','))].join('\n');
      const BOM = '\uFEFF';
      const blob = new Blob([BOM + csv], { type: 'text/csv;charset=utf-8;' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `request_logs_${new Date().toISOString().slice(0, 10)}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      alert('导出失败');
    } finally {
      setExporting(false);
    }
  };

  const totalPages = Math.ceil(total / pageSize) || 1;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold">请求日志</h2>
        <button
          onClick={handleExport}
          disabled={exporting}
          className="px-4 py-2 bg-green-500 text-white rounded hover:bg-green-600 text-sm disabled:opacity-50"
        >
          {exporting ? '导出中...' : '导出CSV'}
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3 mb-4">
        <input
          type="text"
          placeholder="按模型筛选"
          value={filterModel}
          onChange={(e) => { setFilterModel(e.target.value); setPage(1); }}
          className="border rounded px-2 py-1 text-sm"
        />
        <input
          type="text"
          placeholder="按供应商筛选"
          value={filterProvider}
          onChange={(e) => { setFilterProvider(e.target.value); setPage(1); }}
          className="border rounded px-2 py-1 text-sm"
        />
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
        <select
          value={filterSuccess}
          onChange={(e) => { setFilterSuccess(e.target.value); setPage(1); }}
          className="border rounded px-2 py-1 text-sm"
        >
          <option value="">全部状态</option>
          <option value="true">成功</option>
          <option value="false">失败</option>
        </select>
        <input
          type="date"
          value={filterDateFrom}
          onChange={(e) => { setFilterDateFrom(e.target.value); setPage(1); }}
          className="border rounded px-2 py-1 text-sm"
        />
        <input
          type="date"
          value={filterDateTo}
          onChange={(e) => { setFilterDateTo(e.target.value); setPage(1); }}
          className="border rounded px-2 py-1 text-sm"
        />
      </div>

      {loading ? (
        <div className="text-gray-500">加载中...</div>
      ) : (
        <>
          <div className="bg-white rounded-lg shadow overflow-x-auto">
            <table className="w-full text-sm min-w-[900px]">
              <thead className="bg-gray-50 text-gray-600">
                <tr>
                  <th className="px-3 py-3 text-left">时间</th>
                  <th className="px-3 py-3 text-left">模型</th>
                  <th className="px-3 py-3 text-left">供应商</th>
                  <th className="px-3 py-3 text-left">延迟</th>
                  <th className="px-3 py-3 text-left">状态</th>
                  <th className="px-3 py-3 text-left">任务类型</th>
                  <th className="px-3 py-3 text-left">难度</th>
                  <th className="px-3 py-3 text-left">Token</th>
                  <th className="px-3 py-3 text-left">费用</th>
                  <th className="px-3 py-3 text-left">路由</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((log) => (
                  <>
                    <tr
                      key={log.id}
                      className={`border-b hover:bg-gray-50 cursor-pointer ${expandedRow === log.id ? 'bg-blue-50' : ''}`}
                      onClick={() => setExpandedRow(expandedRow === log.id ? null : log.id)}
                    >
                      <td className="px-3 py-2 text-xs text-gray-500">{log.timestamp}</td>
                      <td className="px-3 py-2 font-medium">{log.model}</td>
                      <td className="px-3 py-2">{log.provider || '-'}</td>
                      <td className="px-3 py-2">{log.latency_ms}ms</td>
                      <td className="px-3 py-2">
                        <span className={`px-2 py-0.5 rounded text-xs ${log.success ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
                          {log.success ? '成功' : '失败'}
                        </span>
                      </td>
                      <td className="px-3 py-2">{log.task_type || '-'}</td>
                      <td className="px-3 py-2">{log.difficulty !== undefined ? log.difficulty.toFixed(2) : '-'}</td>
                      <td className="px-3 py-2 text-xs">
                        {(log.input_tokens || 0) > 0 && <span className="text-blue-600">{formatNum(log.input_tokens)}</span>}
                        {(log.input_tokens || 0) > 0 && (log.output_tokens || 0) > 0 && <span className="text-gray-400">/</span>}
                        {(log.output_tokens || 0) > 0 && <span className="text-green-600">{formatNum(log.output_tokens)}</span>}
                        {!log.input_tokens && !log.output_tokens && '-'}
                      </td>
                      <td className="px-3 py-2 text-xs">
                        {log.cost !== undefined && log.cost > 0 ? `${log.cost.toFixed(4)}` : '-'}
                      </td>
                      <td className="px-3 py-2 text-xs text-gray-500">{log.route_source || '-'}</td>
                    </tr>
                    {expandedRow === log.id && (
                      <tr key={`${log.id}-detail`} className="bg-gray-50">
                        <td colSpan={10} className="px-4 py-3">
                          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
                            <div><span className="text-gray-500">租户ID:</span> {log.tenant_id || '-'}</div>
                            <div><span className="text-gray-500">输入Token:</span> {log.input_tokens || 0}</div>
                            <div><span className="text-gray-500">输出Token:</span> {log.output_tokens || 0}</div>
                            <div><span className="text-gray-500">费用:</span> {log.cost !== undefined ? `${log.cost.toFixed(6)} ${log.cost_currency || ''}` : '-'}</div>
                            <div><span className="text-gray-500">路由来源:</span> {log.route_source || '-'}</div>
                            <div><span className="text-gray-500">任务类型:</span> {log.task_type || '-'}</div>
                            <div><span className="text-gray-500">难度:</span> {log.difficulty !== undefined ? log.difficulty.toFixed(2) : '-'}</div>
                            {log.error_message && (
                              <div className="col-span-4"><span className="text-gray-500">错误:</span> <span className="text-red-600">{log.error_message}</span></div>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </>
                ))}
                {logs.length === 0 && (
                  <tr><td colSpan={10} className="px-4 py-6 text-center text-gray-400">暂无请求日志</td></tr>
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

function formatNum(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}
