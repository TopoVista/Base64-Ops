/**
 * ClerkTokenProvider
 *
 * Mounts inside ClerkProvider and wires the Clerk `getToken` function into
 * the Axios client so every API request automatically gets a fresh Bearer
 * token without any component needing to manage it manually.
 */
import { useEffect } from "react";
import { useAuth } from "@clerk/clerk-react";
import { setTokenGetter } from "@/lib/axios-client";

const ClerkTokenProvider = ({ children }: { children: React.ReactNode }) => {
  const { getToken } = useAuth();

  useEffect(() => {
    setTokenGetter(() => getToken());
  }, [getToken]);

  return <>{children}</>;
};

export default ClerkTokenProvider;
