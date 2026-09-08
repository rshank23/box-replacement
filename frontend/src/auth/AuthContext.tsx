import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

import { api, ApiError, configureApi } from '@/api/client';

const STORAGE_KEY = 'dmu.session';
const ADMIN_ROLE = 'tmf_admin';

export interface Session {
  token: string | null;
  subject: string;
  roles: string[];
  /** True when the API runs with AUTH_ENABLED=false and no token was issued. */
  openAccess: boolean;
}

interface AuthContextValue extends Session {
  isAuthenticated: boolean;
  isAdmin: boolean;
  signIn: (email: string, roles: string[]) => Promise<void>;
  signOut: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function readStoredSession(): Session | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

// Registered at module load so the very first query already carries the bearer token.
let handleUnauthorized: () => void = () => {};
configureApi({
  readToken: () => readStoredSession()?.token ?? null,
  onUnauthorized: () => handleUnauthorized(),
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(readStoredSession);

  const signOut = useCallback(() => {
    sessionStorage.removeItem(STORAGE_KEY);
    setSession(null);
  }, []);

  useEffect(() => {
    handleUnauthorized = signOut;
  }, [signOut]);

  const signIn = useCallback(async (email: string, roles: string[]) => {
    let next: Session = { token: null, subject: email, roles, openAccess: true };
    try {
      const response = await api.post<{ access_token: string }>('/api/admin/dev-token', {
        subject: email,
        roles,
        ttl_seconds: 28800,
      });
      next = { token: response.access_token, subject: email, roles, openAccess: false };
    } catch (error) {
      // A 404 means DEV_TOKEN_ENABLED is off. That is expected wherever the API either
      // runs with AUTH_ENABLED=false or sits behind enterprise SSO.
      if (!(error instanceof ApiError) || error.status !== 404) throw error;
    }
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    setSession(next);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      token: session?.token ?? null,
      subject: session?.subject ?? '',
      roles: session?.roles ?? [],
      openAccess: session?.openAccess ?? false,
      isAuthenticated: session !== null,
      isAdmin: (session?.roles ?? []).includes(ADMIN_ROLE),
      signIn,
      signOut,
    }),
    [session, signIn, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used inside an AuthProvider');
  return context;
}
