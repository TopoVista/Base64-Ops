import { useQuery } from "@tanstack/react-query";
import { useAuth } from "@clerk/clerk-react";
import { syncClerkUser } from "@/lib/api";
import { useClerkApiToken } from "@/components/clerk-token-provider";

/**
 * Fetches the backend user record (githubConnected, etc.) after Clerk
 * confirms the session. The Clerk user object itself is available via
 * `useUser` from @clerk/clerk-react if you need name/email/avatar directly.
 */
export const useBackendUser = () => {
  const { isSignedIn, isLoaded, userId } = useAuth();
  const { isReady: isTokenReady, hasToken } = useClerkApiToken();

  return useQuery({
    // A browser can switch Clerk accounts without a full page reload. Keeping
    // this cache identity user-scoped prevents one account's backend profile
    // (including GitHub connection state) being shown for another account.
    queryKey: ["backend-user", userId],
    queryFn: syncClerkUser,
    // The Axios interceptor obtains a fresh Clerk token at request time. A
    // one-time token probe must not permanently suppress backend/session
    // loading when Clerk refreshes a token moments after the app mounts.
    enabled: isLoaded && !!isSignedIn && isTokenReady && hasToken,
    retry: false,
    staleTime: 1000 * 60 * 5,
  });
};
