import { useState, useEffect, useMemo } from 'react';
import ReactECharts from 'echarts-for-react';
import { createApi } from '../api';

interface TenantUsagePageProps {
  token: string;
}

type Dimension = 'tenant' | 'tenant_model' | 'tenant_time';
type TimeRange = 'day' | 'week' | 'month' | 'all';

const dimensionLabels: Record<Dimension, string> = {
  tenant: '按租户',
  tenant_model: '按租户+模型',
  tenant_time: '按租户+时间',
};

const timeRangeLabels: Record<TimeRange, string> = {
  day: '日',
  week: '周',
  month: '月',
  all: '全部',
};

interface UsageRecord {
  tenant_id: string;
  tenant_name: string;
  model?: string;
  date?: string;
  requests: number;
  cost: number;
  input_tokens: number;
  output_tokens: number;
  [key: string]: unknown;
}

export default function TenantUsagePage({ token }: TenantUsagePageProps) {
  const api = createApi(token);
  const [data, setData] = useState<UsageRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [dimension, setDimension] = useState<Dimension>('tenant');
  const [timeRange, setTimeRange] = useState<TimeRange>('month');

  useEffect(() => {
    loadData();
  }, [dimension, timeRange]);

  const loadData = async () => {
    setLoading(true);
    try {
      const result = await api.getTenantUsage({ dimension, time_range: timeRange });
      setData(result.records || []);
    } catch (err) {
      console.error('Failed to load tenant usage:', err);
    } finally {
      setLoading(false);
    }
  };

  const chartOption = useMemo(() => {
    if (data.length === 0) return {};

    if (dimension === 'tenant') {
      const tenants = data.map((d) => d.tenant_name);
      const requests = data.map((d) => d.requests);
      const costs = data.map((d) => d.cost);

      return {
        tooltip: { trigger: 'axis' as const },
        legend: { data: ['请求数', '费用(¥)'] },
        xAxis: { type: 'category' as const, data: tenants, axisLabel: { rotate: 30 } },
        yAxis: [
          { type: 'value' as const, name: '请求数' },
          { type: 'value' as const, name: '费用(¥)' },
        ],
        series: [
          {
            name: '请求数',
            type: 'bar' as const,
            data: requests,
            itemStyle: { color: '#3b82f6' },
          },
          {
            name: '费用(¥)',
            type: 'bar' as const,
            yAxisIndex: 1,
            data: costs,
            itemStyle: { color: '#f97316' },
          },
        ],
        grid: { left: 60, right: 60, top: 40, bottom: 60 },
      };
    }

    if (dimension === 'tenant_model') {
      const tenantSet = new Set(data.map((d) => d.tenant_name));
      const modelSet = new Set(data.map((d) => d.model || '未知'));
      const tenants = Array.from(tenantSet);
      const models = Array.from(modelSet);

      const series = models.map((model) => ({
        name: model,
        type: 'bar' as const,
        stack: 'total',
        data: tenants.map((tenant) => {
          const record = data.find((d) => d.tenant_name === tenant && (d.model || '未知') === model);
          return record?.requests || 0;
        }),
      }));

      return {
        tooltip: { trigger: 'axis' as const },
        legend: { data: models, type: 'scroll' as const },
        xAxis: { type: 'category' as const, data: tenants, axisLabel: { rotate: 30 } },
        yAxis: { type: 'value' as const, name: '请求数' },
        series,
        grid: { left: 60, right: 20, top: 60, bottom: 60 },
      };
    }

    if (dimension === 'tenant_time') {
      const tenantSet = new Set(data.map((d) => d.tenant_name));
      const dateSet = new Set(data.map((d) => d.date || ''));
      const dates = Array.from(dateSet).sort();
      const tenants = Array.from(tenantSet);

      const series = tenants.map((tenant) => ({
        name: tenant,
        type: 'line' as const,
        smooth: true,
        data: dates.map((date) => {
          const record = data.find((d) => d.tenant_name === tenant && d.date === date);
          return record?.requests || 0;
        }),
      }));

      return {
        tooltip: { trigger: 'axis' as const },
        legend: { data: tenants, type: 'scroll' as const },
        xAxis: { type: 'category' as const, data: dates },
        yAxis: { type: 'value' as const, name: '请求数' },
        series,
        grid: { left: 60, right: 20, top: 60, bottom: 30 },
      };
    }

    return {};
  }, [data, dimension]);

  if (loading) return <div className="text-gray-500">加载中...</div>;

  return (
    <div>
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between mb-6 gap-3">
        <h2 className="text-2xl font-bold">租户消耗统计</h2>
        <div className="flex gap-2 flex-wrap">
          {(Object.keys(dimensionLabels) as Dimension[]).map((d) => (
            <button
              key={d}
              onClick={() => setDimension(d)}
              className={`px-3 py-1 rounded text-sm ${
                dimension === d ? 'bg-blue-500 text-white' : 'bg-gray-200 text-gray-600 hover:bg-gray-300'
              }`}
            >
              {dimensionLabels[d]}
            </button>
          ))}
          <span className="text-gray-300 mx-1">|</span>
          {(Object.keys(timeRangeLabels) as TimeRange[]).map((t) => (
            <button
              key={t}
              onClick={() => setTimeRange(t)}
              className={`px-3 py-1 rounded text-sm ${
                timeRange === t ? 'bg-green-500 text-white' : 'bg-gray-200 text-gray-600 hover:bg-gray-300'
              }`}
            >
              {timeRangeLabels[t]}
            </button>
          ))}
        </div>
      </div>

      {/* Chart */}
      {data.length > 0 ? (
        <div className="bg-white rounded-lg shadow p-4 mb-6">
          <ReactECharts option={chartOption} style={{ height: 400 }} />
        </div>
      ) : (
        <div className="bg-white rounded-lg shadow p-6 mb-6 text-center text-gray-400">暂无数据</div>
      )}

      {/* Data Table */}
      <div className="bg-white rounded-lg shadow overflow-x-auto">
        <table className="w-full text-sm min-w-[600px]">
          <thead className="bg-gray-50 text-gray-600">
            <tr>
              <th className="px-4 py-3 text-left">租户</th>
              {dimension === 'tenant_model' && <th className="px-4 py-3 text-left">模型</th>}
              {dimension === 'tenant_time' && <th className="px-4 py-3 text-left">日期</th>}
              <th className="px-4 py-3 text-right">请求数</th>
              <th className="px-4 py-3 text-right">费用(¥)</th>
              <th className="px-4 py-3 text-right">输入Token</th>
              <th className="px-4 py-3 text-right">输出Token</th>
            </tr>
          </thead>
          <tbody>
            {data.map((d, i) => (
              <tr key={i} className="border-b hover:bg-gray-50">
                <td className="px-4 py-3 font-medium">{d.tenant_name}</td>
                {dimension === 'tenant_model' && <td className="px-4 py-3">{d.model || '-'}</td>}
                {dimension === 'tenant_time' && <td className="px-4 py-3">{d.date || '-'}</td>}
                <td className="px-4 py-3 text-right">{formatNum(d.requests)}</td>
                <td className="px-4 py-3 text-right">{d.cost.toFixed(4)}</td>
                <td className="px-4 py-3 text-right">{formatNum(d.input_tokens)}</td>
                <td className="px-4 py-3 text-right">{formatNum(d.output_tokens)}</td>
              </tr>
            ))}
            {data.length === 0 && (
              <tr><td colSpan={6} className="px-4 py-6 text-center text-gray-400">暂无数据</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function formatNum(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}
