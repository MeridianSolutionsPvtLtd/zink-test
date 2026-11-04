import React, { createContext, useContext, useMemo, useState } from 'react';

/** Fixed demo credentials — no signup / forgot password */
export const FIXED_CREDENTIALS = {
  userId: 'admin',
  password: 'StockPulse@2026',
} as const;

const AUTH_STORAGE_KEY = 'stockpulse-auth';

interface AuthContextType {
  isAuthenticated: boolean;
  userId: string | null;
  login: (userId: string, password: string) => { ok: true } | { ok: false; error: string };
  logout: () => void;
}

const AuthContext = createContext<AuthContextType>({
  isAuthenticated: false,
  userId: null,
  login: () => ({ ok: false, error: 'Auth not ready' }),
  logout: () => {},
});

export const useAuth = () => useContext(AuthContext);

function readStoredAuth(): { userId: string } | null {
  try {
    const raw = localStorage.getItem(AUTH_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { userId?: string };
    if (parsed?.userId) return { userId: parsed.userId };
  } catch {
    /* ignore */
  }
  return null;
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [userId, setUserId] = useState<string | null>(() => readStoredAuth()?.userId ?? null);

  const value = useMemo<AuthContextType>(
    () => ({
      isAuthenticated: Boolean(userId),
      userId,
      login: (id, password) => {
        const trimmedId = id.trim();
        if (
          trimmedId === FIXED_CREDENTIALS.userId &&
          password === FIXED_CREDENTIALS.password
        ) {
          localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify({ userId: trimmedId }));
          setUserId(trimmedId);
          return { ok: true };
        }
        return { ok: false, error: 'Invalid User ID or Password' };
      },
      logout: () => {
        localStorage.removeItem(AUTH_STORAGE_KEY);
        setUserId(null);
      },
    }),
    [userId]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
