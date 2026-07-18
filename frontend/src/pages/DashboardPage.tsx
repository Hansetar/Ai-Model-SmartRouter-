import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import ReactECharts from 'echarts-for-react';
import { createApi } from '../api';

// Gradient refresh intervals: stats 1s, charts 5s, extra data 30s
const REFRESH_STATS_MS = 1000;
const REFRESH_CHARTS_MS = 5000;
const REFRESH_EXTRA_MS = 30000;

const periods = [
  { value: 'today', label: '今日' },
  { value: 'week', label: '本周' },
  { value: 'month', label: '本月' },
  { value: 'year', label: '今年' },
  { value: 'all', label: '全部' },
] as const;

const periodLabels: Record<string, string> = {
  today: '今日',
  week: '本周',
  month: '本月',
  year: '今年',
  all: '累计',
};

interface DashboardPageProps {
  token: string;
}

interface ProviderBalance {
  provider: string;
  balance: number;
  currency: string;
  status: string;
  [key: string]: unknown;
}

interface TenantUsageRecord {
  tenant_name: string;
  requests: number;
  cost: number;
  [key: string]: unknown;
}

// 示例数据 - 用于无数据时展示
const DEMO_STATS = {
  total_interceptions: 1280,
  saved_cost: 15.68,
  total_input_tokens: 2450000,
  total_output_tokens: 890000,
  avg_latency_ms: 342,
  success_rate: 98.5,
};

const DEMO_TREND_LABELS = ['00:00', '04:00', '08:00', '12:00', '16:00', '20:00'];
const DEMO_TREND_VALUES = [12, 28, 65, 89, 72, 45];
const DEMO_MODEL_DIST = [
  { name: 'gpt-4o', value: 35 },
  { name: 'deepseek-v3', value: 28 },
  { name: 'claude-3.5', value: 22 },
  { name: 'qwen-max', value: 15 },
];

