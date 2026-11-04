/**
 * API base. Production: same-origin (nginx / App Service proxies /api → backend).
 * Local Vite DEV: '' so requests hit /api and the Vite proxy → 127.0.0.1:5001.
 *
 * Never bake loopback (127.0.0.1 / localhost) into a production browser bundle —
 * that makes the user's PC the API host after deploy and breaks Sales Overview.
 */
function isLoopbackUrl(url: string): boolean {
  return /^(https?:\/\/)?(127\.0\.0\.1|localhost)(:\d+)?\/?$/i.test(url.trim());
}

function apiBase(): string {
  // Browser + Vite dev → same-origin /api (proxied to local uvicorn)
  if (import.meta.env.DEV && typeof window !== 'undefined') {
    const h = window.location.hostname;
    if (h === 'localhost' || h === '127.0.0.1') {
      return '';
    }
  }

  const env = import.meta.env.VITE_API_URL;
  if (typeof env === 'string' && env.trim() !== '') {
    const cleaned = env.trim().replace(/\/$/, '');
    // Production build must not call the visitor's localhost
    if (!import.meta.env.DEV && isLoopbackUrl(cleaned)) {
      if (typeof window !== 'undefined' && window.location?.origin) {
        return window.location.origin.replace(/\/$/, '');
      }
      return '';
    }
    return cleaned;
  }

  if (typeof window !== 'undefined' && window.location?.origin) {
    return window.location.origin.replace(/\/$/, '');
  }
  return '';
}

export const API_URL = `${apiBase()}/api`.replace(/([^:]\/)\/+/g, '$1');

/** Read fetch body as JSON with a clear error when the server returned HTML/proxy junk. */
export async function readApiJson<T = unknown>(res: Response): Promise<T> {
  const text = await res.text();
  const trimmed = text.trim();
  if (!trimmed) {
    throw new Error(`Empty response from API (${res.status})`);
  }
  if (trimmed.startsWith('<!') || trimmed.startsWith('<html') || trimmed.startsWith('<HTML')) {
    if (res.status === 502 || res.status === 503 || res.status === 504) {
      throw new Error(
        `Server busy or restarting (HTTP ${res.status}). Please retry — heavy filters (season + zone) can take a moment.`
      );
    }
    throw new Error(
      `API returned HTML instead of JSON (${res.status}). Backend /api proxy is missing or VITE_API_URL is wrong.`
    );
  }
  try {
    return JSON.parse(trimmed) as T;
  } catch (err) {
    const preview = trimmed.slice(0, 80).replace(/\s+/g, ' ');
    const why = err instanceof Error ? err.message : 'Invalid JSON';
    throw new Error(`${why} — body starts: ${preview}`);
  }
}
