import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Toaster } from 'react-hot-toast';
import { AccountProvider } from './lib/account-context';
import Layout from './components/Layout';
import Dashboard from './pages/Dashboard';
import Signals from './pages/Signals';
import Trades from './pages/Trades';
import Risk from './pages/Risk';
import Control from './pages/Control';
import Settings from './pages/Settings';
import Logs from './pages/Logs';
import Performance from './pages/Performance';
import Replay from './pages/Replay';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 10000 },
  },
});

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AccountProvider>
      <Toaster
        position="top-right"
        toastOptions={{
          style: { background: '#1e2235', color: '#e4e6f0', border: '1px solid #2a2d3e', fontSize: '13px' },
          success: { iconTheme: { primary: '#10b981', secondary: '#1e2235' } },
          error: { iconTheme: { primary: '#ef4444', secondary: '#1e2235' } },
        }}
      />
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<Dashboard />} />
            <Route path="/performance" element={<Performance />} />
            <Route path="/signals" element={<Signals />} />
            <Route path="/trades" element={<Trades />} />
            <Route path="/risk" element={<Risk />} />
            <Route path="/control" element={<Control />} />
            <Route path="/replay" element={<Replay />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/logs" element={<Logs />} />
          </Route>
        </Routes>
      </BrowserRouter>
      </AccountProvider>
    </QueryClientProvider>
  );
}

export default App;
