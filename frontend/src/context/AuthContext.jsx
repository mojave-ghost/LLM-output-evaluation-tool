/**
 * AuthContext — in-memory JWT token management.
 *
 * Token storage strategy
 * ----------------------
 * Access tokens live in React state (component memory), never localStorage.
 * This prevents XSS attacks from reading the token between page navigations.
 * Trade-off: the user must re-authenticate on a hard page refresh, which is
 * acceptable for MVP.
 *
 * The refresh token is stored in the same in-memory state.  If persistence
 * across refreshes is needed later, it can be moved to an httpOnly cookie on
 * the server side without changing this module's interface.
 *
 * 401 handling
 * ------------
 * api/index.js dispatches a 'auth:expired' CustomEvent on any 401 response.
 * This context listens for that event and clears auth state, triggering a
 * redirect to the login page via the PrivateRoute guard in App.jsx.
 */

import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { auth as authApi, setToken, clearToken } from '../api/index.js';

// ---------------------------------------------------------------------------
// Context + hook
// ---------------------------------------------------------------------------

const AuthContext = createContext(null);

/** Consume auth state and actions anywhere inside <AuthProvider>. */
export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);       // { email }
  const [accessToken, setAccessToken] = useState(null);
  const [refreshToken, setRefreshToken] = useState(null);

  // True once we have confirmed the user is authenticated
  const isAuthenticated = Boolean(accessToken);

  // ── Persist tokens in the api module whenever they change ──────────────────
  useEffect(() => {
    if (accessToken) setToken(accessToken);
    else clearToken();
  }, [accessToken]);

  // ── Listen for 401 events fired by the API client ─────────────────────────
  useEffect(() => {
    const handleExpired = () => {
      setUser(null);
      setAccessToken(null);
      setRefreshToken(null);
    };
    window.addEventListener('auth:expired', handleExpired);
    return () => window.removeEventListener('auth:expired', handleExpired);
  }, []);

  // ── Actions ────────────────────────────────────────────────────────────────

  const login = useCallback(async (email, password) => {
    const data = await authApi.login(email, password);
    setUser({ email });
    setAccessToken(data.access_token);
    setRefreshToken(data.refresh_token);
    return data;
  }, []);

  const register = useCallback(async (email, password) => {
    return authApi.register(email, password);
  }, []);

  const logout = useCallback(() => {
    setUser(null);
    setAccessToken(null);
    setRefreshToken(null);
  }, []);

  const refreshSession = useCallback(async () => {
    if (!refreshToken) throw new Error('No refresh token available.');
    const data = await authApi.refresh(refreshToken);
    setAccessToken(data.access_token);
    setRefreshToken(data.refresh_token);
    return data;
  }, [refreshToken]);

  // ── Context value ──────────────────────────────────────────────────────────

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated,
        login,
        register,
        logout,
        refreshSession,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}
