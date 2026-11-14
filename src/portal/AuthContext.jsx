import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { Activity } from 'lucide-react';
import { auth, setCsrfToken, setUnauthenticatedHandler } from './api';

const AuthCtx = createContext(null);
export const useAuth = () => useContext(AuthCtx);

export function applyTheme(theme) {
  const resolved = theme === 'system' ? (window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : theme;
  if (resolved) { document.documentElement.dataset.theme = resolved; try { localStorage.setItem('medaudit-theme-v2', resolved); } catch { /* ignore */ } }
}

// Central authentication state. The server session is the only source of truth: on load we ask
// /auth/me; the role and permissions it returns drive routing. Nothing sensitive is persisted client-side.
export function AuthProvider({ children }) {
  const [state, setState] = useState({ status: 'loading', user: null, preferences: null, reason: null });

  const accept = useCallback(data => {
    setCsrfToken(data.csrf_token);
    setState({ status: 'authenticated', user: data.user, preferences: data.preferences || null, reason: null });
    if (data.preferences?.theme) applyTheme(data.preferences.theme);
    return data;
  }, []);

  const refresh = useCallback(() => auth.me().then(accept).catch(err => {
    setCsrfToken(null);
    setState({ status: err.status === 401 ? 'anonymous' : 'error', user: null, preferences: null,
      reason: err.status === 401 ? null : err.message });
  }), [accept]);

  useEffect(() => {
    setUnauthenticatedHandler(() => {
      setCsrfToken(null);
      setState(s => s.status === 'authenticated' ? { status: 'anonymous', user: null, preferences: null, reason: 'expired' } : s);
    });
    refresh();
  }, [refresh]);

  const value = useMemo(() => ({
    ...state,
    refresh,
    login: async body => accept(await auth.login(body)),
    activate: async body => accept(await auth.activate(body)),
    logout: async () => {
      try { await auth.logout(); } catch { /* session may already be gone */ }
      setCsrfToken(null);
      setState({ status: 'anonymous', user: null, preferences: null, reason: 'signed_out' });
    },
    setPreferences: preferences => setState(s => ({ ...s, preferences })),
  }), [state, refresh, accept]);

  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}

export function Splash({ message = 'Checking your session…' }) {
  return <div className="auth-splash" role="status"><div className="brand-mark"><Activity size={18} /></div><span>{message}</span></div>;
}

// Route guard. `roles` limits access; the server enforces the same rules on every API call.
export function RequireAuth({ roles, deny, children }) {
  const { status, user, reason } = useAuth();
  const location = useLocation();
  if (status === 'loading') return <Splash />;
  if (status === 'error') return <Splash message={reason || 'The MedAudit service is unavailable.'} />;
  if (status !== 'authenticated') {
    return <Navigate to="/login" replace state={{ from: location.pathname, reason }} />;
  }
  if ((roles && !roles.includes(user.role)) || (deny && deny.includes(user.role))) {
    return <Navigate to="/unauthorized" replace state={{ home: user.home }} />;
  }
  return children;
}
