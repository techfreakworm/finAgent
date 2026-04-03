import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import toast from 'react-hot-toast';

function formatINR(n: number) {
  return `₹${n.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
}

export default function Trades() {
  const queryClient = useQueryClient();

  const { data: positions } = useQuery({
    queryKey: ['positions'],
    queryFn: api.getPositions,
    refetchInterval: 10000,
  });

  const { data: trades, isLoading } = useQuery({
    queryKey: ['trades'],
    queryFn: () => api.getTrades(100),
  });

  const { data: summary } = useQuery({
    queryKey: ['trades-summary'],
    queryFn: api.getTradesSummary,
  });

  const handleClose = async (id: string, symbol: string) => {
    try {
      await api.closePosition(id);
      toast.success(`Closed ${symbol}`);
      queryClient.invalidateQueries();
    } catch {
      toast.error('Failed to close position');
    }
  };

  if (isLoading) return <div className="text-gray-500">Loading trades...</div>;

  return (
    <div>
      <h2 className="text-xl font-semibold text-white mb-4">Trades</h2>

      {/* Open Positions */}
      {positions && positions.length > 0 && (
        <div className="bg-[#1e2235] rounded-xl border border-[#2a2d3e] overflow-x-auto mb-6">
          <div className="p-3 border-b border-[#2a2d3e]">
            <h3 className="text-sm text-amber-400 font-medium">Open Positions ({positions.length})</h3>
          </div>
          <table className="w-full text-sm min-w-[600px]">
            <thead>
              <tr className="border-b border-[#2a2d3e] text-gray-500 text-xs uppercase">
                <th className="text-left p-3">Symbol</th>
                <th className="text-left p-3">Strategy</th>
                <th className="text-left p-3">Dir</th>
                <th className="text-right p-3">Entry</th>
                <th className="text-right p-3">Qty</th>
                <th className="text-right p-3">Margin</th>
                <th className="text-left p-3">Date</th>
                <th className="text-right p-3"></th>
              </tr>
            </thead>
            <tbody>
              {positions.map(p => (
                <tr key={p.id} className="border-b border-[#2a2d3e]/50 hover:bg-[#252840]">
                  <td className="p-3 text-white font-medium">{p.symbol}</td>
                  <td className="p-3 text-gray-400">{p.strategy.replace(/_/g, ' ')}</td>
                  <td className="p-3">
                    <span className={`px-1.5 py-0.5 rounded text-xs ${
                      p.direction === 'LONG' ? 'bg-emerald-500/15 text-emerald-400' : 'bg-red-500/15 text-red-400'
                    }`}>{p.direction}</span>
                  </td>
                  <td className="p-3 text-right text-gray-300">{formatINR(p.entry_price)}</td>
                  <td className="p-3 text-right text-gray-400">{p.quantity}</td>
                  <td className="p-3 text-right text-gray-300">{formatINR(p.margin_required)}</td>
                  <td className="p-3 text-gray-500 text-xs">{p.entry_date?.slice(0, 16)}</td>
                  <td className="p-3 text-right">
                    <button
                      onClick={() => handleClose(p.id, p.symbol)}
                      className="text-xs px-2 py-1 rounded bg-red-500/15 text-red-400 hover:bg-red-500/25 transition-colors"
                    >
                      Close
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Strategy summary cards */}
      {summary && Object.keys(summary).length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
          {Object.entries(summary).map(([name, s]) => (
            <div key={name} className="bg-[#1e2235] rounded-xl border border-[#2a2d3e] p-4">
              <h4 className="text-sm text-gray-400 capitalize mb-2">{name.replace(/_/g, ' ')}</h4>
              <div className="grid grid-cols-2 gap-2 text-xs">
                <div>
                  <span className="text-gray-500">Trades:</span>{' '}
                  <span className="text-white">{s.total_trades}</span>
                </div>
                <div>
                  <span className="text-gray-500">Win Rate:</span>{' '}
                  <span className="text-white">{(s.win_rate * 100).toFixed(0)}%</span>
                </div>
                <div>
                  <span className="text-gray-500">Total P&L:</span>{' '}
                  <span className={s.total_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}>
                    {formatINR(s.total_pnl)}
                  </span>
                </div>
                <div>
                  <span className="text-gray-500">Avg P&L:</span>{' '}
                  <span className={s.avg_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}>
                    {formatINR(s.avg_pnl)}
                  </span>
                </div>
                <div>
                  <span className="text-gray-500">Best:</span>{' '}
                  <span className="text-emerald-400">{formatINR(s.best_trade)}</span>
                </div>
                <div>
                  <span className="text-gray-500">Worst:</span>{' '}
                  <span className="text-red-400">{formatINR(s.worst_trade)}</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Closed trade log table */}
      <div className="bg-[#1e2235] rounded-xl border border-[#2a2d3e] overflow-x-auto">
        <div className="p-3 border-b border-[#2a2d3e]">
          <h3 className="text-sm text-gray-400 font-medium">Closed Trades</h3>
        </div>
        <table className="w-full text-sm min-w-[700px]">
          <thead>
            <tr className="border-b border-[#2a2d3e] text-gray-500 text-xs uppercase">
              <th className="text-left p-3">Exit Date</th>
              <th className="text-left p-3">Symbol</th>
              <th className="text-left p-3">Strategy</th>
              <th className="text-left p-3">Dir</th>
              <th className="text-right p-3">Entry</th>
              <th className="text-right p-3">Exit</th>
              <th className="text-right p-3">Qty</th>
              <th className="text-right p-3">P&L</th>
              <th className="text-left p-3">Reason</th>
            </tr>
          </thead>
          <tbody>
            {trades?.map(t => (
              <tr key={t.id} className="border-b border-[#2a2d3e]/50 hover:bg-[#252840]">
                <td className="p-3 text-gray-400 text-xs">{t.exit_date?.slice(0, 16)}</td>
                <td className="p-3 text-white font-medium">{t.symbol?.replace('.NS', '')}</td>
                <td className="p-3 text-gray-400">{t.strategy?.replace(/_/g, ' ')}</td>
                <td className="p-3">
                  <span className={`px-1.5 py-0.5 rounded text-xs ${
                    t.direction === 'BUY' || t.direction === 'LONG' ? 'bg-emerald-500/15 text-emerald-400' : 'bg-red-500/15 text-red-400'
                  }`}>{t.direction}</span>
                </td>
                <td className="p-3 text-right text-gray-300">{formatINR(t.entry_price)}</td>
                <td className="p-3 text-right text-gray-300">{formatINR(t.exit_price || 0)}</td>
                <td className="p-3 text-right text-gray-400">{t.quantity}</td>
                <td className={`p-3 text-right font-medium ${t.pnl_net >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                  {t.pnl_net != null ? formatINR(t.pnl_net) : '—'}
                </td>
                <td className="p-3 text-gray-500 text-xs">{t.exit_reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!trades?.length && <p className="text-gray-600 text-sm p-6 text-center">No closed trades yet.</p>}
      </div>
    </div>
  );
}
