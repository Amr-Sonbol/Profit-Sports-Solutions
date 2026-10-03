import * as SecureStore from 'expo-secure-store';

const BASE_URL = process.env.EXPO_PUBLIC_API_BASE_URL ?? 'http://10.0.2.2:8000';
const TOKEN_KEY = 'auth_token';

// expo-secure-store has no real implementation on web (no OS keychain to
// back it) and throws outright rather than just returning null — worth
// catching everywhere, not just there, since a stored token is never
// worth crashing the whole app over if a read/write ever fails on some
// device for any other reason either. The practical effect on an actual
// failure is the same either way: treated as logged out.
export async function getToken(): Promise<string | null> {
  try {
    return await SecureStore.getItemAsync(TOKEN_KEY);
  } catch {
    return null;
  }
}

export async function setToken(token: string): Promise<void> {
  try {
    await SecureStore.setItemAsync(TOKEN_KEY, token);
  } catch {
    // Sign-in already succeeded server-side by the time this runs — not
    // being able to persist the token just means it won't survive a
    // restart, not that sign-in itself should appear to fail.
  }
}

export async function clearToken(): Promise<void> {
  try {
    await SecureStore.deleteItemAsync(TOKEN_KEY);
  } catch {
    // Already effectively "signed out" from the app's own perspective
    // either way — see signOut() in context/auth.tsx.
  }
}

export class ApiRequestError extends Error {
  status: number;
  body: unknown;

  constructor(status: number, body: unknown) {
    // DRF's own error shape is either {"detail": "..."} or per-field
    // validation errors ({"field": ["message"]}) — this covers both
    // without the caller needing to know which one they got.
    const detail =
      body && typeof body === 'object' && 'detail' in body
        ? String((body as { detail: unknown }).detail)
        : 'Something went wrong.';
    super(detail);
    this.status = status;
    this.body = body;
  }
}

interface RequestOptions {
  method?: 'GET' | 'POST';
  body?: unknown;
  /** For multipart uploads — pass a FormData body instead of JSON. */
  isFormData?: boolean;
}

/**
 * Every authenticated call to the Django API goes through here — same
 * token auth as the technician/staff mobile endpoints (api/views.py),
 * one place to attach the Authorization header and turn a non-2xx
 * response into a thrown error instead of every caller checking
 * response.ok by hand.
 */
export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const token = await getToken();
  const headers: Record<string, string> = {};
  if (token) {
    headers.Authorization = `Token ${token}`;
  }

  let body: BodyInit | undefined;
  if (options.body !== undefined) {
    if (options.isFormData) {
      body = options.body as FormData;
      // Deliberately no Content-Type here — fetch sets the multipart
      // boundary itself only when it builds the header from the FormData
      // object; setting it by hand strips that boundary.
    } else {
      headers['Content-Type'] = 'application/json';
      body = JSON.stringify(options.body);
    }
  }

  const response = await fetch(`${BASE_URL}${path}`, {
    method: options.method ?? 'GET',
    headers,
    body,
  });

  const isJson = response.headers.get('content-type')?.includes('application/json');
  const data = isJson ? await response.json() : null;

  if (!response.ok) {
    throw new ApiRequestError(response.status, data);
  }
  return data as T;
}
