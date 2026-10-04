import { createContext, useContext, useEffect, useMemo, useState, type PropsWithChildren } from 'react';

import { ApiRequestError, clearToken, getToken, setToken as persistToken } from '@/api/client';
import { fetchMe, login as loginRequest } from '@/api/endpoints';
import { withOfflineCopy } from '@/offline';
import { registerForPush, unregisterForPush } from '@/push';
import type { Me } from '@/types';

interface AuthContextValue {
  me: Me | null;
  isLoading: boolean;
  signIn: (username: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/**
 * Whether someone's signed in lives here, once, at the root — everything
 * under (app)/_layout.tsx's Stack.Protected reads it from useSession()
 * instead of each screen managing its own copy. me itself (name, role,
 * country) came back on the same /api/login/ call that got the token —
 * there's no separate profile fetch needed just to land on the app.
 */
export function AuthProvider({ children }: PropsWithChildren) {
  const [me, setMe] = useState<Me | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    restoreSession();

    // A token surviving from a previous launch, checked against /api/me/
    // rather than trusted outright — the device could have been offline
    // long enough for the account to be deactivated or the token reset,
    // and there's no separate "is this still valid" endpoint, so this is
    // also how a technician's own sign-in gets them straight into the app
    // on every later launch instead of typing a password in every time.
    async function restoreSession() {
      const token = await getToken();
      if (!token) {
        setIsLoading(false);
        return;
      }
      try {
        // With no signal, the last-known profile keeps them signed in (a
        // report can be filed offline); only the server actually refusing
        // the token signs them out.
        const { data, offline } = await withOfflineCopy('me', fetchMe);
        setMe(data);
        if (!offline) {
          registerForPush();
        }
      } catch (err) {
        if (err instanceof ApiRequestError) {
          await clearToken();
        }
      } finally {
        setIsLoading(false);
      }
    }
  }, []);

  const signIn = async (username: string, password: string) => {
    const response = await loginRequest(username, password);
    await persistToken(response.token);
    const { token: _token, ...profile } = response;
    setMe(profile);
    registerForPush();
  };

  const signOut = async () => {
    await unregisterForPush();
    await clearToken();
    setMe(null);
  };

  const value = useMemo(() => ({ me, isLoading, signIn, signOut }), [me, isLoading]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useSession() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useSession must be used within an AuthProvider');
  }
  return context;
}
