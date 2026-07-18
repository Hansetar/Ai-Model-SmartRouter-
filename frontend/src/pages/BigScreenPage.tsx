import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import ReactECharts from 'echarts-for-react';
import { createApi } from '../api';

interface BigScreenPageProps {
  token: string;
}

interface DashboardData {
  total_interceptions: number;
  saved_cost: number;
  avg_latency_ms: number;
  total_input_tokens: number;
  total_output_tokens: number;
  success_rate: number;
  trend_labels: string[];
  trend_values: number[];
  model_distribution: { name: string; value: number }[];
  cost_trend_labels?: string[];
  cost_trend_values?: number[];
  [key: string]: unknown;
}

// Gradient refresh intervals for big screen
const REFRESH_STATS_MS = 1000;    // Key metrics: 1s
const REFRESH_CHARTS_MS = 5000;   // Charts: 5s

export default function BigScreenPage({ token }: BigScreenPageProps) {
  const api = createApi(token);
  const [data, setData] = useState<DashboardData | null>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [activeChart, setActiveChart] = useState(0);
  const [liveEnabled, setLiveEnabled] = useState(true);
  const containerRef = useRef<HTMLDivElement>(null);
  const statsTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const chartsTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const carouselRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const loadingRef = useRef(false);

  const loadData = useCallback(async () => {
    loadingRef.current = true;
    try {
      const result = await api.getDashboard('today');
      setData(result as DashboardData);
    } catch (err) {
      console.error('Failed to load big screen data:', err);
    } finally {
      loadingRef.current = false;
    }
  }, [api]);

  // Lightweight stats refresh (1s)
  const loadStats = useCallback(async () => {
    if (loadingRef.current) return;
    try {
      const result = await api.getDashboard('today');
      setData((prev) => prev ? { ...prev, ...result } as DashboardData : result as DashboardData);
    } catch {
      // Silent fail for background refresh
    }
  }, [api]);

  // Charts refresh (5s)
  const loadCharts = useCallback(async () => {
    if (loadingRef.current) return;
    try {
      const result = await api.getDashboard('today');
      setData((prev) => prev ? { ...prev, ...result } as DashboardData : result as DashboardData);
    } catch {
      // Silent fail for background refresh
    }
  }, [api]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Gradient real-time update timers
  useEffect(() => {
    if (!liveEnabled) {
      if (statsTimerRef.current) clearInterval(statsTimerRef.current);
      if (chartsTimerRef.current) clearInterval(chartsTimerRef.current);
      statsTimerRef.current = null;
      chartsTimerRef.current = null;
      return;
    }

    // Level 1: Stats refresh every 1s
    statsTimerRef.current = setInterval(loadStats, REFRESH_STATS_MS);

    // Level 2: Charts refresh every 5s
    chartsTimerRef.current = setInterval(loadCharts, REFRESH_CHARTS_MS);

    return () => {
      if (statsTimerRef.current) clearInterval(statsTimerRef.current);
      if (chartsTimerRef.current) clearInterval(chartsTimerRef.current);
    };
  }, [liveEnabled, loadStats, loadCharts]);

  useEffect(() => {
    // Auto carousel charts every 8 seconds
    carouselRef.current = setInterval(() => {
      setActiveChart((prev) => (prev + 1) % 3);
    }, 8000);
    return () => {
      if (carouselRef.current) clearInterval(carouselRef.current);
    };
  }, []);

  const toggleFullscreen = () => {
    if (!containerRef.current) return;
    if (!document.fullscreenElement) {
      containerRef.current.requestFullscreen().then(() => setIsFullscreen(true)).catch(() => {});
    } else {
      document.exitFullscreen().then(() => setIsFullscreen(false)).catch(() => {});
    }
  };

  useEffect(() => {
    const handler = () => setIsFullscreen(!!document.fullscreenElement);
    document.addEventListener('fullscreenchange', handler);
    return () => document.removeEventListener('fullscreenchange', handler);
  }, []);

  const totalRequests = data?.total_interceptions || 0;
  const totalCost = data?.saved_cost || 0;
  const avgLatency = data?.avg_latency_ms || 0;
  const activeModels = (data?.model_distribution || []).length;

  // Request trend chart
  const trendOption = useMemo(() => ({
    tooltip: { trigger: 'axis' as const },
    xAxis: {
      type: 'category' as const,
      data: data?.trend_labels || [],
      axisLabel: { color: '#94a3b8' },
      axisLine: { lineStyle: { color: '#334155' } },
    },
    yAxis: {
      type: 'value' as const,
      axisLabel: { color: '#94a3b8' },
      splitLine: { lineStyle: { color: '#1e293b' } },
    },
    series: [
      {
        name: '请求数',
        type: 'line' as const,
        data: data?.trend_values || [],
        smooth: true,
        areaStyle: { opacity: 0.3, color: '#3b82f6' },
        lineStyle: { color: '#3b82f6', width: 3 },
        itemStyle: { color: '#3b82f6' },
      },
    ],
    grid: { left: 60, right: 30, top: 20, bottom: 40 },
    backgroundColor: 'transparent',
  }), [data]);

  // Model distribution pie chart
  const modelDistOption = useMemo(() => ({
    tooltip: { trigger: 'item' as const },
    series: [
      {
        type: 'pie' as const,
        radius: ['35%', '65%'],
        data: data?.model_distribution || [],
        label: { color: '#94a3b8' },
        emphasis: {
          itemStyle: { shadowBlur: 10, shadowOffsetX: 0, shadowColor: 'rgba(0,0,0,0.5)' },
        },
      },
    ],
    backgroundColor: 'transparent',
  }), [data]);

  // Cost trend chart
  const costTrendOption = useMemo(() => {
    const costLabels = data?.cost_trend_labels || data?.trend_labels || [];
    const costValues = data?.cost_trend_values || [];
    return {
      tooltip: { trigger: 'axis' as const, formatter: (params: unknown[]) => {
        const p = params[0] as { name: string; value: number };
        return `${p.name}<br/>费用: ¥${p.value.toFixed(4)}`;
      }},
      xAxis: {
        type: 'category' as const,
        data: costLabels,
        axisLabel: { color: '#94a3b8' },
        axisLine: { lineStyle: { color: '#334155' } },
      },
      yAxis: {
        type: 'value' as const,
        name: '费用(¥)',
        axisLabel: { color: '#94a3b8' },
        splitLine: { lineStyle: { color: '#1e293b' } },
      },
      series: [
        {
          name: '费用',
          type: 'line' as const,
          data: costValues,
          smooth: true,
          areaStyle: { opacity: 0.3, color: '#f97316' },
          lineStyle: { color: '#f97316', width: 3 },
          itemStyle: { color: '#f97316' },
        },
      ],
      grid: { left: 70, right: 30, top: 20, bottom: 40 },
      backgroundColor: 'transparent',
    };
  }, [data]);

  const chartOptions = [trendOption, modelDistOption, costTrendOption];
  const chartTitles = ['请求趋势', '模型分布', '费用趋势'];

  return (
    <div ref={containerRef} className="min-h-screen bg-slate-900 text-white p-4 lg:p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl lg:text-3xl font-bold">SmartRouter 监控大屏</h1>
        <div className="flex items-center gap-4">
          <button
            onClick={() => setLiveEnabled(!liveEnabled)}
            className={`px-3 py-1.5 rounded text-sm flex items-center gap-1.5 ${
              liveEnabled ? 'bg-green-600 text-white' : 'bg-slate-700 text-slate-400 hover:bg-slate-600'
            }`}
            title={liveEnabled ? '实时更新中 (1s/5s 梯度刷新)' : '实时更新已暂停'}
          >
            <span className={`inline-block w-2 h-2 rounded-full ${liveEnabled ? 'bg-white animate-pulse' : 'bg-slate-500'}`} />
            {liveEnabled ? '实时' : '暂停'}
          </button>
          <span className="text-sm text-slate-400">
            {new Date().toLocaleString('zh-CN')}
          </span>
          <button
            onClick={toggleFullscreen}
            className="px-4 py-2 bg-slate-700 text-slate-200 rounded hover:bg-slate-600 text-sm"
          >
            {isFullscreen ? '退出全屏' : '全屏'}
          </button>
        </div>
      </div>

      {/* Key Metrics */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <MetricCard label="总请求数" value={formatNum(totalRequests)} color="blue" />
        <MetricCard label="总费用" value={`¥${totalCost.toFixed(4)}`} color="orange" />
        <MetricCard label="平均延迟" value={`${avgLatency.toFixed(0)}ms`} color="green" />
        <MetricCard label="活跃模型数" value={String(activeModels)} color="purple" />
      </div>

      {/* Chart Carousel Indicators */}
      <div className="flex justify-center gap-2 mb-4">
        {chartTitles.map((title, i) => (
          <button
            key={i}
            onClick={() => setActiveChart(i)}
            className={`px-4 py-1.5 rounded text-sm transition-colors ${
              activeChart === i
                ? 'bg-blue-500 text-white'
                : 'bg-slate-700 text-slate-400 hover:bg-slate-600'
            }`}
          >
            {title}
          </button>
        ))}
      </div>

      {/* Main Chart Area */}
      <div className="bg-slate-800 rounded-lg p-4 lg:p-6" style={{ minHeight: 400 }}>
        <ReactECharts
          option={chartOptions[activeChart]}
          style={{ height: 380 }}
          opts={{ renderer: 'canvas' }}
        />
      </div>

      {/* Bottom: All 3 charts in a row on large screens */}
      <div className="hidden lg:grid grid-cols-3 gap-4 mt-6">
        {chartOptions.map((opt, i) => (
          <div key={i} className="bg-slate-800 rounded-lg p-4">
            <h3 className="text-sm font-medium text-slate-300 mb-2">{chartTitles[i]}</h3>
            <ReactECharts option={opt} style={{ height: 200 }} opts={{ renderer: 'canvas' }} />
          </div>
        ))}
      </div>
    </div>
  );
}

function MetricCard({ label, value, color }: { label: string; value: string; color: string }) {
  const colorMap: Record<string, string> = {
    blue: 'from-blue-500/20 to-blue-600/10 border-blue-500/30',
    orange: 'from-orange-500/20 to-orange-600/10 border-orange-500/30',
    green: 'from-green-500/20 to-green-600/10 border-green-500/30',
    purple: 'from-purple-500/20 to-purple-600/10 border-purple-500/30',
  };
  const textColorMap: Record<string, string> = {
    blue: 'text-blue-400',
    orange: 'text-orange-400',
    green: 'text-green-400',
    purple: 'text-purple-400',
  };

  return (
    <div className={`bg-gradient-to-br ${colorMap[color]} border rounded-lg p-4 lg:p-6`}>
      <div className="text-sm text-slate-400">{label}</div>
      <div className={`text-3xl lg:text-4xl font-bold mt-2 ${textColorMap[color]}`}>{value}</div>
    </div>
  );
}

function formatNum(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}
