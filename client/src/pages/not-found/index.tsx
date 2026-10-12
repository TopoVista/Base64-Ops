import { Home, SearchX } from "lucide-react";
import { Link } from "react-router-dom";

const NotFoundPage = () => (
  <main className="grid min-h-screen place-items-center bg-background p-6 text-foreground">
    <section className="w-full max-w-lg rounded-2xl border border-border bg-card p-7 shadow-sm">
      <div className="flex size-11 items-center justify-center rounded-xl bg-muted text-muted-foreground">
        <SearchX className="size-5" aria-hidden="true" />
      </div>
      <p className="mt-5 text-sm font-medium text-muted-foreground">Page unavailable</p>
      <h1 className="mt-1 text-2xl font-semibold tracking-tight">We could not find that page</h1>
      <p className="mt-3 text-sm leading-6 text-muted-foreground">
        Check the address, or return to Base64 Ops to select a workspace.
      </p>
      <Link
        to="/new"
        className="mt-6 inline-flex items-center gap-2 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90"
      >
        <Home className="size-4" aria-hidden="true" />
        Return to workspace
      </Link>
    </section>
  </main>
);

export default NotFoundPage;
