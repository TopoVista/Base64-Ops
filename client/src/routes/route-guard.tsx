import { Navigate, Outlet, useLocation } from "react-router-dom";
import { isAuthRoute } from "./route";
import { useAuth } from "@clerk/clerk-react";
import Logo from "@/components/logo";
import { Spinner } from "@/components/ui/spinner";
import { getPostAuthRedirect } from "@/lib/auth-redirect";

type RouteGuardProps = {
  requireAuth: boolean;
};

const RouteGuard = ({ requireAuth }: RouteGuardProps) => {
  const location = useLocation();
  const { isLoaded, isSignedIn } = useAuth();
  const _isAuthRoute = isAuthRoute(location.pathname);

  // Show loading spinner on protected routes while Clerk initialises
  if (!isLoaded && !_isAuthRoute) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-3">
        <Logo />
        <Spinner className="size-8" />
      </div>
    );
  }

  if (requireAuth && !isSignedIn) {
    return <Navigate to="/" replace state={{ from: location }} />;
  }

  if (!requireAuth && isSignedIn) {
    return <Navigate to={getPostAuthRedirect(location)} replace />;
  }

  return <Outlet />;
};

export default RouteGuard;
