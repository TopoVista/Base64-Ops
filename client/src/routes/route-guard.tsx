import { Navigate, Outlet, useLocation } from "react-router-dom";
import { isAuthRoute, PROTECTED_ROUTES } from "./route";
import { useAuth } from "@clerk/clerk-react";
import Logo from "@/components/logo";
import { Spinner } from "@/components/ui/spinner";

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
    return <Navigate to={PROTECTED_ROUTES.NEW} replace />;
  }

  return <Outlet />;
};

export default RouteGuard;
