import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import StatCard from '../components/StatCard';
import ActionButton from '../components/ActionButton';
import { Wallet, Shield, AlertTriangle, BarChart3, Radar, Bot } from 'lucide-react';
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';

function formatINR(n: number) {
  if (Math.abs(n) >= 100000) return `₹${(n / 100000).toFixed(1)}L`;
  if (Math.abs(n) >= 1000) return `₹${(n / 1000).toFixed(1)}K`;
  return `₹${n.toFixed(0)}`;
}

export default function Dashboard() {
  const { data: portfolio, isLoading: pLoading } = useQuery({
    queryKey: ['portfolio'],
    queryFn: api.getPortfolio,
    refetchInterval: 30000,
  });

  const { data: risk } = useQuery({
    queryKey: ['risk'],
    queryFn: api.getRisk,
    refetchInterval: 30000,
  });

  const { data: history } = useQuery({
    queryKey: ['history'],
    queryFn: () => api.getPortfolioHistory(30),
  });

  const { data: signals } = useQuery({
    queryKey: ['signals'],
    queryFn: () => api.getSignals(5),
  });

  const queryClient = useQueryClient();
  const refreshAll = () => queryClient.invalidateQueries();

  if (pLoading) return <div className="text-gray-500">Loading...</div>;

  const p = portfolio!;
  const pnlPct = p.starting_capital > 0 ? (p.cumulative_pnl / p.starting_capital) * 100 : 0;
  const isProfit = p.cumulative_pnl >= 0;

  const chartData = (history || []).reverse().map(d => ({
    date: d.date?.slice(5) || '',
    capital: d.capital,
    pnl: d.cumulative_pnl,
  }));

  return (
    <div>
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-6">
        <div>
          <h2 className="text-xl font-semibold text-white">Dashboard</h2>
          <p className="text-sm text-gray-500">
            {p.paper_mode ? '📝 Paper Trading' : '🔴 Live Trading'}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <ActionButton label="Scan" icon={<Radar size={14} />} action={api.triggerScan} onComplete={refreshAll as any} />
          <ActionButton label="AI Workflow" icon={<Bot size={14} />} action={api.triggerWorkflow} onComplete={refreshAll as any} />
          <div className={`px-2 py-1 rounded-full text-xs font-medium ${
            isProfit ? 'bg-emerald-500/10 text-emerald-400' : 'bg-red-500/10 text-red-400'
          }`}>
            {formatINR(p.cumulative_pnl)} ({pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(1)}%)
          </div>
        </div>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <StatCard
          label="Capital"
          value={formatINR(p.capital)}
          sub={`Started: ${formatINR(p.starting_capital)}`}
          icon={<Wallet size={16} />}
          variant={isProfit ? 'green' : 'red'}
        />
        <StatCard
          label="Floor Distance"
          value={formatINR(p.floor_distance)}
          sub={`Floor: ${formatINR(p.hard_floor)}`}
          icon={<Shield size={16} />}
          variant={p.floor_distance < 50000 ? 'red' : p.floor_distance < 80000 ? 'yellow' : 'green'}
        />
        <StatCard
          label="Total Trades"
          value={String(p.total_trades)}
          sub={`${p.open_positions} open`}
          icon={<BarChart3 size={16} />}
        />
        <StatCard
          label="Drawdown"
          value={`${risk?.drawdown_pct?.toFixed(1) || '0.0'}%`}
          sub={`Peak: ${formatINR(risk?.peak_capital || p.capital)}`}
          icon={<AlertTriangle size={16} />}
          variant={Math.abs(risk?.drawdown_pct || 0) > 10 ? 'red' : 'default'}
        />
      </div>

      {/* P&L Chart */}
      {chartData.length > 1 && (
        <div className="bg-[#1e2235] rounded-xl border border-[#2a2d3e] p-4 mb-6">
          <h3 className="text-sm text-gray-400 mb-3">Capital Over Time</h3>
          <ResponsiveContainer width="100%" height={250}>
            <AreaChart data={chartData}>
              <defs>
                <linearGradient id="capitalGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#10b981" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis dataKey="date" tick={{ fontSize: 11, fill: '#6b7280' }} />
              <YAxis tick={{ fontSize: 11, fill: '#6b7280' }} tickFormatter={v => formatINR(v)} />
              <Tooltip
                contentStyle={{ background: '#1e2235', border: '1px solid #2a2d3e', borderRadius: 8, fontSize: 12 }}
                formatter={(v) => [formatINR(Number(v)), 'Capital']}
              />
              <Area type="monotone" dataKey="capital" stroke="#10b981" fill="url(#capitalGrad)" strokeWidth={2} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Strategy Performance + Recent Signals */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Strategies */}
        <div className="bg-[#1e2235] rounded-xl border border-[#2a2d3e] p-4">
          <h3 className="text-sm text-gray-400 mb-3">Strategy Performance</h3>
          {Object.keys(p.strategies).length === 0 ? (
            <p className="text-gray-600 text-sm">No trades yet</p>
          ) : (
            <div className="space-y-3">
              {Object.entries(p.strategies).map(([name, s]) => (
                <div key={name} className="flex items-center justify-between">
                  <div>
                    <div className="text-sm text-white">{name.replace(/_/g, ' ')}</div>
                    <div className="text-xs text-gray-500">
                      {s.total_trades} trades · {(s.win_rate * 100).toFixed(0)}% win
                    </div>
                  </div>
                  <div className={`text-sm font-medium ${s.total_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                    {formatINR(s.total_pnl)}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Recent signals */}
        <div className="bg-[#1e2235] rounded-xl border border-[#2a2d3e] p-4">
          <h3 className="text-sm text-gray-400 mb-3">Recent Signals</h3>
          {!signals?.length ? (
            <p className="text-gray-600 text-sm">No signals yet</p>
          ) : (
            <div className="space-y-2">
              {signals.map(s => (
                <div key={s.id} className="flex items-center justify-between py-1.5 border-b border-[#2a2d3e] last:border-0">
                  <div>
                    <span className={`text-xs font-medium px-1.5 py-0.5 rounded ${
                      s.direction === 'BUY' ? 'bg-emerald-500/15 text-emerald-400' : 'bg-red-500/15 text-red-400'
                    }`}>{s.direction}</span>
                    <span className="text-sm text-white ml-2">{s.symbol.replace('.NS', '')}</span>
                  </div>
                  <div className="text-right">
                    <div className="text-xs text-gray-400">{formatINR(s.entry_price)}</div>
                    <div className={`text-xs ${
                      s.status === 'EXECUTED' ? 'text-emerald-500' : s.status === 'REJECTED' ? 'text-red-500' : 'text-amber-500'
                    }`}>{s.status}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
