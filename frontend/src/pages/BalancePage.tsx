import { useState, useEffect, useRef } from 'react';
import { createApi } from '../api';
import { useToast } from '../components/Toast';
import * as echarts from 'echarts';

interface BalancePageProps {
  token: string;
}

interface ProviderBalance {
  provider: string;
  balance: number;
  currency: string;
  status: string;
  [key: string]: unknown;
}

interface TenantQuota {
  id: string;
  name: string;
  quota: Record<string, unknown>;
  usage: Record<string, unknown>;
  [key: string]: unknown;
}

export default function BalancePage({ token }: BalancePageProps) {
  const api = createApi(token);
  const toast = useToast();
  const [providerBalances, setProviderBalances] = useState<ProviderBalance[]>([]);
  const [tenantQuotas, setTenantQuotas] = useState<TenantQuota[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [syncingProvider, setSyncingProvider] = useState<string | null>(null);
  const [syncingAll, setSyncingAll] = useState(false);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setLoading(true);
    try {
      const [balanceResult, tenantsResult] = await Promise.all([
        api.getBalance().catch(() => ({ providers: [] })),
        api.listTenants().catch(() => ({ tenants: [] })),
      ]);
      setProviderBalances(balanceResult.providers || []);
      setTenantQuotas(tenantsResult.tenants || []);
    } catch (err) {
      console.error('Failed to load balance data:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await loadData();
    } finally {
      setRefreshing(false);
    }
  };

  const handleSyncProvider = async (providerName: string) => {
    setSyncingProvider(providerName);
    try {
      const result = await api.syncProviderBalance(providerName);
      if (result.status === 'ok') {
        toast.addToast(`${providerName} 余额同步成功`, 'success', `新余额: ${result.balance} ${result.currency || ''}`);
        await loadData();
      } else if (result.status === 'no_script') {
        toast.addToast(`${providerName} 未配置余额查询脚本`, 'warning', '请在供应商管理中配置余额查询脚本或手动设置余额');
      } else if (result.status === 'error') {
        toast.addToast(`${providerName} 余额同步失败`, 'error', result.message || '脚本执行失败');
      } else if (result.status === 'no_data') {
        toast.addToast(`${providerName} 脚本未返回余额数据`, 'warning', result.message || '');
      } else {
        toast.addToast(`${providerName} 同步返回未知状态`, 'warning', `状态: ${result.status}`);
      }
      await loadData();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.addToast(`${providerName} 余额同步失败`, 'error', msg);
    } finally {
      setSyncingProvider(null);
    }
  };

  const handleSyncAll = async () => {
    setSyncingAll(true);
    try {
      const result = await api.syncAllBalances();
      const results = result.results || [];
      const okCount = results.filter((r: { status: string }) => r.status === 'ok').length;
      const errCount = results.filter((r: { status: string }) => r.status === 'error').length;
      const noScriptCount = results.filter((r: { status: string }) => r.status === 'no_script').length;
      const noDataCount = results.filter((r: { status: string }) => r.status === 'no_data').length;

      if (errCount > 0) {
        const errProviders = results.filter((r: { status: string }) => r.status === 'error').map((r: { provider: string; message: string }) => `${r.provider}: ${r.message}`).join('; ');
        toast.addToast(`同步完成，${errCount} 个失败`, 'error', errProviders);
      }
      if (noScriptCount > 0) {
        toast.addToast(`${noScriptCount} 个供应商未配置余额查询脚本`, 'warning');
      }
      if (noDataCount > 0) {
        toast.addToast(`${noDataCount} 个供应商脚本未返回数据`, 'warning');
      }
      if (okCount > 0) {
        toast.addToast(`${okCount} 个供应商余额同步成功`, 'success');
      }
      if (okCount === 0 && errCount === 0 && noScriptCount === 0 && noDataCount === 0) {
        toast.addToast('没有可同步的供应商', 'info');
      }
      await loadData();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.addToast('全部同步失败', 'error', msg);
    } finally {
      setSyncingAll(false);
    }
  };

  const getStatusColor = (status: string) => {
    switch (status?.toLowerCase()) {
      case 'active':
      case 'normal':
      case 'ok':
        return 'bg-green-100 text-green-700';
      case 'low':
      case 'warning':
        return 'bg-yellow-100 text-yellow-700';
      case 'exhausted':
      case 'error':
      case 'disabled':
        return 'bg-red-100 text-red-700';
      default:
        return 'bg-gray-100 text-gray-700';
    }
  };

  const getStatusLabel = (status: string) => {
    switch (status?.toLowerCase()) {
      case 'active':
      case 'normal':
      case 'ok':
        return '正常';
      case 'low':
      case 'warning':
        return '余额不足';
      case 'exhausted':
        return '已耗尽';
      case 'error':
        return '异常';
      case 'disabled':
        return '已禁用';
      default:
        return status || '未知';
    }
  };

  if (loading) return <div className="text-gray-500">加载中...</div>;

  return (
    <div>
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between mb-6 gap-3">
        <h2 className="text-2xl font-bold">余额查询</h2>
        <div className="flex gap-2">
          <button
            onClick={handleSyncAll}
            disabled={syncingAll}
            className="px-4 py-2 bg-green-500 text-white rounded hover:bg-green-600 text-sm disabled:opacity-50"
          >
            {syncingAll ? '同步中...' : '全部同步'}
          </button>
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600 text-sm disabled:opacity-50"
          >
            {refreshing ? '刷新中...' : '刷新'}
          </button>
        </div>
      </div>

      {/* Provider Balance Cards */}
      <div className="mb-8">
        <h3 className="text-lg font-semibold mb-4">供应商余额</h3>
        {providerBalances.length === 0 ? (
          <div className="bg-white rounded-lg shadow p-6 text-center text-gray-400">暂无供应商余额数据</div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {providerBalances.map((pb) => (
              <div key={pb.provider} className="bg-white rounded-lg shadow p-4">
                <div className="flex items-center justify-between mb-2">
                  <h4 className="font-semibold text-gray-800">{pb.provider}</h4>
                  <div className="flex items-center gap-2">
                    <span className={`px-2 py-0.5 rounded text-xs ${getStatusColor(pb.status)}`}>
                      {getStatusLabel(pb.status)}
                    </span>
                    <button
                      onClick={() => handleSyncProvider(pb.provider)}
                      disabled={syncingProvider === pb.provider}
                      className="px-2 py-0.5 bg-green-100 text-green-700 rounded text-xs hover:bg-green-200 disabled:opacity-50"
                      title="同步余额"
                    >
                      {syncingProvider === pb.provider ? '同步中...' : '同步'}
                    </button>
                  </div>
                </div>
                <div className="text-2xl font-bold text-blue-600">
                  {typeof pb.balance === 'number' ? pb.balance.toFixed(2) : pb.balance}
                  <span className="text-sm font-normal text-gray-500 ml-1">{pb.currency || ''}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Provider Consumption Chart */}
      <div className="mb-8">
        <h3 className="text-lg font-semibold mb-4">供应商消耗对比</h3>
        <div className="bg-white rounded-lg shadow p-4">
          <ProviderConsumptionChart token={token} providers={providerBalances} />
        </div>
      </div>

      {/* Tenant Quota Cards */}
      <div>
        <h3 className="text-lg font-semibold mb-4">租户配额</h3>
        {tenantQuotas.length === 0 ? (
          <div className="bg-white rounded-lg shadow p-6 text-center text-gray-400">暂无租户数据</div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {tenantQuotas.map((tq) => {
              const quota = tq.quota as Record<string, number> || {};
              const usage = tq.usage as Record<string, number> || {};
              const dailyLimit = quota.daily_limit || 0;
              const dailyUsed = usage.daily_requests || 0;
              const monthlyTokenLimit = quota.monthly_token_limit || 0;
              const monthlyTokenUsed = usage.monthly_tokens || 0;
              const monthlyCostLimit = quota.monthly_cost_limit || 0;
              const monthlyCostUsed = usage.monthly_cost || 0;

              return (
                <div key={tq.id} className="bg-white rounded-lg shadow p-4">
                  <h4 className="font-semibold text-gray-800 mb-3">{tq.name}</h4>
                  <div className="space-y-2 text-sm">
                    {dailyLimit > 0 && (
                      <div>
                        <div className="flex justify-between text-gray-500">
                          <span>每日请求</span>
                          <span>{dailyUsed} / {dailyLimit}</span>
                        </div>
                        <div className="w-full bg-gray-200 rounded-full h-1.5 mt-1">
                          <div
                            className="bg-blue-500 h-1.5 rounded-full"
                            style={{ width: `${Math.min(100, (dailyUsed / dailyLimit) * 100)}%` }}
                          />
                        </div>
                      </div>
                    )}
                    {monthlyTokenLimit > 0 && (
                      <div>
                        <div className="flex justify-between text-gray-500">
                          <span>每月Token</span>
                          <span>{formatNum(monthlyTokenUsed)} / {formatNum(monthlyTokenLimit)}</span>
                        </div>
                        <div className="w-full bg-gray-200 rounded-full h-1.5 mt-1">
                          <div
                            className="bg-indigo-500 h-1.5 rounded-full"
                            style={{ width: `${Math.min(100, (monthlyTokenUsed / monthlyTokenLimit) * 100)}%` }}
                          />
                        </div>
                      </div>
                    )}
                    {monthlyCostLimit > 0 && (
                      <div>
                        <div className="flex justify-between text-gray-500">
                          <span>每月费用</span>
                          <span>¥{monthlyCostUsed.toFixed(2)} / ¥{monthlyCostLimit.toFixed(2)}</span>
                        </div>
                        <div className="w-full bg-gray-200 rounded-full h-1.5 mt-1">
                          <div
                            className="bg-orange-500 h-1.5 rounded-full"
                            style={{ width: `${Math.min(100, (monthlyCostUsed / monthlyCostLimit) * 100)}%` }}
                          />
                        </div>
                      </div>
                    )}
                    {dailyLimit === 0 && monthlyTokenLimit === 0 && monthlyCostLimit === 0 && (
                      <div className="text-gray-400 text-xs">未设置配额</div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function formatNum(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function ProviderConsumptionChart({ token, providers }: { token: string; providers: ProviderBalance[] }) {
  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstance = useRef<echarts.ECharts | null>(null);
  const api = createApi(token);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadChartData();
    return () => {
      chartInstance.current?.dispose();
    };
  }, [providers]);

  const loadChartData = async () => {
    if (!chartRef.current) return;
    setLoading(true);
    try {
      // 获取Dashboard数据来提取供应商消耗
      const dashboard = await api.getDashboard('today').catch(() => null);
      const providerCosts: Record<string, number> = {};

      if (dashboard?.provider_stats) {
        const stats = dashboard.provider_stats as Record<string, Record<string, number>>;
        for (const [name, s] of Object.entries(stats)) {
          providerCosts[name] = s.cost || s.total_cost || 0;
        }
      }

      // 合并余额数据
      const names = [...new Set([...providers.map((p) => p.provider), ...Object.keys(providerCosts)])];
      const balanceData = names.map((n) => {
        const p = providers.find((pb) => pb.provider === n);
        return typeof p?.balance === 'number' ? p.balance : 0;
      });
      const costData = names.map((n) => providerCosts[n] || 0);

      if (!chartInstance.current) {
        chartInstance.current = echarts.init(chartRef.current);
      }
      chartInstance.current.setOption({
        tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
        legend: { data: ['余额', '今日消耗'] },
        grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
        xAxis: { type: 'category', data: names },
        yAxis: { type: 'value' },
        series: [
          {
            name: '余额',
            type: 'bar',
            data: balanceData,
            itemStyle: { color: '#3b82f6' },
          },
          {
            name: '今日消耗',
            type: 'bar',
            data: costData,
            itemStyle: { color: '#f97316' },
          },
        ],
      });
    } catch (err) {
      console.error('Failed to load chart data:', err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      {loading && <div className="text-gray-400 text-sm mb-2">加载图表...</div>}
      <div ref={chartRef} style={{ width: '100%', height: 300 }} />
    </div>
  );
}
