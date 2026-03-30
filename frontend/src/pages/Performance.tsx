import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import StatCard from '../components/StatCard';
import {
  AreaChart,
  Area,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from 'recharts';
import {
  TrendingUp,
  Percent,
  Target,
  BarChart3,
  Trophy,
  Scale,
  ArrowDownRight,
  Activity,
  GitCompare,
} from 'lucide-react';

// ---------------------------------------------------------------------------
// API helpers (inline to keep the page self-contained)
// ---------------------------------------------------------------------------

const BASE = '/api';

async function fetchJSON<T>(url: string): Promise<T> {
  const res = await fetch(`${BASE}${url}`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

interface PerformanceKPIs {
  account_id: string;
  capital: number;
  starting_capital: number;
  total_pnl: number;
  return_pct: number;
  xirr: number | null;
  win_rate: number;
  profit_factor: number;
  avg_win: number;
  avg_loss: number;
  best_trade: number;
  worst_trade: number;
  total_trades: number;
  total_costs: number;
  total_slippage: number;
  max_drawdown_pct: number;
  sharpe_ratio: number | null;
  avg_holding_days: number | null;
  label?: string;
}

interface EquityPoint {
  date: string;
  capital: number;
}

interface MonthlyRow {
  month: string;
  pnl: number;
  trades: number;
  win_rate: number;
}

interface StrategyRow {
  strategy: string;
  trades: number;
  win_rate: number;
  pnl: number;
  avg_pnl: number;
  profit_factor: number;
  total_costs: number;
}

const perfApi = {
  getKPIs: () => fetchJSON<PerformanceKPIs>('/performance'),
  getEquityCurve: () => fetchJSON<EquityPoint[]>('/performance/equity-curve'),
  getMonthly: () => fetchJSON<MonthlyRow[]>('/performance/monthly'),
  getStrategies: () => fetchJSON<StrategyRow[]>('/performance/strategies'),
  compare: (accounts: string) =>
    fetchJSON<Record<string, PerformanceKPIs>>(`/performance/compare?accounts=${accounts}`),
};

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

function formatINR(n: number): string {
  if (n == null || isNaN(n)) return '₹0';
  if (Math.abs(n) >= 100000) return `₹${(n / 100000).toFixed(1)}L`;
  if (Math.abs(n) >= 1000) return `₹${(n / 1000).toFixed(1)}K`;
  return `₹${n.toFixed(0)}`;
}

function fmtPct(n: number | null | undefined): string {
  if (n == null || isNaN(n)) return '--';
  return `${n >= 0 ? '+' : ''}${n.toFixed(1)}%`;
}

function fmtNum(n: number | null | undefined, decimals = 2): string {
  if (n == null || isNaN(n)) return '--';
  return n.toFixed(decimals);
}

// ---------------------------------------------------------------------------
// Tooltip style (reuse across charts)
// ---------------------------------------------------------------------------
const tooltipStyle = {
  background: '#1e2235',
  border: '1px solid #2a2d3e',
  borderRadius: 8,
  fontSize: 12,
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function Performance() {
  const [showCompare, setShowCompare] = useState(false);

  // ---- Queries ----
  const { data: kpi, isLoading } = useQuery({
    queryKey: ['performance'],
    queryFn: perfApi.getKPIs,
    refetchInterval: 30000,
  });

  const { data: equity } = useQuery({
    queryKey: ['equity-curve'],
    queryFn: perfApi.getEquityCurve,
  });

  const { data: monthly } = useQuery({
    queryKey: ['monthly-pnl'],
    queryFn: perfApi.getMonthly,
  });

  const { data: strategies } = useQuery({
    queryKey: ['strategies-breakdown'],
    queryFn: perfApi.getStrategies,
  });

  const { data: comparison } = useQuery({
    queryKey: ['performance-compare'],
    queryFn: () => perfApi.compare('paper,live'),
    enabled: showCompare,
  });

  if (isLoading || !kpi) {
    return <div className="text-gray-500">Loading performance data...</div>;
  }

  // ---- Derived ----
  const isProfit = kpi.total_pnl >= 0;

  const equityData = (equity || []).map((d) => ({
    date: d.date?.slice(5) || '',
    capital: d.capital,
  }));

  const monthlyData = (monthly || []).map((m) => ({
    ...m,
    month: m.month?.slice(2) || '', // "24-01" style
  }));

  // Comparison accounts (exclude self)
  const compareEntries = comparison
    ? Object.entries(comparison).filter(([, v]) => !('error' in v))
    : [];

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-xl font-semibold text-white">Performance Analytics</h2>
          <p className="text-sm text-gray-500">
            Account: {kpi.account_id} &middot; {kpi.total_trades} trades
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div
            className={`px-3 py-1.5 rounded-full text-sm font-medium ${
              isProfit
                ? 'bg-emerald-500/10 text-emerald-400'
                : 'bg-red-500/10 text-red-400'
            }`}
          >
            {formatINR(kpi.total_pnl)} ({fmtPct(kpi.return_pct)})
          </div>
          <button
            onClick={() => setShowCompare(!showCompare)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm transition ${
              showCompare
                ? 'bg-blue-500/20 text-blue-400 border border-blue-500/30'
                : 'bg-[#1e2235] text-gray-400 border border-[#2a2d3e] hover:text-white'
            }`}
          >
            <GitCompare size={14} />
            Compare with Live
          </button>
        </div>
      </div>

      {/* ================================================================ */}
      {/* KPI Cards                                                        */}
      {/* ================================================================ */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <StatCard
          label="Capital"
          value={formatINR(kpi.capital)}
          sub={`Started: ${formatINR(kpi.starting_capital)}`}
          icon={<TrendingUp size={16} />}
          variant={isProfit ? 'green' : 'red'}
        />
        <StatCard
          label="Return"
          value={fmtPct(kpi.return_pct)}
          sub={`P&L: ${formatINR(kpi.total_pnl)}`}
          icon={<Percent size={16} />}
          variant={kpi.return_pct >= 0 ? 'green' : 'red'}
        />
        <StatCard
          label="XIRR"
          value={kpi.xirr != null ? fmtPct(kpi.xirr) : '--'}
          sub="Annualised return"
          icon={<Activity size={16} />}
          variant={
            kpi.xirr != null ? (kpi.xirr >= 0 ? 'green' : 'red') : 'default'
          }
        />
        <StatCard
          label="Sharpe Ratio"
          value={kpi.sharpe_ratio != null ? fmtNum(kpi.sharpe_ratio) : '--'}
          sub="Risk-adj. return"
          icon={<Scale size={16} />}
          variant={
            kpi.sharpe_ratio != null
              ? kpi.sharpe_ratio >= 1
                ? 'green'
                : kpi.sharpe_ratio >= 0
                ? 'yellow'
                : 'red'
              : 'default'
          }
        />
        <StatCard
          label="Win Rate"
          value={`${(kpi.win_rate * 100).toFixed(1)}%`}
          sub={`${kpi.total_trades} trades`}
          icon={<Trophy size={16} />}
          variant={kpi.win_rate >= 0.5 ? 'green' : kpi.win_rate >= 0.35 ? 'yellow' : 'red'}
        />
        <StatCard
          label="Profit Factor"
          value={fmtNum(kpi.profit_factor)}
          sub={`Avg win: ${formatINR(kpi.avg_win)}`}
          icon={<Target size={16} />}
          variant={kpi.profit_factor >= 1.5 ? 'green' : kpi.profit_factor >= 1 ? 'yellow' : 'red'}
        />
        <StatCard
          label="Max Drawdown"
          value={`${kpi.max_drawdown_pct.toFixed(1)}%`}
          sub={`Worst: ${formatINR(kpi.worst_trade)}`}
          icon={<ArrowDownRight size={16} />}
          variant={kpi.max_drawdown_pct > 15 ? 'red' : kpi.max_drawdown_pct > 8 ? 'yellow' : 'green'}
        />
        <StatCard
          label="Total Trades"
          value={String(kpi.total_trades)}
          sub={kpi.avg_holding_days != null ? `Avg hold: ${kpi.avg_holding_days}d` : undefined}
          icon={<BarChart3 size={16} />}
        />
      </div>

      {/* ================================================================ */}
      {/* Equity Curve                                                     */}
      {/* ================================================================ */}
      {equityData.length > 1 && (
        <div className="bg-[#1e2235] rounded-xl border border-[#2a2d3e] p-4 mb-6">
          <h3 className="text-sm text-gray-400 mb-3">Equity Curve</h3>
          <ResponsiveContainer width="100%" height={280}>
            <AreaChart data={equityData}>
              <defs>
                <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#10b981" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis
                dataKey="date"
                tick={{ fontSize: 11, fill: '#6b7280' }}
                tickLine={false}
              />
              <YAxis
                tick={{ fontSize: 11, fill: '#6b7280' }}
                tickFormatter={(v) => formatINR(v)}
                tickLine={false}
              />
              <Tooltip
                contentStyle={tooltipStyle}
                formatter={(v) => [formatINR(Number(v)), 'Capital']}
              />
              <Area
                type="monotone"
                dataKey="capital"
                stroke="#10b981"
                fill="url(#eqGrad)"
                strokeWidth={2}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* ================================================================ */}
      {/* Monthly P&L Bar Chart                                            */}
      {/* ================================================================ */}
      {monthlyData.length > 0 && (
        <div className="bg-[#1e2235] rounded-xl border border-[#2a2d3e] p-4 mb-6">
          <h3 className="text-sm text-gray-400 mb-3">Monthly P&L</h3>
          <ResponsiveContainer width="100%" height={250}>
            <BarChart data={monthlyData}>
              <XAxis
                dataKey="month"
                tick={{ fontSize: 11, fill: '#6b7280' }}
                tickLine={false}
              />
              <YAxis
                tick={{ fontSize: 11, fill: '#6b7280' }}
                tickFormatter={(v) => formatINR(v)}
                tickLine={false}
              />
              <Tooltip
                contentStyle={tooltipStyle}
                formatter={(v) => [formatINR(Number(v)), 'P&L']}
                labelFormatter={(l) => `Month: ${l}`}
              />
              <Bar dataKey="pnl" radius={[4, 4, 0, 0]}>
                {monthlyData.map((entry, i) => (
                  <Cell
                    key={i}
                    fill={entry.pnl >= 0 ? '#10b981' : '#ef4444'}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* ================================================================ */}
      {/* Strategy Breakdown + Cost Breakdown side by side                 */}
      {/* ================================================================ */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-6">
        {/* Strategy table */}
        <div className="bg-[#1e2235] rounded-xl border border-[#2a2d3e] overflow-hidden">
          <div className="p-4 pb-2">
            <h3 className="text-sm text-gray-400">Strategy Breakdown</h3>
          </div>
          {strategies && strategies.length > 0 ? (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[#2a2d3e] text-gray-500 text-xs uppercase">
                  <th className="text-left p-3">Strategy</th>
                  <th className="text-right p-3">Trades</th>
                  <th className="text-right p-3">Win %</th>
                  <th className="text-right p-3">P&L</th>
                  <th className="text-right p-3">Avg</th>
                  <th className="text-right p-3">PF</th>
                </tr>
              </thead>
              <tbody>
                {strategies.map((s) => (
                  <tr
                    key={s.strategy}
                    className="border-b border-[#2a2d3e]/50 hover:bg-[#252840]"
                  >
                    <td className="p-3 text-white font-medium">
                      {s.strategy.replace(/_/g, ' ')}
                    </td>
                    <td className="p-3 text-right text-gray-400">{s.trades}</td>
                    <td className="p-3 text-right text-gray-300">
                      {(s.win_rate * 100).toFixed(0)}%
                    </td>
                    <td
                      className={`p-3 text-right font-medium ${
                        s.pnl >= 0 ? 'text-emerald-400' : 'text-red-400'
                      }`}
                    >
                      {formatINR(s.pnl)}
                    </td>
                    <td
                      className={`p-3 text-right ${
                        s.avg_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'
                      }`}
                    >
                      {formatINR(s.avg_pnl)}
                    </td>
                    <td className="p-3 text-right text-gray-300">
                      {fmtNum(s.profit_factor)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="text-gray-600 text-sm p-4">No strategy data yet.</p>
          )}
        </div>

        {/* Cost breakdown */}
        <div className="bg-[#1e2235] rounded-xl border border-[#2a2d3e] p-4">
          <h3 className="text-sm text-gray-400 mb-4">Cost Breakdown</h3>
          <div className="space-y-4">
            {[
              {
                label: 'Total Brokerage & Fees',
                value: kpi.total_costs,
                color: 'text-amber-400',
              },
              {
                label: 'Total Slippage',
                value: kpi.total_slippage,
                color: 'text-orange-400',
              },
              {
                label: 'Total Costs',
                value: kpi.total_costs + kpi.total_slippage,
                color: 'text-red-400',
              },
              {
                label: 'Gross P&L (before costs)',
                value: kpi.total_pnl + kpi.total_costs + kpi.total_slippage,
                color:
                  kpi.total_pnl + kpi.total_costs + kpi.total_slippage >= 0
                    ? 'text-emerald-400'
                    : 'text-red-400',
              },
              {
                label: 'Net P&L',
                value: kpi.total_pnl,
                color: kpi.total_pnl >= 0 ? 'text-emerald-400' : 'text-red-400',
              },
            ].map((item) => (
              <div
                key={item.label}
                className="flex items-center justify-between py-2 px-3 rounded bg-[#0f1117]"
              >
                <span className="text-gray-400 text-sm">{item.label}</span>
                <span className={`text-sm font-medium ${item.color}`}>
                  {formatINR(item.value)}
                </span>
              </div>
            ))}
          </div>

          {/* Additional stats */}
          <div className="mt-6 grid grid-cols-2 gap-3">
            <div className="bg-[#0f1117] rounded p-3">
              <div className="text-xs text-gray-500 mb-1">Best Trade</div>
              <div className="text-sm text-emerald-400 font-medium">
                {formatINR(kpi.best_trade)}
              </div>
            </div>
            <div className="bg-[#0f1117] rounded p-3">
              <div className="text-xs text-gray-500 mb-1">Worst Trade</div>
              <div className="text-sm text-red-400 font-medium">
                {formatINR(kpi.worst_trade)}
              </div>
            </div>
            <div className="bg-[#0f1117] rounded p-3">
              <div className="text-xs text-gray-500 mb-1">Avg Win</div>
              <div className="text-sm text-emerald-400 font-medium">
                {formatINR(kpi.avg_win)}
              </div>
            </div>
            <div className="bg-[#0f1117] rounded p-3">
              <div className="text-xs text-gray-500 mb-1">Avg Loss</div>
              <div className="text-sm text-red-400 font-medium">
                {formatINR(kpi.avg_loss)}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* ================================================================ */}
      {/* Account Comparison                                               */}
      {/* ================================================================ */}
      {showCompare && (
        <div className="bg-[#1e2235] rounded-xl border border-blue-500/20 p-4 mb-6">
          <h3 className="text-sm text-blue-400 mb-4">Account Comparison</h3>
          {compareEntries.length === 0 ? (
            <p className="text-gray-600 text-sm">
              No comparison data available. Make sure both paper and live accounts exist.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[#2a2d3e] text-gray-500 text-xs uppercase">
                    <th className="text-left p-3">Metric</th>
                    {compareEntries.map(([id, data]) => (
                      <th key={id} className="text-right p-3">
                        {data.label || id}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {[
                    { label: 'Capital', key: 'capital', fmt: formatINR },
                    { label: 'Return %', key: 'return_pct', fmt: fmtPct },
                    {
                      label: 'XIRR',
                      key: 'xirr',
                      fmt: (v: number | null) => (v != null ? fmtPct(v) : '--'),
                    },
                    {
                      label: 'Sharpe',
                      key: 'sharpe_ratio',
                      fmt: (v: number | null) => (v != null ? fmtNum(v) : '--'),
                    },
                    {
                      label: 'Win Rate',
                      key: 'win_rate',
                      fmt: (v: number) => `${(v * 100).toFixed(1)}%`,
                    },
                    { label: 'Profit Factor', key: 'profit_factor', fmt: (v: number) => fmtNum(v) },
                    {
                      label: 'Max Drawdown',
                      key: 'max_drawdown_pct',
                      fmt: (v: number) => `${v.toFixed(1)}%`,
                    },
                    { label: 'Total Trades', key: 'total_trades', fmt: (v: number) => String(v) },
                    { label: 'Total P&L', key: 'total_pnl', fmt: formatINR },
                    { label: 'Total Costs', key: 'total_costs', fmt: formatINR },
                  ].map((metric) => (
                    <tr
                      key={metric.key}
                      className="border-b border-[#2a2d3e]/50 hover:bg-[#252840]"
                    >
                      <td className="p-3 text-gray-400">{metric.label}</td>
                      {compareEntries.map(([id, data]) => {
                        const val = (data as unknown as Record<string, unknown>)[metric.key];
                        const formatted = metric.fmt(val as never);
                        const isNumeric = typeof val === 'number';
                        const isPositive =
                          isNumeric &&
                          ['total_pnl', 'return_pct', 'xirr'].includes(metric.key) &&
                          (val as number) >= 0;
                        const isNegative =
                          isNumeric &&
                          ['total_pnl', 'return_pct', 'xirr'].includes(metric.key) &&
                          (val as number) < 0;
                        return (
                          <td
                            key={id}
                            className={`p-3 text-right font-medium ${
                              isPositive
                                ? 'text-emerald-400'
                                : isNegative
                                ? 'text-red-400'
                                : 'text-white'
                            }`}
                          >
                            {formatted}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
