import { createContext, useContext, useState, useEffect, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api } from './api';

interface Account {
  id: string;
  type: string;
  label: string;
  starting_capital: number;
  current_capital: number;
  hard_floor: number;
}

interface AccountContextType {
  activeAccount: Account | null;
  accounts: Account[];
  switchAccount: (id: string) => Promise<void>;
  refreshAccounts: () => Promise<void>;
}

const AccountContext = createContext<AccountContextType>({
  activeAccount: null,
  accounts: [],
  switchAccount: async () => {},
  refreshAccounts: async () => {},
});

export function AccountProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [activeAccount, setActiveAccount] = useState<Account | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);

  const refreshAccounts = async () => {
    try {
      const [accs, active] = await Promise.all([api.getAccounts(), api.getActiveAccount()]);
      setAccounts(accs);
      setActiveAccount(active);
    } catch (e) {
      console.error('Failed to load accounts', e);
    }
  };

  const switchAccount = async (id: string) => {
    try {
      await api.setActiveAccount(id);
      await refreshAccounts();
      queryClient.invalidateQueries();
    } catch (e) {
      console.error('Failed to switch account', e);
    }
  };

  useEffect(() => { refreshAccounts(); }, []);

  return (
    <AccountContext.Provider value={{ activeAccount, accounts, switchAccount, refreshAccounts }}>
      {children}
    </AccountContext.Provider>
  );
}

export function useAccount() {
  return useContext(AccountContext);
}
