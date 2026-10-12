import { AlertTriangle, Home, RefreshCw } from "lucide-react";
import { isRouteErrorResponse, Link, useRouteError } from "react-router-dom";

/**
 * User-facing route failure surface. Never render an exception message or
 * stack trace here: those can disclose implementation details or request data.
 */
export default function RouteErrorBoundary() {
  const error = useRouteError();
  const status = isRouteErrorResponse(error) ? error.status : undefined;
  const missing = status === 404;

  return (
    <main className="grid min-h-screen place-items-center bg-background p-6 text-foreground">
      <section className="w-full max-w-lg rounded-2xl border border-border bg-card p-7 shadow-sm">
        <div className="flex size-11 items-center justify-center rounded-xl bg-destructive/10 text-destructive">
          <AlertTriangle className="size-5" aria-hidden="true" />
        </div>
        <p className="mt-5 text-sm font-medium text-muted-foreground">
          {missing ? "Page unavailable" : "Base64 Ops needs to recover"}
        </p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight">
          {missing ? "This page could not be found" : "Something went wrong while loading this page"}
        </h1>
        <p className="mt-3 text-sm leading-6 text-muted-foreground">
          {missing
            ? "The address may be outdated, or the item may no longer be available to this account."
            : "Your saved sessions and approvals were not changed. Try again, or return to the workspace."}
        </p>
        <div className="mt-6 flex flex-wrap gap-3">
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90"
          >
            <RefreshCw className="size-4" aria-hidden="true" />
            Retry page
          </button>
          <Link
            to="/new"
            className="inline-flex items-center gap-2 rounded-md border border-border px-3 py-2 text-sm font-medium text-foreground hover:bg-muted"
          >
            <Home className="size-4" aria-hidden="true" />
            Return to workspace
          </Link>
        </div>
        {status && !missing ? <p className="mt-5 text-xs text-muted-foreground">Reference: HTTP {status}</p> : null}
      </section>
    </main>
  );
}
