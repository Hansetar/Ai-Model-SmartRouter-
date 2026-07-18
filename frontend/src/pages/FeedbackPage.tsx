import { useState, useEffect } from 'react';
import ReactECharts from 'echarts-for-react';
import { createApi } from '../api';

interface FeedbackPageProps {
  token: string;
}

interface FeedbackEntry {
  id: string;
  timestamp: string;
  model: string;
  rating: number;
  comment: string;
  [key: string]: unknown;
}

export default function FeedbackPage({ token }: FeedbackPageProps) {
  const api = createApi(token);
  const [feedback, setFeedback] = useState<FeedbackEntry[]>([]);
  const [stats, setStats] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const pageSize = 20;

  useEffect(() => {
    loadData();
  }, [page]);

  const loadData = async () => {
    setLoading(true);
    try {
      const [feedbackResult, statsResult] = await Promise.all([
        api.listFeedback({ page, page_size: pageSize }),
        api.getFeedbackStats(),
      ]);
      setFeedback(feedbackResult.feedback || feedbackResult.items || []);
      setTotal(feedbackResult.total || 0);
      setStats(statsResult);
    } catch (err) {
      console.error('Failed to load feedback:', err);
    } finally {
      setLoading(false);
    }
  };

  const totalPages = Math.ceil(total / pageSize) || 1;

  const positive = Number(stats?.positive || 0);
  const negative = Number(stats?.negative || 0);

  const pieOption = {
    tooltip: { trigger: 'item' as const },
    series: [
      {
        type: 'pie' as const,
        radius: ['40%', '70%'],
        data: [
          { name: '正面', value: positive, itemStyle: { color: '#22c55e' } },
          { name: '负面', value: negative, itemStyle: { color: '#ef4444' } },
        ],
        emphasis: {
          itemStyle: { shadowBlur: 10, shadowOffsetX: 0, shadowColor: 'rgba(0,0,0,0.5)' },
        },
      },
    ],
  };

  return (
    <div>
      <h2 className="text-2xl font-bold mb-6">反馈管理</h2>

      {/* Stats */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-6">
        <div className="bg-white rounded-lg shadow p-4">
          <div className="text-sm text-gray-500">总反馈数</div>
          <div className="text-2xl font-bold mt-1 text-blue-600">{positive + negative}</div>
        </div>
        <div className="bg-white rounded-lg shadow p-4">
          <div className="text-sm text-gray-500">正面比例</div>
          <div className="text-2xl font-bold mt-1 text-green-600">
            {positive + negative > 0 ? `${((positive / (positive + negative)) * 100).toFixed(1)}%` : '-'}
          </div>
        </div>
        <div className="bg-white rounded-lg shadow p-4">
          <h3 className="text-sm text-gray-500 mb-2">反馈分布</h3>
          <ReactECharts option={pieOption} style={{ height: 120 }} />
        </div>
      </div>

      {loading ? (
        <div className="text-gray-500">加载中...</div>
      ) : (
        <>
          <div className="bg-white rounded-lg shadow overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-gray-600">
                <tr>
                  <th className="px-4 py-3 text-left">时间</th>
                  <th className="px-4 py-3 text-left">模型</th>
                  <th className="px-4 py-3 text-left">评分</th>
                  <th className="px-4 py-3 text-left">评论</th>
                </tr>
              </thead>
              <tbody>
                {feedback.map((f) => (
                  <tr key={f.id} className="border-b hover:bg-gray-50">
                    <td className="px-4 py-3 text-xs text-gray-500">{f.timestamp}</td>
                    <td className="px-4 py-3 font-medium">{f.model}</td>
                    <td className="px-4 py-3">
                      <span className={`px-2 py-0.5 rounded text-xs ${f.rating >= 4 ? 'bg-green-100 text-green-700' : f.rating >= 3 ? 'bg-yellow-100 text-yellow-700' : 'bg-red-100 text-red-700'}`}>
                        {f.rating}/5
                      </span>
                    </td>
                    <td className="px-4 py-3 max-w-[300px] truncate">{f.comment || '-'}</td>
                  </tr>
                ))}
                {feedback.length === 0 && (
                  <tr><td colSpan={4} className="px-4 py-6 text-center text-gray-400">暂无反馈</td></tr>
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
