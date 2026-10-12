import type { Location } from "react-router-dom";

const DEFAULT_POST_AUTH_PATH = "/new";

type PriorLocation = {
  pathname?: unknown;
  search?: unknown;
  hash?: unknown;
};

function isAllowedPostAuthPath(value: string): boolean {
  if (
    !value.startsWith("/") ||
    value.startsWith("//") ||
    value.includes("\\") ||
    value.includes("\u0000")
  ) {
    return false;
  }

  const pathname = value.split(/[?#]/, 1)[0];
  return (
    pathname === "/new" ||
    pathname === "/billing" ||
    pathname.startsWith("/session/") ||
    pathname.startsWith("/dev/runs/")
  );
}

function fromLocationState(state: unknown): string | null {
  if (!state || typeof state !== "object" || !("from" in state)) return null;
  const from = (state as { from?: unknown }).from;
  if (!from || typeof from !== "object") return null;

  const prior = from as PriorLocation;
  if (typeof prior.pathname !== "string") return null;
  const search = typeof prior.search === "string" ? prior.search : "";
  const hash = typeof prior.hash === "string" ? prior.hash : "";
  const candidate = `${prior.pathname}${search}${hash}`;
  return isAllowedPostAuthPath(candidate) ? candidate : null;
}

/**
 * Preserve a bookmarked protected route across Clerk authentication without
 * trusting a browser-supplied external redirect destination.
 */
export function getPostAuthRedirect(location: Pick<Location, "search" | "state">): string {
  const fromState = fromLocationState(location.state);
  if (fromState) return fromState;

  const fromQuery = new URLSearchParams(location.search).get("redirect");
  return fromQuery && isAllowedPostAuthPath(fromQuery)
    ? fromQuery
    : DEFAULT_POST_AUTH_PATH;
}

export function postAuthRedirectQuery(path: string): string {
  return `?redirect=${encodeURIComponent(path)}`;
}
