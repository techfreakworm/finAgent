import { useState, useRef } from 'react';
import { Loader2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../lib/api';
import type { TaskStatus } from '../lib/api';

interface ActionButtonProps {
  label: string;
  action: () => Promise<{ task_id: string }>;
  icon?: React.ReactNode;
  variant?: 'default' | 'danger';
  onComplete?: (result: TaskStatus) => void;
}

export default function ActionButton({ label, action, icon, variant = 'default', onComplete }: ActionButtonProps) {
  const [loading, setLoading] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const handleClick = async () => {
    if (loading) return;
    setLoading(true);

    try {
      const resp = await action();
      const task_id = resp.task_id;
      toast.loading(`${label}...`, { id: task_id });

      // Poll for completion
      pollRef.current = setInterval(async () => {
        try {
          const status = await api.getTaskStatus(task_id);
          if (status.status === 'completed') {
            clearInterval(pollRef.current!);
            setLoading(false);
            toast.success(`${label} complete`, { id: task_id });
            onComplete?.(status);
          } else if (status.status === 'failed') {
            clearInterval(pollRef.current!);
            setLoading(false);
            toast.error(`${label} failed: ${status.error}`, { id: task_id });
          }
        } catch {
          // Keep polling
        }
      }, 2000);

      // Safety timeout: stop polling after 2 minutes
      setTimeout(() => {
        if (pollRef.current) {
          clearInterval(pollRef.current!);
          setLoading(false);
        }
      }, 120000);
    } catch (e) {
      setLoading(false);
      toast.error(`${label} failed: ${e}`);
    }
  };

  const base = variant === 'danger'
    ? 'bg-red-500/20 text-red-400 hover:bg-red-500/30 border-red-500/30'
    : 'bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30 border-emerald-500/30';

  return (
    <button
      onClick={handleClick}
      disabled={loading}
      className={`flex items-center gap-2 px-4 py-2.5 rounded-lg border text-sm font-medium transition-colors disabled:opacity-50 ${base}`}
    >
      {loading ? <Loader2 size={16} className="animate-spin" /> : icon}
      {label}
    </button>
  );
}
