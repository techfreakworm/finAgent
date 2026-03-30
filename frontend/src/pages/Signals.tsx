import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { Signal } from '../lib/api';
import { CheckCircle, XCircle, Clock } from 'lucide-react';

function formatINR(n: number) {
  return `₹${n.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
}

const statusBadge = (status: string) => {
  switch (status) {
    case 'EXECUTED': return <span className="flex items-center gap-1 text-emerald-400 text-xs"><CheckCircle size={12} />Executed</span>;
    case 'REJECTED': return <span className="flex items-center gap-1 text-red-400 text-xs"><XCircle size={12} />Rejected</span>;
    case 'PENDING': return <span className="flex items-center gap-1 text-amber-400 text-xs"><Clock size={12} />Pending</span>;
    default: return <span className="text-gray-500 text-xs">{status}</span>;
  }
};

export default function Signals() {
  const queryClient = useQueryClient();
  const { data: signals, isLoading } = useQuery({
    queryKey: ['signals'],
    queryFn: () => api.getSignals(50),
    refetchInterval: 10000,
  });

  const approve = useMutation({
    mutationFn: (id: number) => api.approveSignal(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['signals'] }),
  });

  const reject = useMutation({
    mutationFn: (id: number) => api.rejectSignal(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['signals'] }),
  });

  if (isLoading) return <div className="text-gray-500">Loading signals...</div>;

  return (
    <div>
      <h2 className="text-xl font-semibold text-white mb-4">Signals</h2>

      <div className="bg-[#1e2235] rounded-xl border border-[#2a2d3e] overflow-x-auto">
        <table className="w-full text-sm min-w-[700px]">
          <thead>
            <tr className="border-b border-[#2a2d3e] text-gray-500 text-xs uppercase">
              <th className="text-left p-3">Time</th>
              <th className="text-left p-3">Dir</th>
              <th className="text-left p-3">Symbol</th>
              <th className="text-left p-3">Strategy</th>
              <th className="text-right p-3">Price</th>
              <th className="text-right p-3">Qty</th>
              <th className="text-right p-3">Margin</th>
              <th className="text-center p-3">Status</th>
              <th className="text-center p-3">Action</th>
            </tr>
          </thead>
          <tbody>
            {signals?.map((s: Signal) => (
              <tr key={s.id} className="border-b border-[#2a2d3e]/50 hover:bg-[#252840] transition-colors">
                <td className="p-3 text-gray-400 text-xs">{s.created_at?.slice(5, 16)}</td>
                <td className="p-3">
                  <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${
                    s.direction === 'BUY' ? 'bg-emerald-500/15 text-emerald-400' : 'bg-red-500/15 text-red-400'
                  }`}>{s.direction}</span>
                </td>
                <td className="p-3 text-white font-medium">{s.symbol.replace('.NS', '')}</td>
                <td className="p-3 text-gray-400">{s.strategy.replace(/_/g, ' ')}</td>
                <td className="p-3 text-right text-white">{formatINR(s.entry_price)}</td>
                <td className="p-3 text-right text-gray-400">{s.lot_size}</td>
                <td className="p-3 text-right text-gray-400">{formatINR(s.margin_required)}</td>
                <td className="p-3 text-center">{statusBadge(s.status)}</td>
                <td className="p-3 text-center">
                  {s.status === 'PENDING' && (
                    <div className="flex gap-1 justify-center">
                      <button
                        onClick={() => approve.mutate(s.id)}
                        className="px-2 py-1 bg-emerald-500/20 text-emerald-400 rounded text-xs hover:bg-emerald-500/30 transition-colors"
                      >Approve</button>
                      <button
                        onClick={() => reject.mutate(s.id)}
                        className="px-2 py-1 bg-red-500/20 text-red-400 rounded text-xs hover:bg-red-500/30 transition-colors"
                      >Reject</button>
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!signals?.length && <p className="text-gray-600 text-sm p-6 text-center">No signals recorded yet.</p>}
      </div>

      {/* Signal detail on click could go here */}
    </div>
  );
}
