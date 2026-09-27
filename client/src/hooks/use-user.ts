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
  const { isSignedIn, isLoaded } = useAuth();
  const { isReady: isTokenReady, hasToken } = useClerkApiToken();

  return useQuery({
    queryKey: ["backend-user"],
    queryFn: syncClerkUser,
    enabled: isLoaded && !!isSignedIn && isTokenReady && hasToken,
    retry: false,
    staleTime: 1000 * 60 * 5,
  });
};
