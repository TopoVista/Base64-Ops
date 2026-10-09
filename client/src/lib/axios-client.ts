import axios, { AxiosError, type InternalAxiosRequestConfig } from "axios";
import { BASE_API_URL } from "./env";

interface CustomError extends AxiosError {
  errorCode?: string;
}

type RetriableGetConfig = InternalAxiosRequestConfig & {
  /** A fresh Clerk token may be used once after an otherwise-idempotent 401. */
  _base64FreshToken?: string;
  _base64AuthRetried?: boolean;
};

const options = {
  baseURL: BASE_API_URL,
  withCredentials: false, // Clerk uses Bearer tokens, not cookies
  timeout: 10000,
};

const API = axios.create(options);

/**
 * Inject a Clerk session token into every request.
 * Call this once from a component that has access to the Clerk hook,
 * or use the `getToken` helper exported below.
 */
type TokenOptions = { skipCache?: boolean };

let _getToken: ((options?: TokenOptions) => Promise<string | null>) | null = null;

export const setTokenGetter = (fn: (options?: TokenOptions) => Promise<string | null>) => {
  _getToken = fn;
};

/** Returns a Clerk token for Fetch-based requests such as the SSE chat stream. */
export const getAccessToken = async (options?: TokenOptions): Promise<string | null> => {
  return _getToken ? _getToken(options) : null;
};

API.interceptors.request.use(async (config) => {
  const token = (config as RetriableGetConfig)._base64FreshToken ?? await getAccessToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

API.interceptors.response.use(
  (response) => response,
  async (error) => {
    const request = error.config as RetriableGetConfig | undefined;
    const responseDetail = typeof error.response?.data?.detail === "string"
      ? error.response.data.detail.toLowerCase()
      : "";
    const clerkAuthFailure = /authorization header|invalid authentication token|token has expired/.test(responseDetail);
    // Only retry idempotent reads. A renewed Clerk token commonly becomes
    // available just after an OAuth return; retrying a write could duplicate a
    // repository mutation, so it is intentionally excluded.
    if (
      error.response?.status === 401 &&
      clerkAuthFailure &&
      request?.method?.toLowerCase() === "get" &&
      !request._base64AuthRetried
    ) {
      const freshToken = await getAccessToken({ skipCache: true });
      if (freshToken) {
        request._base64AuthRetried = true;
        request._base64FreshToken = freshToken;
        return API.request(request);
      }
    }
    const data = error.response?.data;
    const apiError = error as CustomError;
    apiError.errorCode = data?.errorCode || "UNKNOWN_ERROR";
    if (error.response?.status === 401 && clerkAuthFailure) {
      apiError.message = "Your Base64 sign-in session could not be refreshed. Reload the page and sign in again.";
    } else if (data?.detail || data?.message) {
      apiError.message = data.detail || data.message;
    } else if (error.code === "ECONNABORTED") {
      apiError.message = "The Base64 backend took too long to respond. Check the backend and MongoDB connection, then retry.";
    } else if (error.message === "Network Error") {
      const backendUrl = BASE_API_URL?.replace(/\/api\/?$/, "") ?? "the configured backend";
      apiError.message = `Cannot reach the Base64 backend at ${backendUrl}. Confirm it is running and that the local network/DNS connection is available.`;
    }
    return Promise.reject(apiError);
  }
);

export default API;
