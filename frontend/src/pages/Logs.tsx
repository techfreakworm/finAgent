import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { AlertCircle, Info, AlertTriangle } from 'lucide-react';

const levelIcon = (type: string) => {
  if (type.includes('ERROR') || type.includes('BREACH')) return <AlertCircle size={12} className="text-red-400" />;
  if (type.includes('WARNING') || type.includes('REJECT')) return <AlertTriangle size={12} className="text-amber-400" />;
  return <Info size={12} className="text-blue-400" />;
};

const levelColor = (type: string) => {
  if (type.includes('ERROR') || type.includes('BREACH')) return 'text-red-400';
  if (type.includes('WARNING') || type.includes('REJECT')) return 'text-amber-400';
  return 'text-blue-400';
};

export default function Logs() {
  const { data: logs, isLoading } = useQuery({
    queryKey: ['logs'],
    queryFn: () => api.getLogs(200),
    refetchInterval: 5000,
  });

  if (isLoading) return <div className="text-gray-500">Loading logs...</div>;

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-xl font-semibold text-white">System Logs</h2>
          <p className="text-sm text-gray-500">Events, trades, errors — auto-refreshes every 5 seconds</p>
        </div>
        <span className="text-xs text-gray-600">{logs?.length || 0} entries</span>
      </div>

      <div className="bg-[#1e2235] rounded-xl border border-[#2a2d3e] overflow-hidden">
        <div className="max-h-[70vh] overflow-y-auto">
          {!logs?.length ? (
            <p className="text-gray-600 text-sm p-6 text-center">No events recorded yet. Run a scan to generate events.</p>
          ) : (
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-[#1e2235]">
                <tr className="border-b border-[#2a2d3e] text-gray-500 text-xs uppercase">
                  <th className="text-left p-3 w-36">Time</th>
                  <th className="text-left p-3 w-36">Type</th>
                  <th className="text-left p-3">Message</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((log) => (
                  <tr key={log.id} className="border-b border-[#2a2d3e]/30 hover:bg-[#252840]">
                    <td className="p-3 text-gray-500 text-xs font-mono">{log.created_at?.slice(5, 19)}</td>
                    <td className="p-3">
                      <span className={`flex items-center gap-1.5 text-xs font-medium ${levelColor(log.event_type)}`}>
                        {levelIcon(log.event_type)}
                        {log.event_type}
                      </span>
                    </td>
                    <td className="p-3 text-gray-300 text-xs">{log.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
