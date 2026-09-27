import axios, { AxiosError } from "axios";
import { BASE_API_URL } from "./env";

interface CustomError extends AxiosError {
  errorCode?: string;
}

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
let _getToken: (() => Promise<string | null>) | null = null;

export const setTokenGetter = (fn: () => Promise<string | null>) => {
  _getToken = fn;
};

/** Returns a Clerk token for Fetch-based requests such as the SSE chat stream. */
export const getAccessToken = async (): Promise<string | null> => {
  return _getToken ? _getToken() : null;
};

API.interceptors.request.use(async (config) => {
  const token = await getAccessToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

API.interceptors.response.use(
  (response) => response,
  async (error) => {
    const data = error.response?.data;
    const apiError = error as CustomError;
    apiError.errorCode = data?.errorCode || "UNKNOWN_ERROR";
    if (data?.detail || data?.message) {
      apiError.message = data.detail || data.message;
    }
    return Promise.reject(apiError);
  }
);

export default API;
