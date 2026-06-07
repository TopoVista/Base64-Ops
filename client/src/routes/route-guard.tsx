import { Navigate, Outlet, useLocation } from "react-router-dom";
import { isAuthRoute, PROTECTED_ROUTES } from "./route";
import { useUser } from "@/hooks/use-user";
import Logo from "@/components/logo";
import { Spinner } from "@/components/ui/spinner";

type RouteGuardProps = {
  requireAuth: boolean;
};

const RouteGuard = ({ requireAuth }: RouteGuardProps) => {
  const location = useLocation();

  const {data,isLoading} = useUser();

  const _isAuthRoute = isAuthRoute(location.pathname);

 if (isLoading && !_isAuthRoute) {
    return <div className="min-h-screen flex flex-col items-center justify-center gap-3">
      <Logo />
      <Spinner className="size-8" />
    </div>;
  }

  const isAuth =  Boolean(data?.user);
 if (requireAuth && !isAuth) {
    return <Navigate to="/" replace state={{ from: location }} />;
  }

  if (!requireAuth && isAuth) {
    return <Navigate to={PROTECTED_ROUTES.NEW} replace />;
  }

  return <Outlet />;
};


export default RouteGuard;