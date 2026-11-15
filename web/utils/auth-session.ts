import { STORAGE_USERINFO_KEY } from '@/utils/constants/index';
import type { AxiosError, AxiosInstance, AxiosRequestConfig } from 'axios';

type AuthRetryConfig = AxiosRequestConfig & { _authRetried?: boolean };

let refreshRequest: Promise<boolean> | null = null;
let expiredRedirectStarted = false;
const axiosInstances = new WeakSet<AxiosInstance>();
const fetchWindows = new WeakSet<Window>();

function hasStoredUser(): boolean {
  try {
    return Boolean(window.localStorage.getItem(STORAGE_USERINFO_KEY));
  } catch {
    return false;
  }
}

function isProtectedApiUrl(value: string, base?: string): boolean {
  if (typeof window === 'undefined') return false;

  try {
    const url = new URL(value, base || window.location.origin);
    if (url.origin !== window.location.origin || !url.pathname.startsWith('/api/')) return false;
    if (url.pathname === '/api/health' || url.pathname.startsWith('/api/v1/chat/share/')) return false;
    if (url.pathname === '/api/v1/auth/me') return true;
    if (url.pathname.startsWith('/api/v1/auth/')) return false;
    return true;
  } catch {
    return false;
  }
}

export function isAuthExpiredResponse(status?: number): boolean {
  return status === 401 && expiredRedirectStarted;
}

function redirectToLoginAfterExpiry(): void {
  if (expiredRedirectStarted || !hasStoredUser()) return;
  expiredRedirectStarted = true;

  try {
    window.localStorage.removeItem(STORAGE_USERINFO_KEY);
  } catch {
    // The login redirect still works when local storage is unavailable.
  }

  const returnTo = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  const loginUrl = new URL('/login', window.location.origin);
  loginUrl.searchParams.set('return_to', returnTo);
  loginUrl.searchParams.set('expired', '1');
  window.location.replace(`${loginUrl.pathname}${loginUrl.search}`);
}

async function refreshSession(): Promise<boolean> {
  if (refreshRequest) return refreshRequest;

  const requestRefresh = () =>
    window.fetch('/api/v1/auth/refresh', {
      method: 'POST',
      credentials: 'same-origin',
    });
  refreshRequest = requestRefresh()
    .then(async response => {
      if (response.ok) return true;
      // Another tab may have rotated the shared refresh cookie while this
      // request was in flight. Retry once with the browser's newest cookie.
      if (response.status === 401) {
        return (await requestRefresh()).ok;
      }
      return false;
    })
    .catch(() => false)
    .finally(() => {
      refreshRequest = null;
    });

  return refreshRequest;
}

/** Try a one-time cookie refresh after an authenticated API request expires. */
export async function recoverExpiredSession(): Promise<boolean> {
  if (!hasStoredUser()) return false;

  if (await refreshSession()) {
    expiredRedirectStarted = false;
    return true;
  }

  redirectToLoginAfterExpiry();
  return false;
}

/** Call after a successful interactive login so a later expiry can redirect again. */
export function resetAuthExpiryHandling(): void {
  expiredRedirectStarted = false;
}

export function isAuthExpiryRedirecting(): boolean {
  return expiredRedirectStarted;
}

export function installFetchAuthRetry(): void {
  if (typeof window === 'undefined' || fetchWindows.has(window)) return;
  fetchWindows.add(window);

  const originalFetch = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const request = new Request(input instanceof URL ? input.toString() : input, init);
    const response = await originalFetch(request.clone());
    if (response.status !== 401 || !isProtectedApiUrl(request.url)) return response;

    if (await recoverExpiredSession()) {
      const retriedResponse = await originalFetch(request.clone());
      if (retriedResponse.status === 401) redirectToLoginAfterExpiry();
      return retriedResponse;
    }
    return response;
  };
}

export function installAxiosAuthRetry(instance: AxiosInstance): void {
  if (axiosInstances.has(instance)) return;
  axiosInstances.add(instance);

  instance.interceptors.response.use(
    response => response,
    async (error: AxiosError) => {
      const config = error.config as AuthRetryConfig | undefined;
      if (typeof window === 'undefined') throw error;
      const base = config?.baseURL || window.location.origin;
      const requestUrl = config?.url;
      const isAuthFailure =
        error.response?.status === 401 && typeof requestUrl === 'string' && isProtectedApiUrl(requestUrl, base);
      if (isAuthFailure && config?._authRetried) {
        redirectToLoginAfterExpiry();
        throw error;
      }
      if (!config || !isAuthFailure) {
        throw error;
      }

      config._authRetried = true;
      if (await recoverExpiredSession()) {
        return instance.request(config);
      }
      throw error;
    },
  );
}