export default function DashboardPage({ token }: DashboardPageProps) {
  const api = createApi(token);
  const navigate = useNavigate();
  const [period, setPeriod] = useState<string>('today');
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [providerBalances, setProviderBalances] = useState<ProviderBalance[]>([]);
  const [tenantUsage, setTenantUsage] = useState<TenantUsageRecord[]>([]);
  const [hasProviders, setHasProviders] = useState<boolean | null>(null);
  const [hasModels, setHasModels] = useState<boolean | null>(null);
  const [showGuide, setShowGuide] = useState(false);
  const [showDemo, setShowDemo] = useState(false);
  const [liveEnabled, setLiveEnabled] = useState(true);
  const [lastStatsUpdate, setLastStatsUpdate] = useState<number>(0);
  const [lastChartsUpdate, setLastChartsUpdate] = useState<number>(0);
  const statsTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const chartsTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const extraTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const loadingRef = useRef(false);

  // Initial load
  useEffect(() => {
    loadDashboard();
  }, [period]);

  useEffect(() => {
    loadExtraData();
    checkSetupState();
  }, []);

  // Gradient real-time update timers
  useEffect(() => {
    if (!liveEnabled) {
      // Clear all timers when live is disabled
      if (statsTimerRef.current) clearInterval(statsTimerRef.current);
      if (chartsTimerRef.current) clearInterval(chartsTimerRef.current);
      if (extraTimerRef.current) clearInterval(extraTimerRef.current);
      statsTimerRef.current = null;
      chartsTimerRef.current = null;
      extraTimerRef.current = null;
      return;
    }

    // Level 1: Stats refresh every 1s (lightweight - just key numbers)
    statsTimerRef.current = setInterval(() => {
      if (loadingRef.current) return;
      loadDashboardStats();
    }, REFRESH_STATS_MS);

    // Level 2: Charts refresh every 5s (medium - trend + distribution)
    chartsTimerRef.current = setInterval(() => {
      if (loadingRef.current) return;
      loadDashboardCharts();
    }, REFRESH_CHARTS_MS);

    // Level 3: Extra data refresh every 30s (heavy - balance + tenant usage)
    extraTimerRef.current = setInterval(() => {
      loadExtraData();
    }, REFRESH_EXTRA_MS);

    return () => {
      if (statsTimerRef.current) clearInterval(statsTimerRef.current);
      if (chartsTimerRef.current) clearInterval(chartsTimerRef.current);
      if (extraTimerRef.current) clearInterval(extraTimerRef.current);
    };
  }, [liveEnabled, period]);

  // Check if this is a fresh install
  const checkSetupState = async () => {
    try {
      const [providersResult, modelsResult] = await Promise.all([
        api.listProviders().catch(() => ({ providers: [] })),
        api.getModels().catch(() => ({ models: [] })),
      ]);
      const providers = providersResult.providers || [];
      const models = modelsResult.models || [];
      setHasProviders(providers.length > 0);
      setHasModels(models.length > 0);

      // Fresh install: no providers and no models
      if (providers.length === 0 && models.length === 0) {
        setShowGuide(true);
      }
    } catch {
      setHasProviders(false);
      setHasModels(false);
    }
  };

  const loadDashboard = async () => {
    loadingRef.current = true;
    setLoading(true);
    try {
      const result = await api.getDashboard(period);
      setData(result);
      setLastStatsUpdate(Date.now());
      setLastChartsUpdate(Date.now());
    } catch (err) {
      console.error('Failed to load dashboard:', err);
    } finally {
      setLoading(false);
      loadingRef.current = false;
    }
  };

  // Lightweight stats-only refresh (1s interval)
  const loadDashboardStats = useCallback(async () => {
    try {
      const result = await api.getDashboard(period);
      setData((prev) => prev ? { ...prev, ...result } : result);
      setLastStatsUpdate(Date.now());
    } catch {
      // Silently fail for background refresh
    }
  }, [api, period]);

  // Charts refresh (5s interval) - same API but marks charts as updated
  const loadDashboardCharts = useCallback(async () => {
    try {
      const result = await api.getDashboard(period);
      setData((prev) => prev ? { ...prev, ...result } : result);
      setLastChartsUpdate(Date.now());
    } catch {
      // Silently fail for background refresh
    }
  }, [api, period]);

  const loadExtraData = async () => {
    try {
      const [balanceResult, usageResult] = await Promise.all([
        api.getBalance().catch(() => ({ providers: [] })),
        api.getTenantUsage({ group_by: 'tenant', period: 'month' }).catch(() => ({ data: [] })),
      ]);
      setProviderBalances(balanceResult.providers || []);
      setTenantUsage(usageResult.data || usageResult.records || []);
    } catch (err) {
      console.error('Failed to load extra data:', err);
    }
  };

  const label = periodLabels[period] || '今日';

  // Determine if we have real data
  const hasRealData = data && (data.total_interceptions as number) > 0;
  const isDataEmpty = !loading && (data === null || (data.total_interceptions as number) === 0);

  // Request trend chart option
  const trendOption = useMemo(() => {
    const isDemo = showDemo || !hasRealData;
    const labels = (data?.trend_labels as string[]) || (isDemo ? DEMO_TREND_LABELS : []);
    const values = (data?.trend_values as number[]) || (isDemo ? DEMO_TREND_VALUES : []);

    return {
      tooltip: { trigger: 'axis' as const },
      xAxis: {
        type: 'category' as const,
        data: labels,
      },
      yAxis: { type: 'value' as const },
      series: [
        {
          name: '请求数',
          type: 'line' as const,
          data: values,
          smooth: true,
          areaStyle: { opacity: isDemo ? 0.08 : 0.15 },
          itemStyle: { color: isDemo ? '#9ca3af' : '#3b82f6' },
          lineStyle: isDemo ? { type: 'dashed' as const, color: '#9ca3af' } : undefined,
        },
      ],
      grid: { left: 50, right: 20, top: 20, bottom: 30 },
    };
  }, [data, hasRealData, showDemo]);

  // Model distribution pie chart option
  const modelDistOption = useMemo(() => {
    const isDemo = showDemo || !hasRealData;
    const distData = (data?.model_distribution as { name: string; value: number }[]) || (isDemo ? DEMO_MODEL_DIST : []);

    return {
      tooltip: { trigger: 'item' as const },
      series: [
        {
          type: 'pie' as const,
          radius: ['40%', '70%'],
          data: distData,
          emphasis: {
            itemStyle: { shadowBlur: 10, shadowOffsetX: 0, shadowColor: 'rgba(0,0,0,0.5)' },
          },
          itemStyle: isDemo ? { opacity: 0.4 } : undefined,
          label: isDemo ? { color: '#9ca3af' } : undefined,
        },
      ],
    };
  }, [data, hasRealData, showDemo]);

  // Cost trend chart option
  const costTrendOption = useMemo(() => {
    const costLabels = (data?.cost_trend_labels as string[]) || (data?.trend_labels as string[]) || [];
    const costValues = (data?.cost_trend_values as number[]) || [];
    if (costValues.length === 0 && !showDemo) return null;
    const isDemo = showDemo && costValues.length === 0;
    return {
      tooltip: { trigger: 'axis' as const, formatter: (params: unknown[]) => {
        const p = params[0] as { name: string; value: number };
        return `${p.name}<br/>费用: ¥${p.value.toFixed(4)}`;
      }},
      xAxis: { type: 'category' as const, data: isDemo ? DEMO_TREND_LABELS : costLabels },
      yAxis: { type: 'value' as const, name: '费用(¥)' },
      series: [
        {
          name: '费用',
          type: 'line' as const,
          data: isDemo ? [0.5, 1.2, 3.8, 5.2, 3.1, 2.4] : costValues,
          smooth: true,
          areaStyle: { opacity: isDemo ? 0.08 : 0.15 },
          itemStyle: { color: isDemo ? '#9ca3af' : '#f97316' },
          lineStyle: isDemo ? { type: 'dashed' as const, color: '#9ca3af' } : undefined,
        },
      ],
      grid: { left: 60, right: 20, top: 20, bottom: 30 },
    };
  }, [data, showDemo]);

  // Tenant usage bar chart
  const tenantUsageOption = useMemo(() => {
    if (tenantUsage.length === 0) return null;
    return {
      tooltip: { trigger: 'axis' as const },
      legend: { data: ['请求数', '费用(¥)'] },
      xAxis: {
        type: 'category' as const,
        data: tenantUsage.map((t) => t.tenant_name),
        axisLabel: { rotate: 30 },
      },
      yAxis: [
        { type: 'value' as const, name: '请求数' },
        { type: 'value' as const, name: '费用(¥)' },
      ],
      series: [
        {
          name: '请求数',
          type: 'bar' as const,
          data: tenantUsage.map((t) => t.requests),
          itemStyle: { color: '#3b82f6' },
        },
        {
          name: '费用(¥)',
          type: 'bar' as const,
          yAxisIndex: 1,
          data: tenantUsage.map((t) => t.cost),
          itemStyle: { color: '#f97316' },
        },
      ],
      grid: { left: 60, right: 60, top: 40, bottom: 60 },
    };
  }, [tenantUsage]);

  // Guide page for fresh install
  if (showGuide && hasProviders === false && hasModels === false) {
    return (
      <div className="min-h-[80vh] flex items-center justify-center">
        <div className="max-w-lg w-full text-center">
          <div className="mb-8">
            <div className="w-24 h-24 mx-auto mb-6 bg-blue-100 rounded-full flex items-center justify-center">
              <svg className="w-12 h-12 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M13 10V3L4 14h7v7l9-11h-7z" />
              </svg>
            </div>
            <h2 className="text-2xl font-bold text-gray-800 mb-2">欢迎使用 SmartRouter</h2>
            <p className="text-gray-500">智能 LLM 模型路由引擎，开始配置您的第一个供应商和模型</p>
          </div>

          <div className="space-y-4">
            {/* Step 1: Add Provider */}
            <button
              type="button"
              className="w-full bg-white rounded-lg shadow p-5 text-left cursor-pointer hover:shadow-md transition-shadow border-l-4 border-blue-500"
              onClick={() => navigate('/providers')}
            >
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 bg-blue-500 text-white rounded-full flex items-center justify-center text-sm font-bold">1</div>
                <div>
                  <h3 className="font-semibold text-gray-800">添加供应商</h3>
                  <p className="text-sm text-gray-500">配置 API 供应商（如 OpenAI、DeepSeek、Anthropic 等）</p>
                </div>
              </div>
            </button>

            {/* Step 2: Add Model */}
            <button
              type="button"
              className="w-full bg-white rounded-lg shadow p-5 text-left cursor-pointer hover:shadow-md transition-shadow border-l-4 border-indigo-500"
              onClick={() => navigate('/models')}
            >
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 bg-indigo-500 text-white rounded-full flex items-center justify-center text-sm font-bold">2</div>
                <div>
                  <h3 className="font-semibold text-gray-800">添加模型</h3>
                  <p className="text-sm text-gray-500">配置可用的 LLM 模型及其参数</p>
                </div>
              </div>
            </button>

            {/* Step 3: Start Using */}
            <button
              type="button"
              className="w-full bg-white rounded-lg shadow p-5 text-left cursor-pointer hover:shadow-md transition-shadow border-l-4 border-green-500"
              onClick={() => { setShowGuide(false); setShowDemo(true); }}
            >
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 bg-green-500 text-white rounded-full flex items-center justify-center text-sm font-bold">3</div>
                <div>
                  <h3 className="font-semibold text-gray-800">开始使用</h3>
                  <p className="text-sm text-gray-500">查看示例数据预览，了解仪表盘功能</p>
                </div>
              </div>
            </button>
          </div>

          <p className="mt-6 text-xs text-gray-400">配置完成后，仪表盘将自动显示真实数据</p>
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between mb-6 gap-3">
        <h2 className="text-2xl font-bold">仪表盘</h2>
        <div className="flex gap-2 flex-wrap items-center">
          {periods.map((p) => (
            <button
              key={p.value}
              onClick={() => setPeriod(p.value)}
              className={`px-3 py-1 rounded text-sm ${
                period === p.value ? 'bg-blue-500 text-white' : 'bg-gray-200 text-gray-600 hover:bg-gray-300'
              }`}
            >
              {p.label}
            </button>
          ))}
          {/* Live update toggle */}
          <button
            onClick={() => setLiveEnabled(!liveEnabled)}
            className={`px-3 py-1 rounded text-sm flex items-center gap-1 ${
              liveEnabled ? 'bg-green-500 text-white' : 'bg-gray-200 text-gray-600 hover:bg-gray-300'
            }`}
            title={liveEnabled ? '实时更新中 (1s/5s/30s 梯度刷新)' : '实时更新已暂停'}
          >
            <span className={`inline-block w-2 h-2 rounded-full ${liveEnabled ? 'bg-white animate-pulse' : 'bg-gray-400'}`} />
            {liveEnabled ? '实时' : '暂停'}
          </button>
          {isDataEmpty && (
            <button
              onClick={() => setShowDemo(!showDemo)}
              className={`px-3 py-1 rounded text-sm ${showDemo ? 'bg-gray-500 text-white' : 'bg-gray-100 text-gray-500 hover:bg-gray-200'}`}
            >
              {showDemo ? '隐藏示例' : '查看示例'}
            </button>
          )}
        </div>
      </div>

      {/* Smart Router API Usage Guide - always visible */}
      {!loading && (
        <>
          {/* Demo data notice */}
          {showDemo && !hasRealData && (
            <div className="mb-4 bg-gray-50 border border-dashed border-gray-300 rounded-lg p-3 flex items-center gap-2">
              <svg className="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <span className="text-sm text-gray-500">示例数据预览 - 真实数据到达后将自动替换</span>
            </div>
          )}

          <div className="mb-6 bg-gradient-to-r from-blue-50 to-indigo-50 border border-blue-200 rounded-lg p-4">
            <div className="flex items-start gap-3">
              <div className="w-8 h-8 bg-blue-500 rounded-lg flex items-center justify-center flex-shrink-0 mt-0.5">
                <svg className="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                </svg>
              </div>
              <div className="flex-1 min-w-0">
                <h3 className="text-sm font-semibold text-blue-800 mb-1">调用智能路由系统</h3>
                <p className="text-xs text-blue-600 mb-2">将请求中的 <code className="px-1.5 py-0.5 bg-blue-100 rounded font-mono text-blue-800">model</code> 参数设置为 <code className="px-1.5 py-0.5 bg-blue-100 rounded font-mono text-blue-800 font-bold">"auto"</code> 即可启用智能路由，系统将自动选择最优模型。</p>
                <div className="bg-white/80 rounded p-3 font-mono text-xs text-gray-700 overflow-x-auto">
                  <div className="text-gray-400"># 使用 OpenAI 兼容接口调用</div>
                  <div>curl {String.raw`${window.location.origin}`}/v1/chat/completions \</div>
                  <div className="pl-4">-H "Authorization: Bearer YOUR_API_KEY" \</div>
                  <div className="pl-4">-H "Content-Type: application/json" \</div>
                  <div className="pl-4">-d {'{'}</div>
                  <div className="pl-8">"model": <span className="text-blue-600 font-bold">"auto"</span>,</div>
                  <div className="pl-8">"messages": [{'{'}"role": "user", "content": "你好"{'}'}]</div>
                  <div className="pl-4">{'}'}</div>
                </div>
                <div className="mt-2 flex flex-wrap gap-3 text-xs text-blue-600">
                  <span>model=<strong>"auto"</strong> 智能路由</span>
                  <span>model=<strong>"具体模型名"</strong> 直连指定模型</span>
                  <span>model=<strong>"别名"</strong> 通过别名映射</span>
                </div>
              </div>
            </div>
          </div>
        </>
      )}

      {loading ? (
        <div className="flex items-center justify-center h-64">
          <div className="text-gray-400">加载中...</div>
        </div>
      ) : hasRealData || showDemo ? (
        <>

          {/* Main stats */}
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 mb-6">
            <StatCard
              label={`${label}转发次数`}
              value={hasRealData ? String(data!.total_interceptions || 0) : String(DEMO_STATS.total_interceptions)}
              color="blue"
              isDemo={!hasRealData}
            />
            <StatCard
              label={`${label}花费`}
              value={hasRealData ? `¥${Number(data!.saved_cost || 0).toFixed(4)}` : `¥${DEMO_STATS.saved_cost.toFixed(4)}`}
              color="orange"
              isDemo={!hasRealData}
            />
            <StatCard
              label="输入Token"
              value={hasRealData ? formatTokens(Number(data!.total_input_tokens || 0)) : formatTokens(DEMO_STATS.total_input_tokens)}
              color="indigo"
              isDemo={!hasRealData}
            />
            <StatCard
              label="输出Token"
              value={hasRealData ? formatTokens(Number(data!.total_output_tokens || 0)) : formatTokens(DEMO_STATS.total_output_tokens)}
              color="cyan"
              isDemo={!hasRealData}
            />
            <StatCard
              label="平均延迟"
              value={hasRealData ? `${Number(data!.avg_latency_ms || 0).toFixed(0)}ms` : `${DEMO_STATS.avg_latency_ms}ms`}
              color="green"
              isDemo={!hasRealData}
            />
            <StatCard
              label="成功率"
              value={hasRealData ? `${Number(data!.success_rate || 0).toFixed(1)}%` : `${DEMO_STATS.success_rate}%`}
              color="purple"
              isDemo={!hasRealData}
            />
          </div>

          {/* Provider balance overview */}
          {providerBalances.length > 0 && (
            <div className="mb-6">
              <h3 className="text-lg font-semibold mb-3">提供商余额概览</h3>
              <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
                {providerBalances.map((pb) => (
                  <div key={pb.provider} className="bg-white rounded-lg shadow p-3">
                    <div className="text-xs text-gray-500 truncate">{pb.provider}</div>
                    <div className="text-lg font-bold text-blue-600">
                      {typeof pb.balance === 'number' ? pb.balance.toFixed(2) : pb.balance}
                    </div>
                    <div className="text-xs text-gray-400">{pb.currency || ''}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Tenant usage overview */}
          {tenantUsage.length > 0 && (
            <div className="mb-6">
              <h3 className="text-lg font-semibold mb-3">租户消耗概览</h3>
              <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
                {tenantUsage.slice(0, 6).map((tu) => (
                  <div key={tu.tenant_name} className="bg-white rounded-lg shadow p-3">
                    <div className="text-xs text-gray-500 truncate">{tu.tenant_name}</div>
                    <div className="text-lg font-bold text-indigo-600">{formatTokens(tu.requests)}</div>
                    <div className="text-xs text-gray-400">¥{tu.cost.toFixed(2)}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Charts */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div className="bg-white rounded-lg shadow p-4">
              <h3 className="font-semibold mb-2">请求趋势</h3>
              <ReactECharts option={trendOption} style={{ height: 300 }} />
            </div>
            <div className="bg-white rounded-lg shadow p-4">
              <h3 className="font-semibold mb-2">模型分布</h3>
              <ReactECharts option={modelDistOption} style={{ height: 300 }} />
            </div>
            {costTrendOption && (
              <div className="bg-white rounded-lg shadow p-4">
                <h3 className="font-semibold mb-2">费用趋势</h3>
                <ReactECharts option={costTrendOption} style={{ height: 300 }} />
              </div>
            )}
            {tenantUsageOption && (
              <div className="bg-white rounded-lg shadow p-4">
                <h3 className="font-semibold mb-2">租户消耗</h3>
                <ReactECharts option={tenantUsageOption} style={{ height: 300 }} />
              </div>
            )}
          </div>
        </>
      ) : (
        /* Empty state - has config but no data */
        <div className="min-h-[60vh] flex items-center justify-center">
          <div className="text-center max-w-md">
            <div className="w-20 h-20 mx-auto mb-6 bg-gray-100 rounded-full flex items-center justify-center">
              <svg className="w-10 h-10 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
              </svg>
            </div>
            <h3 className="text-lg font-semibold text-gray-600 mb-2">暂无数据</h3>
            <p className="text-sm text-gray-400 mb-6">
              开始使用 SmartRouter 后，仪表盘将自动展示请求统计、模型分布和费用趋势
            </p>
            <div className="flex gap-3 justify-center">
              <button
                onClick={() => setShowDemo(true)}
                className="px-4 py-2 bg-gray-100 text-gray-600 rounded-lg hover:bg-gray-200 text-sm"
              >
                查看示例数据
              </button>
              {hasProviders === false && (
                <button
                  onClick={() => setShowGuide(true)}
                  className="px-4 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600 text-sm"
                >
                  开始配置
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function StatCard({ label, value, color, isDemo }: { label: string; value: string; color: string; isDemo?: boolean }) {
  const colorMap: Record<string, string> = {
    blue: 'text-blue-600',
    orange: 'text-orange-600',
    indigo: 'text-indigo-600',
    cyan: 'text-cyan-600',
    green: 'text-green-600',
    purple: 'text-purple-600',
  };

  return (
    <div className={`bg-white rounded-lg shadow p-4 ${isDemo ? 'opacity-60' : ''}`}>
      <div className="text-sm text-gray-500">{label}</div>
      <div className={`text-2xl font-bold mt-1 ${colorMap[color] || ''}`}>{value}</div>
      {isDemo && <div className="text-xs text-gray-300 mt-1">示例</div>}
    </div>
  );
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}
