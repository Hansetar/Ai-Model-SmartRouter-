import { useState, useEffect } from 'react';
import { createApi } from '../api';

interface ExchangeRatesPageProps {
  token: string;
}

interface RateEntry {
  effective: number;
  reference?: number;
  is_manual: boolean;
}

export default function ExchangeRatesPage({ token }: ExchangeRatesPageProps) {
  const api = createApi(token);
  const [rates, setRates] = useState<Record<string, RateEntry>>({});
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [editKey, setEditKey] = useState<string | null>(null);
  const [editValue, setEditValue] = useState<number>(0);
  const [fetching, setFetching] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const [newCurrency, setNewCurrency] = useState('');
  const [newRate, setNewRate] = useState<number>(1);
  const [baseCurrency, setBaseCurrency] = useState('CNY');
  const [hasFetched, setHasFetched] = useState(false);

  useEffect(() => {
    loadConfig();
  }, []);

  const loadConfig = async () => {
    setLoading(true);
    try {
      const result = await api.getConfig();
      const ratesMap = result.exchange_rates as Record<string, number> || {};
      const base = result.currency as string || 'CNY';
      setBaseCurrency(base);
      // Convert to RateEntry format
      const entries: Record<string, RateEntry> = {};
      for (const [key, rate] of Object.entries(ratesMap)) {
        entries[key] = { effective: rate, is_manual: false };
      }
      setRates(entries);
    } catch (err) {
      console.error('Failed to load config:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleFetch = async () => {
    setFetching(true);
    try {
      const result = await api.fetchExchangeRates();
      const fetchedRates = result.rates as Record<string, RateEntry> || {};
      setRates(fetchedRates);
      setHasFetched(true);
    } catch (err) {
      alert('获取参考汇率失败');
    } finally {
      setFetching(false);
    }
  };

  const handleSync = async () => {
    setSyncing(true);
    try {
      const manualOverrides: Record<string, number> = {};
      for (const [key, entry] of Object.entries(rates)) {
        if (entry.is_manual) {
          manualOverrides[key] = entry.effective;
        }
      }
      await api.syncExchangeRates(manualOverrides);
      await loadConfig();
      setHasFetched(false);
      alert('汇率已同步（手动值已保留）');
    } catch (err) {
      alert('同步汇率失败');
    } finally {
      setSyncing(false);
    }
  };

  const handleEdit = (key: string) => {
    setEditKey(key);
    setEditValue(rates[key]?.effective ?? 0);
  };

  const handleSave = async () => {
    if (!editKey) return;
    try {
      // Build full rates map from current state
      const ratesMap: Record<string, number> = {};
      for (const [key, entry] of Object.entries(rates)) {
        ratesMap[key] = key === editKey ? editValue : entry.effective;
      }
      ratesMap[editKey] = editValue;
      await api.updateExchangeRates(ratesMap);
      setRates({
        ...rates,
        [editKey]: { ...rates[editKey], effective: editValue, is_manual: true },
      });
      setEditKey(null);
    } catch (err) {
      alert('更新汇率失败');
    }
  };

  const handleAddRate = async () => {
    if (!newCurrency.trim()) { alert('货币对不能为空'); return; }
    try {
      const ratesMap: Record<string, number> = {};
      for (const [key, entry] of Object.entries(rates)) {
        ratesMap[key] = entry.effective;
      }
      ratesMap[newCurrency.trim().toUpperCase()] = newRate;
      await api.updateExchangeRates(ratesMap);
      setShowAdd(false);
      setNewCurrency('');
      setNewRate(1);
      await loadConfig();
    } catch (err) {
      alert('添加汇率失败');
    }
  };

  const handleDeleteRate = async (currency: string) => {
    if (!confirm(`确定删除汇率 "${currency}"?`)) return;
    try {
      const ratesMap: Record<string, number> = {};
      for (const [key, entry] of Object.entries(rates)) {
        if (key !== currency) ratesMap[key] = entry.effective;
      }
      await api.updateExchangeRates(ratesMap);
      await loadConfig();
    } catch (err) {
      alert('删除汇率失败');
    }
  };

  // Filter rates based on search and base currency
  const filteredKeys = Object.keys(rates)
    .filter((key) => key.toLowerCase().includes(search.toLowerCase()))
    .sort();

  // Group rates by base currency for display
  const getBaseCurrencyRates = () => {
    const prefix = `${baseCurrency}_`;
    const otherPrefix = `USD_${baseCurrency}`;
    const result: { key: string; entry: RateEntry; display: string }[] = [];

    for (const key of filteredKeys) {
      const entry = rates[key];
      // Show rates that involve the base currency
      if (key.startsWith(prefix) || key.endsWith(`_${baseCurrency}`) || search) {
        let display = key;
        if (key.startsWith(prefix)) {
          const target = key.slice(prefix.length);
          display = `1 ${baseCurrency} = ${entry.effective.toFixed(4)} ${target}`;
        } else if (key.endsWith(`_${baseCurrency}`)) {
          const source = key.slice(0, key.length - baseCurrency.length - 1);
          display = `1 ${source} = ${entry.effective.toFixed(4)} ${baseCurrency}`;
        }
        result.push({ key, entry, display });
      }
    }
    return result;
  };

  const displayRates = search ? filteredKeys.map((key) => ({
    key,
    entry: rates[key],
    display: key,
  })) : getBaseCurrencyRates();

  if (loading) return <div className="text-gray-500">加载中...</div>;

  return (
    <div>
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between mb-6 gap-3">
        <h2 className="text-2xl font-bold">汇率管理</h2>
        <div className="flex gap-2 flex-wrap">
          <button
            onClick={handleFetch}
            disabled={fetching}
            className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600 text-sm disabled:opacity-50"
          >
            {fetching ? '查询中...' : '查询并更新'}
          </button>
          <button
            onClick={handleSync}
            disabled={syncing}
            className="px-4 py-2 bg-green-500 text-white rounded hover:bg-green-600 text-sm disabled:opacity-50"
          >
            {syncing ? '同步中...' : '同步'}
          </button>
          <button
            onClick={() => setShowAdd(true)}
            className="px-4 py-2 bg-purple-500 text-white rounded hover:bg-purple-600 text-sm"
          >
            + 手动添加
          </button>
        </div>
      </div>

      {/* Add rate form */}
      {showAdd && (
        <div className="bg-white rounded-lg shadow p-4 mb-4">
          <h3 className="font-semibold mb-3">手动添加汇率</h3>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">货币对</label>
              <input
                className="w-full border rounded px-2 py-1 text-sm"
                placeholder="如 USD_CNY"
                value={newCurrency}
                onChange={(e) => setNewCurrency(e.target.value)}
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">汇率</label>
              <input
                type="number"
                step="0.000001"
                className="w-full border rounded px-2 py-1 text-sm"
                value={newRate}
                onChange={(e) => setNewRate(Number(e.target.value))}
              />
            </div>
            <div className="flex items-end gap-2">
              <button onClick={handleAddRate} className="px-4 py-1.5 bg-blue-500 text-white rounded text-sm hover:bg-blue-600">添加</button>
              <button onClick={() => setShowAdd(false)} className="px-4 py-1.5 bg-gray-200 text-gray-700 rounded text-sm hover:bg-gray-300">取消</button>
            </div>
          </div>
        </div>
      )}

      {/* Search and base currency selector */}
      <div className="flex flex-col sm:flex-row gap-3 mb-4">
        <input
          type="text"
          placeholder="搜索货币对..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="border rounded px-3 py-1.5 text-sm w-full sm:w-64"
        />
        <div className="flex items-center gap-2">
          <label className="text-sm text-gray-500">基准货币:</label>
          <select
            value={baseCurrency}
            onChange={(e) => setBaseCurrency(e.target.value)}
            className="border rounded px-2 py-1 text-sm"
          >
            <option value="CNY">CNY (人民币)</option>
            <option value="USD">USD (美元)</option>
            <option value="EUR">EUR (欧元)</option>
            <option value="JPY">JPY (日元)</option>
            <option value="GBP">GBP (英镑)</option>
          </select>
        </div>
      </div>

      {/* Status bar */}
      {hasFetched && (
        <div className="bg-blue-50 border border-blue-200 rounded p-2 mb-4 text-sm text-blue-700">
          已获取参考汇率数据。手动设置的汇率为生效值，与参考值不同的会标注差异。
        </div>
      )}

      <div className="bg-white rounded-lg shadow overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-gray-600">
            <tr>
              <th className="px-4 py-3 text-left">货币对</th>
              <th className="px-4 py-3 text-left">生效汇率</th>
              <th className="px-4 py-3 text-left">参考汇率</th>
              <th className="px-4 py-3 text-left">操作</th>
            </tr>
          </thead>
          <tbody>
            {displayRates.map(({ key, entry, display }) => {
              const hasRef = entry.reference !== undefined;
              const differs = hasRef && Math.abs(entry.effective - entry.reference!) > 1e-6;
              return (
                <tr key={key} className="border-b hover:bg-gray-50">
                  <td className="px-4 py-3 font-medium">
                    {display}
                    {entry.is_manual && (
                      <span className="ml-2 px-1.5 py-0.5 bg-amber-100 text-amber-700 rounded text-xs">手动</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    {editKey === key ? (
                      <input
                        type="number"
                        step="0.000001"
                        className="border rounded px-2 py-1 text-sm w-32"
                        value={editValue}
                        onChange={(e) => setEditValue(Number(e.target.value))}
                      />
                    ) : (
                      <span>
                        {entry.effective.toFixed(6)}
                        {differs && (
                          <span className="ml-1 text-blue-500 text-xs">
                            (查询: {entry.reference!.toFixed(6)})
                          </span>
                        )}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-gray-500">
                    {hasRef ? (
                      differs ? (
                        <span className="text-orange-500">{entry.reference!.toFixed(6)} (与生效值不同)</span>
                      ) : (
                        <span className="text-green-500">与生效值一致</span>
                      )
                    ) : (
                      <span className="text-gray-400">未查询</span>
                    )}
                  </td>
                  <td className="px-4 py-3 space-x-2 whitespace-nowrap">
                    {editKey === key ? (
                      <>
                        <button onClick={handleSave} className="text-blue-500 hover:underline text-xs">保存</button>
                        <button onClick={() => setEditKey(null)} className="text-gray-500 hover:underline text-xs">取消</button>
                      </>
                    ) : (
                      <>
                        <button onClick={() => handleEdit(key)} className="text-blue-500 hover:underline text-xs">编辑</button>
                        <button onClick={() => handleDeleteRate(key)} className="text-red-500 hover:underline text-xs">删除</button>
                      </>
                    )}
                  </td>
                </tr>
              );
            })}
            {displayRates.length === 0 && (
              <tr><td colSpan={4} className="px-4 py-6 text-center text-gray-400">
                {search ? '未找到匹配的汇率' : '暂无汇率数据，请点击"查询并更新"获取'}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
