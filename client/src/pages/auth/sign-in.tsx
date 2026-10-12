import { SignIn } from "@clerk/clerk-react";
import { useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import Logo from "@/components/logo";
import { getPostAuthRedirect, postAuthRedirectQuery } from "@/lib/auth-redirect";
import { getBackendHealth } from "@/lib/api";

const ServiceAvailabilityNotice = () => {
  const { data: health, isError } = useQuery({
    queryKey: ["backend-health"],
    queryFn: getBackendHealth,
    // This is shared with the workspace health badge, so a route transition
    // does not create a second request. A brief stale window keeps the sign-in
    // screen responsive while still surfacing an active service outage.
    staleTime: 30_000,
    retry: false,
  });

  const unavailable = isError || health?.status === "degraded";
  if (!unavailable) return null;

  const message = isError
    ? "We could not reach the Base64 Ops API. Sign-in may succeed, but workspace data cannot load until the service recovers."
    : health?.database === "unavailable"
      ? "Workspace persistence is temporarily unavailable. Sign-in is still available, but sessions, repositories, and approvals cannot be saved until the service recovers."
      : health?.message ?? "Base64 Ops is temporarily unavailable.";

  return (
    <div
      role="status"
      className="mb-4 flex gap-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-950 dark:text-amber-100"
    >
      <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <div>
        <p className="font-medium">Service availability notice</p>
        <p className="mt-1 leading-5 text-amber-900/90 dark:text-amber-100/80">{message}</p>
      </div>
    </div>
  );
};

const SignInPage = () => {
  const location = useLocation();
  const postAuthRedirect = getPostAuthRedirect(location);
  return (
    <div className="grid min-h-svh lg:grid-cols-2">
      <div className="min-w-0 flex flex-col justify-between gap-10 p-6 md:p-10">
        <Logo />
        <div className="mx-auto flex w-full max-w-sm flex-1 items-center justify-center">
          <div className="w-full">
            <ServiceAvailabilityNotice />
            <SignIn
              routing="hash"
              signUpUrl={`/sign-up${postAuthRedirectQuery(postAuthRedirect)}`}
              fallbackRedirectUrl={postAuthRedirect}
              appearance={{
                elements: {
                  card: "shadow-none border border-border rounded-xl",
                  headerTitle: "text-2xl font-semibold tracking-tight",
                  headerSubtitle: "text-sm text-muted-foreground",
                  formButtonPrimary:
                    "bg-primary text-primary-foreground hover:bg-primary/90",
                  footerActionLink: "text-primary font-medium",
                },
              }}
            />
          </div>
        </div>
      </div>
      <div className="relative hidden overflow-hidden border-l bg-gradient-to-br from-zinc-950 via-zinc-900 to-zinc-800 lg:block">
        <div className="absolute inset-0 opacity-50 [background-image:radial-gradient(circle_at_20%_20%,rgba(255,255,255,0.16),transparent_30%),radial-gradient(circle_at_80%_10%,rgba(255,255,255,0.12),transparent_22%),radial-gradient(circle_at_50%_80%,rgba(255,255,255,0.08),transparent_28%)]" />
        <div className="relative flex h-full flex-col justify-end p-10 text-white">
          <p className="max-w-md text-3xl font-semibold leading-tight">
            Connect GitHub, spin up a workspace, and let the agent build.
          </p>
          <p className="mt-4 max-w-md text-sm text-white/70">
            One session, one branch, one sandbox.
          </p>
        </div>
      </div>
    </div>
  );
};

export default SignInPage;
