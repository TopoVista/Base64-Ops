/**
 * ClerkTokenProvider
 *
 * Mounts inside ClerkProvider and wires the Clerk `getToken` function into
 * the Axios client so every API request automatically gets a fresh Bearer
 * token without any component needing to manage it manually.
 */
import { createContext, useContext, useEffect, useState } from "react";
import { useAuth } from "@clerk/clerk-react";
import { setTokenGetter } from "@/lib/axios-client";

type ClerkApiTokenState = {
  isReady: boolean;
  hasToken: boolean;
};

const ClerkApiTokenContext = createContext<ClerkApiTokenState>({
  isReady: false,
  hasToken: false,
});

export const useClerkApiToken = () => useContext(ClerkApiTokenContext);

const ClerkTokenProvider = ({ children }: { children: React.ReactNode }) => {
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const [tokenState, setTokenState] = useState<ClerkApiTokenState>({
    isReady: false,
    hasToken: false,
  });

  useEffect(() => {
    let active = true;
    if (!isLoaded || !isSignedIn) {
      setTokenGetter(async () => null);
      setTokenState({ isReady: isLoaded, hasToken: false });
      return () => { active = false; };
    }

    setTokenState({ isReady: false, hasToken: false });
    void getToken().then((token) => {
      if (!active) return;
      setTokenGetter(() => getToken());
      setTokenState({ isReady: true, hasToken: Boolean(token) });
    }).catch(() => {
      if (!active) return;
      setTokenGetter(async () => null);
      setTokenState({ isReady: true, hasToken: false });
    });
    return () => { active = false; };
  }, [getToken, isLoaded, isSignedIn]);

  return <ClerkApiTokenContext.Provider value={tokenState}>{children}</ClerkApiTokenContext.Provider>;
};

export default ClerkTokenProvider;
