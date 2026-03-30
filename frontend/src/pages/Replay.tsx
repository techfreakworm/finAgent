import { useState, useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Play, Pause, SkipForward, Square, Loader2, Calendar, BarChart3 } from 'lucide-react';
import toast from 'react-hot-toast';
import StatCard from '../components/StatCard';
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';

const BASE = '/api';

function formatINR(n: number) {
  if (Math.abs(n) >= 100000) return `₹${(n / 100000).toFixed(1)}L`;
  if (Math.abs(n) >= 1000) return `₹${(n / 1000).toFixed(1)}K`;
  return `₹${n.toFixed(0)}`;
}

type Phase = 'setup' | 'preparing' | 'ready' | 'playing' | 'paused' | 'complete';

interface ReplayEvent {
  index: number;
  type: string;
  data: any;
}

export default function Replay() {
  const queryClient = useQueryClient();
  const [initialized, setInitialized] = useState(false);

  // Setup state
  const [fromDate, setFromDate] = useState('2025-01-01');
  const [toDate, setToDate] = useState('2025-03-31');
  const [interval, setInterval_] = useState('60');
  const [strategies, setStrategies] = useState(['nifty_strangle', 'equity_mean_reversion']);
  const [capital, setCapital] = useState(500000);
  const [speed, setSpeed] = useState(5);

  // Replay state
  const [phase, setPhase] = useState<Phase>('setup');
  const [progress, setProgress] = useState(0);
  const [totalCandles, setTotalCandles] = useState(0);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [currentTimestamp, setCurrentTimestamp] = useState('');
  const [replayCapital, setReplayCapital] = useState(capital);
  const [positions, setPositions] = useState(0);
  const [signals, setSignals] = useState<any[]>([]);
  const [equityCurve, setEquityCurve] = useState<{index: number; capital: number; time: string}[]>([]);
  const [summary, setSummary] = useState<any>(null);

  const pollRef = useRef<ReturnType<typeof window.setInterval> | null>(null);
  const eventIndexRef = useRef(0);

  // On mount: check if there's an existing completed replay to show
  useEffect(() => {
    if (initialized) return;
    (async () => {
      try {
        const res = await fetch(`${BASE}/replay/status`);
        if (!res.ok) return;
        const st = await res.json();
        if (st.status === 'completed' && st.total_candles > 0) {
          setTotalCandles(st.total_candles);
          setCurrentIndex(st.current_index || st.total_candles);

          // Load summary
          const sumRes = await fetch(`${BASE}/replay/summary`);
          if (sumRes.ok) {
            const sum = await sumRes.json();
            setSummary(sum);
            setReplayCapital(sum.final_capital || capital);
            setSignals([]); // signals are in DB, accessible via Signals page
          }

          // Load state for positions/signals count
          const stateRes = await fetch(`${BASE}/replay/state`);
          if (stateRes.ok) {
            const state = await stateRes.json();
            setReplayCapital(state.capital || capital);
            setPositions(state.positions?.length || 0);
          }

          setPhase('complete');
        }
      } catch {}
      setInitialized(true);
    })();
  }, [initialized]);

  // Poll for events during replay
  useEffect(() => {
    if (phase === 'playing' || phase === 'paused') {
      pollRef.current = window.setInterval(async () => {
        try {
          // Also check replay status for completion
          const statusRes = await fetch(`${BASE}/replay/status`);
          if (statusRes.ok) {
            const st = await statusRes.json();
            if (st.current_index > 0) {
              setCurrentIndex(st.current_index);
              setTotalCandles(st.total_candles || totalCandles);
            }
            if (st.status === 'completed') {
              // Fetch summary
              try {
                const sumRes = await fetch(`${BASE}/replay/summary`);
                if (sumRes.ok) {
                  const sum = await sumRes.json();
                  setSummary(sum);
                }
              } catch {}
              setPhase('complete');
              setReplayCapital(st.capital || replayCapital);
              toast.success('Replay complete');
            }
          }
          const res = await fetch(`${BASE}/replay/events?since=${eventIndexRef.current}`);
          if (!res.ok) return;
          const body = await res.json();
          // API may return {events: [...]} or [...] directly
          const events: ReplayEvent[] = Array.isArray(body) ? body : (body.events || []);

          for (const evt of events) {
            eventIndexRef.current = Math.max(eventIndexRef.current, evt.index + 1);

            if (evt.type === 'candle') {
              setCurrentIndex(evt.data.index);
              setTotalCandles(evt.data.total);
              setCurrentTimestamp(evt.data.timestamp || '');
              const p = evt.data.portfolio;
              if (p) {
                setReplayCapital(p.capital);
                setPositions(p.positions || 0);
                setEquityCurve(prev => [...prev, {
                  index: evt.data.index,
                  capital: p.capital,
                  time: (evt.data.timestamp || '').slice(5, 10),
                }]);
              }
            } else if (evt.type === 'signal') {
              setSignals(prev => [evt.data, ...prev].slice(0, 50));
            } else if (evt.type === 'complete') {
              setPhase('complete');
              setSummary(evt.data);
              toast.success('Replay complete');
            }
          }
        } catch { /* ignore poll errors */ }
      }, 500);

      return () => { if (pollRef.current) clearInterval(pollRef.current); };
    }
  }, [phase]);

  // Poll for prepare progress
  useEffect(() => {
    if (phase === 'preparing') {
      const timer = window.setInterval(async () => {
        try {
          const res = await fetch(`${BASE}/replay/status`);
          const data = await res.json();
          setProgress(data.progress || 0);
          if (data.status === 'ready') {
            setPhase('ready');
            setTotalCandles(data.total_candles || 0);
            toast.success(`Data ready: ${data.total_candles} candles`);
          } else if (data.status === 'error') {
            setPhase('setup');
            toast.error(`Prepare failed: ${data.error || 'Unknown error'}`);
          }
        } catch { /* ignore */ }
      }, 1000);
      return () => clearInterval(timer);
    }
  }, [phase]);

  const handlePrepare = async () => {
    setPhase('preparing');
    setProgress(0);
    eventIndexRef.current = 0;
    setSignals([]);
    setEquityCurve([]);
    setSummary(null);
    try {
      await fetch(`${BASE}/replay/prepare`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ from_date: fromDate, to_date: toDate, interval, strategies, capital, floor: capital * 0.8 }),
      });
    } catch (e) {
      toast.error(`Prepare failed: ${e}`);
      setPhase('setup');
    }
  };

  const handleStart = async () => {
    setPhase('playing');
    await fetch(`${BASE}/replay/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ speed }),
    });
  };

  const handleControl = async (action: string, body?: any) => {
    await fetch(`${BASE}/replay/${action}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (action === 'pause') setPhase('paused');
    if (action === 'resume') setPhase('playing');
    if (action === 'stop') { setPhase('complete'); queryClient.invalidateQueries(); }
  };

  const handleSpeedChange = (newSpeed: number) => {
    setSpeed(newSpeed);
    if (phase === 'playing') {
      handleControl('speed', { value: newSpeed });
    }
  };

  const toggleStrategy = (name: string) => {
    setStrategies(prev => prev.includes(name) ? prev.filter(s => s !== name) : [...prev, name]);
  };

  const pnl = replayCapital - capital;
  const pnlPct = capital > 0 ? (pnl / capital) * 100 : 0;
  const progressPct = totalCandles > 0 ? (currentIndex / totalCandles) * 100 : 0;

  return (
    <div>
      <h2 className="text-xl font-semibold text-white mb-1">Historical Replay</h2>
      <p className="text-sm text-gray-500 mb-6">Stream historical data and watch strategies execute in real-time</p>

      {/* SETUP PANEL */}
      {(phase === 'setup' || phase === 'preparing' || phase === 'ready') && (
        <div className="bg-[#1e2235] rounded-xl border border-[#2a2d3e] p-5 mb-6">
          <h3 className="text-sm text-gray-400 mb-4 uppercase tracking-wider flex items-center gap-2">
            <Calendar size={14} /> Replay Setup
          </h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-4 mb-4">
            <div>
              <label className="block text-xs text-gray-500 mb-1">From Date</label>
              <input type="date" value={fromDate} onChange={e => setFromDate(e.target.value)}
                className="w-full px-3 py-2 bg-[#0f1117] border border-[#2a2d3e] rounded-lg text-white text-sm" />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">To Date</label>
              <input type="date" value={toDate} onChange={e => setToDate(e.target.value)}
                className="w-full px-3 py-2 bg-[#0f1117] border border-[#2a2d3e] rounded-lg text-white text-sm" />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Interval</label>
              <select value={interval} onChange={e => setInterval_(e.target.value)}
                className="w-full px-3 py-2 bg-[#0f1117] border border-[#2a2d3e] rounded-lg text-white text-sm">
                <option value="1">1 minute</option>
                <option value="5">5 minutes</option>
                <option value="15">15 minutes</option>
                <option value="60">1 hour (recommended)</option>
                <option value="D">Daily</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Starting Capital</label>
              <input type="number" value={capital} onChange={e => setCapital(Number(e.target.value))}
                className="w-full px-3 py-2 bg-[#0f1117] border border-[#2a2d3e] rounded-lg text-white text-sm" />
            </div>
          </div>

          <div className="mb-4">
            <label className="block text-xs text-gray-500 mb-2">Strategies</label>
            <div className="flex gap-3">
              {[
                { id: 'nifty_strangle', label: 'NIFTY Short Strangle' },
                { id: 'equity_mean_reversion', label: 'Equity Mean Reversion' },
                { id: 'equity_momentum', label: 'Equity Momentum' },
              ].map(s => (
                <button key={s.id} onClick={() => toggleStrategy(s.id)}
                  className={`px-3 py-1.5 rounded-lg text-xs border transition-colors ${
                    strategies.includes(s.id)
                      ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30'
                      : 'bg-[#0f1117] text-gray-500 border-[#2a2d3e]'
                  }`}>
                  {s.label}
                </button>
              ))}
            </div>
          </div>

          {phase === 'preparing' && (
            <div className="mb-4">
              <div className="flex items-center gap-2 text-sm text-amber-400 mb-2">
                <Loader2 size={14} className="animate-spin" /> Fetching historical data...
              </div>
              <div className="h-2 bg-[#0f1117] rounded-full overflow-hidden">
                <div className="h-full bg-amber-500 transition-all" style={{ width: `${progress}%` }} />
              </div>
              <p className="text-xs text-gray-600 mt-1">{progress}% complete</p>
            </div>
          )}

          <div className="flex gap-3">
            {phase === 'setup' && (
              <button onClick={handlePrepare}
                className="px-4 py-2 bg-blue-500/20 text-blue-400 border border-blue-500/30 rounded-lg text-sm font-medium hover:bg-blue-500/30 transition-colors">
                Prepare Data
              </button>
            )}
            {phase === 'ready' && (
              <button onClick={handleStart}
                className="px-4 py-2 bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 rounded-lg text-sm font-medium hover:bg-emerald-500/30 transition-colors flex items-center gap-2">
                <Play size={14} /> Start Replay ({totalCandles} candles)
              </button>
            )}
          </div>
        </div>
      )}

      {/* PLAYBACK CONTROLS */}
      {(phase === 'playing' || phase === 'paused' || phase === 'complete') && (
        <>
          <div className="bg-[#1e2235] rounded-xl border border-blue-500/20 p-4 mb-4">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-3">
                {phase !== 'complete' && (
                  <>
                    <button onClick={() => handleControl(phase === 'playing' ? 'pause' : 'resume')}
                      className="p-2 bg-blue-500/20 text-blue-400 rounded-lg hover:bg-blue-500/30 transition-colors">
                      {phase === 'playing' ? <Pause size={16} /> : <Play size={16} />}
                    </button>
                    <button onClick={() => handleControl('step')}
                      className="p-2 bg-[#0f1117] text-gray-400 rounded-lg hover:text-white transition-colors" title="Step forward">
                      <SkipForward size={16} />
                    </button>
                    <button onClick={() => handleControl('stop')}
                      className="p-2 bg-red-500/20 text-red-400 rounded-lg hover:bg-red-500/30 transition-colors">
                      <Square size={16} />
                    </button>
                  </>
                )}
                {phase === 'complete' && (
                  <button onClick={() => { setPhase('setup'); setSignals([]); setEquityCurve([]); setSummary(null); }}
                    className="px-3 py-1.5 bg-blue-500/20 text-blue-400 rounded-lg text-sm hover:bg-blue-500/30">
                    New Replay
                  </button>
                )}

                <div className="flex gap-1 ml-4">
                  {[1, 2, 5, 10, 25, 50].map(s => (
                    <button key={s} onClick={() => handleSpeedChange(s)}
                      className={`px-2 py-1 rounded text-xs transition-colors ${
                        speed === s ? 'bg-blue-500/30 text-blue-400' : 'bg-[#0f1117] text-gray-500 hover:text-gray-300'
                      }`}>
                      {s}x
                    </button>
                  ))}
                </div>
              </div>

              <div className="text-right">
                <div className="text-sm text-white font-mono">{currentTimestamp.slice(0, 16)}</div>
                <div className="text-xs text-gray-500">Candle {currentIndex}/{totalCandles}</div>
              </div>
            </div>

            <div className="h-1.5 bg-[#0f1117] rounded-full overflow-hidden">
              <div className="h-full bg-blue-500 transition-all" style={{ width: `${progressPct}%` }} />
            </div>
          </div>

          {/* KPI CARDS */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-4">
            <StatCard label="Capital" value={formatINR(replayCapital)} variant={pnl >= 0 ? 'green' : 'red'}
              sub={`Started: ${formatINR(capital)}`} />
            <StatCard label="P&L" value={`${pnl >= 0 ? '+' : ''}${formatINR(pnl)}`}
              variant={pnl >= 0 ? 'green' : 'red'} sub={`${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(1)}%`} />
            <StatCard label="Positions" value={String(positions)} />
            <StatCard label="Signals" value={String(signals.length)} sub={phase === 'complete' ? 'Replay ended' : 'Live'} />
          </div>

          {/* EQUITY CURVE */}
          {equityCurve.length > 1 && (
            <div className="bg-[#1e2235] rounded-xl border border-[#2a2d3e] p-4 mb-4">
              <h3 className="text-sm text-gray-400 mb-3">Equity Curve</h3>
              <ResponsiveContainer width="100%" height={200}>
                <AreaChart data={equityCurve}>
                  <defs>
                    <linearGradient id="replayGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3} />
                      <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="time" tick={{ fontSize: 10, fill: '#6b7280' }} />
                  <YAxis tick={{ fontSize: 10, fill: '#6b7280' }} tickFormatter={v => formatINR(Number(v))} />
                  <Tooltip contentStyle={{ background: '#1e2235', border: '1px solid #2a2d3e', borderRadius: 8, fontSize: 12 }}
                    formatter={(v: any) => [formatINR(Number(v)), 'Capital']} />
                  <Area type="monotone" dataKey="capital" stroke="#3b82f6" fill="url(#replayGrad)" strokeWidth={2} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* SIGNAL LOG */}
          <div className="bg-[#1e2235] rounded-xl border border-[#2a2d3e] p-4 mb-4">
            <h3 className="text-sm text-gray-400 mb-3">Signal Log</h3>
            <div className="max-h-48 overflow-y-auto space-y-1">
              {signals.length === 0 ? (
                <p className="text-gray-600 text-xs">No signals yet...</p>
              ) : signals.map((s, i) => (
                <div key={i} className="flex items-center justify-between py-1 px-2 rounded bg-[#0f1117] text-xs">
                  <div className="flex items-center gap-2">
                    <span className={`px-1 py-0.5 rounded ${
                      s.direction === 'BUY' ? 'bg-emerald-500/15 text-emerald-400' : 'bg-red-500/15 text-red-400'
                    }`}>{s.direction}</span>
                    <span className="text-white">{s.symbol}</span>
                  </div>
                  <span className="text-gray-500">{formatINR(s.price || 0)}</span>
                </div>
              ))}
            </div>
          </div>

          {/* SUMMARY (after complete) */}
          {phase === 'complete' && summary && (
            <div className="bg-[#1e2235] rounded-xl border border-blue-500/20 p-5">
              <h3 className="text-sm text-blue-400 mb-4 uppercase tracking-wider flex items-center gap-2">
                <BarChart3 size={14} /> Replay Summary
              </h3>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm mb-4">
                <div><span className="text-gray-500">Starting Capital:</span> <span className="text-white">{formatINR(summary.starting_capital || capital)}</span></div>
                <div><span className="text-gray-500">Final Capital:</span> <span className={summary.total_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}>{formatINR(summary.final_capital || replayCapital)}</span></div>
                <div><span className="text-gray-500">Total P&L:</span> <span className={summary.total_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}>{formatINR(summary.total_pnl || 0)}</span></div>
                <div><span className="text-gray-500">Return:</span> <span className={summary.total_pnl_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}>{(summary.total_pnl_pct || 0).toFixed(1)}%</span></div>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm mb-4">
                <div><span className="text-gray-500">Trades:</span> <span className="text-white">{summary.total_trades || 0}</span></div>
                <div><span className="text-gray-500">Winners:</span> <span className="text-emerald-400">{summary.winners || 0}</span></div>
                <div><span className="text-gray-500">Win Rate:</span> <span className="text-white">{((summary.win_rate || 0) * 100).toFixed(0)}%</span></div>
                <div><span className="text-gray-500">Max DD:</span> <span className="text-red-400">{(summary.max_drawdown_pct || summary.max_drawdown || 0).toFixed(1)}%</span></div>
              </div>
              {summary.strategy_stats && (
                <div className="mt-3 pt-3 border-t border-[#2a2d3e]">
                  <h4 className="text-xs text-gray-500 mb-2 uppercase">Per-Strategy</h4>
                  <div className="space-y-1">
                    {Object.entries(summary.strategy_stats).map(([name, s]: [string, any]) => (
                      <div key={name} className="flex items-center justify-between text-xs py-1">
                        <span className="text-gray-400">{name.replace(/_/g, ' ')}</span>
                        <div className="flex gap-4">
                          <span className="text-gray-500">{s.total_trades} trades</span>
                          <span className="text-gray-500">{((s.win_rate || 0) * 100).toFixed(0)}% win</span>
                          <span className={s.total_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}>{formatINR(s.total_pnl || 0)}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              <p className="text-xs text-gray-600 mt-3">
                Switch to this replay account in the sidebar to see all trades and signals on the Trades/Signals pages.
              </p>
            </div>
          )}
        </>
      )}
    </div>
  );
}
