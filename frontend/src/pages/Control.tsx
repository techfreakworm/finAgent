import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import ActionButton from '../components/ActionButton';
import { Radar, Bot, FileText, XCircle, AlertOctagon, Play, Square } from 'lucide-react';
import toast from 'react-hot-toast';

export default function Control() {
  const queryClient = useQueryClient();
  const [report, setReport] = useState('');
  const [killConfirm, setKillConfirm] = useState('');

  const { data: tasks } = useQuery({
    queryKey: ['tasks'],
    queryFn: api.getTasks,
    refetchInterval: 5000,
  });

  const { data: health } = useQuery({
    queryKey: ['health'],
    queryFn: api.getHealth,
    refetchInterval: 30000,
  });

  const handleCloseAll = async () => {
    if (killConfirm !== 'STOP') {
      toast.error('Type STOP to confirm');
      return;
    }
    try {
      await api.closeAllPositions();
      toast.success('All positions closed');
      setKillConfirm('');
      queryClient.invalidateQueries();
    } catch (e) {
      toast.error(`Failed: ${e}`);
    }
  };

  const refreshAll = () => queryClient.invalidateQueries();

  return (
    <div>
      <h2 className="text-xl font-semibold text-white mb-2">Control Panel</h2>
      <p className="text-sm text-gray-500 mb-6">Run scans, execute AI workflows, and manage positions</p>

      {/* Quick Actions */}
      <div className="bg-[#1e2235] rounded-xl border border-[#2a2d3e] p-5 mb-6">
        <h3 className="text-sm text-gray-400 mb-4 uppercase tracking-wider">Quick Actions</h3>
        <div className="flex flex-wrap gap-3">
          <ActionButton
            label="Scan Markets"
            icon={<Radar size={16} />}
            action={api.triggerScan}
            onComplete={(r) => {
              const res = r.result as { signals_found?: number };
              toast.success(`Found ${res?.signals_found ?? 0} signals`);
              refreshAll();
            }}
          />
          <ActionButton
            label="Run AI Workflow"
            icon={<Bot size={16} />}
            action={api.triggerWorkflow}
            onComplete={(r) => {
              const res = r.result as { report?: string; executed?: number };
              if (res?.report) setReport(res.report);
              refreshAll();
            }}
          />
          <ActionButton
            label="Generate Report"
            icon={<FileText size={16} />}
            action={api.triggerReport}
            onComplete={() => {
              toast.success('Report saved');
              refreshAll();
            }}
          />
        </div>
      </div>

      {/* Scheduler */}
      <div className="bg-[#1e2235] rounded-xl border border-[#2a2d3e] p-5 mb-6">
        <h3 className="text-sm text-gray-400 mb-3 uppercase tracking-wider">Automated Scheduler</h3>
        <p className="text-sm text-gray-500 mb-4">
          Runs trading strategies automatically during market hours (9 AM - 4 PM IST, Mon-Fri).
          Options scan on Monday, equity scan daily at 3:35 PM, hourly MTM monitoring.
        </p>
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <div className={`w-2.5 h-2.5 rounded-full ${health?.scheduler ? 'bg-amber-400 animate-pulse' : 'bg-gray-600'}`} />
            <span className={`text-sm font-medium ${health?.scheduler ? 'text-amber-400' : 'text-gray-500'}`}>
              {health?.scheduler ? 'Running' : 'Stopped'}
            </span>
          </div>
          {health?.scheduler ? (
            <button
              onClick={async () => { await api.stopScheduler(); toast.success('Scheduler stopped'); queryClient.invalidateQueries({ queryKey: ['health'] }); }}
              className="flex items-center gap-1.5 px-3 py-2 rounded-lg border text-sm font-medium transition-colors bg-red-500/10 text-red-400 hover:bg-red-500/20 border-red-500/20"
            >
              <Square size={14} />
              Stop
            </button>
          ) : (
            <button
              onClick={async () => { await api.startScheduler(); toast.success('Scheduler started'); queryClient.invalidateQueries({ queryKey: ['health'] }); }}
              className="flex items-center gap-1.5 px-3 py-2 rounded-lg border text-sm font-medium transition-colors bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20 border-emerald-500/20"
            >
              <Play size={14} />
              Start
            </button>
          )}
        </div>
      </div>

      {/* System Status */}
      <div className="bg-[#1e2235] rounded-xl border border-[#2a2d3e] p-5 mb-6">
        <h3 className="text-sm text-gray-400 mb-3 uppercase tracking-wider">System Status</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
          <div className="flex items-center gap-2">
            <div className={`w-2 h-2 rounded-full ${health?.status === 'ok' ? 'bg-emerald-400' : 'bg-red-400'}`} />
            <span className="text-gray-300">API</span>
          </div>
          <div className="flex items-center gap-2">
            <div className={`w-2 h-2 rounded-full ${health?.llm?.available ? 'bg-emerald-400' : 'bg-amber-400'}`} />
            <span className="text-gray-300">
              LLM: {health?.llm?.available ? health.llm.model : 'Not loaded'}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <div className={`w-2 h-2 rounded-full ${health?.scheduler ? 'bg-amber-400' : 'bg-gray-600'}`} />
            <span className="text-gray-300">
              Scheduler: {health?.scheduler ? 'Active' : 'Off'}
            </span>
          </div>
        </div>
      </div>

      {/* AI Report Display */}
      {report && (
        <div className="bg-[#1e2235] rounded-xl border border-[#2a2d3e] p-5 mb-6">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm text-gray-400 uppercase tracking-wider">AI Report</h3>
            <button onClick={() => setReport('')} className="text-gray-500 hover:text-gray-300 text-xs">Dismiss</button>
          </div>
          <pre className="text-sm text-gray-300 whitespace-pre-wrap font-mono leading-relaxed">{report}</pre>
        </div>
      )}

      {/* Recent Tasks */}
      <div className="bg-[#1e2235] rounded-xl border border-[#2a2d3e] p-5 mb-6">
        <h3 className="text-sm text-gray-400 mb-3 uppercase tracking-wider">Recent Tasks</h3>
        {!tasks?.length ? (
          <p className="text-gray-600 text-sm">No tasks run yet. Click a button above to start.</p>
        ) : (
          <div className="space-y-2">
            {tasks.map((t) => (
              <div key={t.id} className="flex items-center justify-between py-2 px-3 rounded bg-[#0f1117] text-sm">
                <div className="flex items-center gap-2">
                  <div className={`w-2 h-2 rounded-full ${
                    t.status === 'completed' ? 'bg-emerald-400' :
                    t.status === 'running' ? 'bg-amber-400 animate-pulse' : 'bg-red-400'
                  }`} />
                  <span className="text-gray-300">{t.name}</span>
                </div>
                <div className="flex items-center gap-3">
                  <span className={`text-xs ${
                    t.status === 'completed' ? 'text-emerald-400' :
                    t.status === 'running' ? 'text-amber-400' : 'text-red-400'
                  }`}>{t.status}</span>
                  <span className="text-xs text-gray-600">{t.started_at?.slice(11, 19)}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Kill Switch */}
      <div className="bg-red-500/5 rounded-xl border border-red-500/20 p-5">
        <h3 className="text-sm text-red-400 mb-3 uppercase tracking-wider flex items-center gap-2">
          <AlertOctagon size={16} />
          Emergency Controls
        </h3>
        <p className="text-sm text-gray-400 mb-4">
          Close all open positions immediately. This cannot be undone.
        </p>
        <div className="flex items-center gap-3">
          <input
            type="text"
            value={killConfirm}
            onChange={(e) => setKillConfirm(e.target.value)}
            placeholder='Type "STOP" to confirm'
            className="px-3 py-2 bg-[#0f1117] border border-red-500/30 rounded-lg text-sm text-white placeholder-gray-600 w-48 focus:outline-none focus:border-red-500"
          />
          <button
            onClick={handleCloseAll}
            disabled={killConfirm !== 'STOP'}
            className="flex items-center gap-2 px-4 py-2 bg-red-500/20 text-red-400 rounded-lg text-sm font-medium border border-red-500/30 hover:bg-red-500/30 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            <XCircle size={16} />
            Close All Positions
          </button>
        </div>
      </div>
    </div>
  );
}
