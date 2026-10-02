const configuredApiUrl = import.meta.env.VITE_BASE_API_URL;

/**
 * Keep the local Vite origin and API origin on the same loopback hostname.
 * Windows treats `localhost` and `127.0.0.1` as separate browser origins. A
 * developer who opens Vite at 127.0.0.1 should therefore not have a frontend
 * configured to call localhost (and vice versa), even though both resolve to
 * the same machine. This avoids opaque browser-level "Network Error" reports.
 */
const resolveLocalApiUrl = (value: string | undefined) => {
  if (!value || typeof window === "undefined") return value;

  try {
    const url = new URL(value);
    const isLoopbackApi = url.hostname === "localhost" || url.hostname === "127.0.0.1";
    const isLoopbackPage = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1";

    if (isLoopbackApi && isLoopbackPage) {
      url.hostname = window.location.hostname;
    }
    return url.toString();
  } catch {
    // Preserve a deployment-provided URL verbatim. Axios will surface a
    // concrete configuration error rather than this helper changing it.
    return value;
  }
};

export const BASE_API_URL = resolveLocalApiUrl(configuredApiUrl);
export const CLERK_PUBLISHABLE_KEY = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY;
