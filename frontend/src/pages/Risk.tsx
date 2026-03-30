import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import StatCard from '../components/StatCard';
import { Shield, AlertTriangle, TrendingDown, Gauge } from 'lucide-react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts';

function formatINR(n: number) {
  if (Math.abs(n) >= 100000) return `₹${(n / 100000).toFixed(1)}L`;
  return `₹${n.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
}

export default function Risk() {
  const { data: risk, isLoading } = useQuery({
    queryKey: ['risk'],
    queryFn: api.getRisk,
    refetchInterval: 15000,
  });

  const { data: portfolio } = useQuery({
    queryKey: ['portfolio'],
    queryFn: api.getPortfolio,
  });

  if (isLoading || !risk) return <div className="text-gray-500">Loading risk data...</div>;

  // Floor gauge: distance as percentage of max allowed loss (₹1L)
  const maxLoss = portfolio?.starting_capital ? portfolio.starting_capital - risk.hard_floor : 100000;
  const floorUsedPct = ((maxLoss - risk.floor_distance) / maxLoss) * 100;

  // Capital breakdown for bar chart
  const breakdownData = [
    { name: 'Floor', value: risk.hard_floor, color: '#ef4444' },
    { name: 'Buffer', value: risk.floor_distance, color: '#10b981' },
    { name: 'Margin', value: risk.margin_used, color: '#3b82f6' },
  ];

  return (
    <div>
      <h2 className="text-xl font-semibold text-white mb-4">Risk Monitor</h2>

      {/* Floor breach alert */}
      {risk.floor_breached && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 mb-6 flex items-center gap-3">
          <AlertTriangle className="text-red-400" size={24} />
          <div>
            <p className="text-red-400 font-semibold">HARD FLOOR BREACHED</p>
            <p className="text-red-300 text-sm">All positions have been exited. No new trades allowed.</p>
          </div>
        </div>
      )}

      {/* Key risk metrics */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <StatCard
          label="Floor Distance"
          value={formatINR(risk.floor_distance)}
          sub={`${risk.floor_distance_pct.toFixed(1)}% of capital`}
          icon={<Shield size={16} />}
          variant={risk.floor_distance < 30000 ? 'red' : risk.floor_distance < 60000 ? 'yellow' : 'green'}
        />
        <StatCard
          label="Drawdown"
          value={`${risk.drawdown_pct.toFixed(1)}%`}
          sub={`Peak: ${formatINR(risk.peak_capital)}`}
          icon={<TrendingDown size={16} />}
          variant={Math.abs(risk.drawdown_pct) > 10 ? 'red' : Math.abs(risk.drawdown_pct) > 5 ? 'yellow' : 'default'}
        />
        <StatCard
          label="Margin Used"
          value={formatINR(risk.margin_used)}
          sub={`${risk.margin_utilization_pct.toFixed(1)}% utilization`}
          icon={<Gauge size={16} />}
          variant={risk.margin_utilization_pct > 80 ? 'red' : risk.margin_utilization_pct > 60 ? 'yellow' : 'default'}
        />
        <StatCard
          label="Max Loss Allowed"
          value={formatINR(risk.max_loss_allowed)}
          sub="Per single trade"
          icon={<AlertTriangle size={16} />}
        />
      </div>

      {/* Floor gauge visualization */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-6">
        <div className="bg-[#1e2235] rounded-xl border border-[#2a2d3e] p-4">
          <h3 className="text-sm text-gray-400 mb-4">Floor Risk Gauge</h3>
          <div className="relative h-6 bg-[#0f1117] rounded-full overflow-hidden mb-3">
            <div
              className={`h-full rounded-full transition-all duration-500 ${
                floorUsedPct > 80 ? 'bg-red-500' : floorUsedPct > 50 ? 'bg-amber-500' : 'bg-emerald-500'
              }`}
              style={{ width: `${Math.min(100, Math.max(0, floorUsedPct))}%` }}
            />
          </div>
          <div className="flex justify-between text-xs text-gray-500">
            <span>Safe (₹{formatINR(risk.hard_floor + maxLoss)})</span>
            <span>{floorUsedPct.toFixed(0)}% risk used</span>
            <span>Floor (₹{formatINR(risk.hard_floor)})</span>
          </div>
        </div>

        <div className="bg-[#1e2235] rounded-xl border border-[#2a2d3e] p-4">
          <h3 className="text-sm text-gray-400 mb-3">Capital Breakdown</h3>
          <ResponsiveContainer width="100%" height={150}>
            <BarChart data={breakdownData} layout="vertical">
              <XAxis type="number" tick={{ fontSize: 10, fill: '#6b7280' }} tickFormatter={v => formatINR(v)} />
              <YAxis type="category" dataKey="name" tick={{ fontSize: 11, fill: '#9ca3af' }} width={60} />
              <Tooltip
                contentStyle={{ background: '#1e2235', border: '1px solid #2a2d3e', borderRadius: 8, fontSize: 12 }}
                formatter={(v) => formatINR(Number(v))}
              />
              <Bar dataKey="value" radius={[0, 4, 4, 0]}>
                {breakdownData.map((entry, i) => (
                  <Cell key={i} fill={entry.color} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Risk rules summary */}
      <div className="bg-[#1e2235] rounded-xl border border-[#2a2d3e] p-4">
        <h3 className="text-sm text-gray-400 mb-3">Active Risk Rules</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-sm">
          {[
            { rule: 'Hard Floor', value: formatINR(risk.hard_floor), status: risk.floor_breached ? 'BREACHED' : 'OK' },
            { rule: 'Max Margin Utilization', value: '90%', status: risk.margin_utilization_pct > 90 ? 'EXCEEDED' : 'OK' },
            { rule: 'Max Single Trade Loss', value: formatINR(risk.max_loss_allowed), status: 'OK' },
            { rule: 'Max Consecutive Losers', value: '5 trades', status: 'OK' },
          ].map(r => (
            <div key={r.rule} className="flex items-center justify-between py-2 px-3 rounded bg-[#0f1117]">
              <span className="text-gray-400">{r.rule}</span>
              <div className="flex items-center gap-2">
                <span className="text-white">{r.value}</span>
                <span className={`text-xs px-1.5 py-0.5 rounded ${
                  r.status === 'OK' ? 'bg-emerald-500/15 text-emerald-400' : 'bg-red-500/15 text-red-400'
                }`}>{r.status}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
