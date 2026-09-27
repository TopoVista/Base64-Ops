import SignInPage from "@/pages/auth/sign-in";
import SignUpPage from "@/pages/auth/sign-up";
import HomePage from "@/pages/home";
import SessionPage from "@/pages/session";
import DevRunInspector from "@/pages/dev/DevRunInspector";
import DemoPage from "@/pages/demo";
import GuidePage from "@/pages/guide";

export const isAuthRoute = (pathname: string): boolean => {
  return Object.values(AUTH_ROUTES).includes(pathname);
};

export const AUTH_ROUTES = {
  SIGN_IN: "/",
  SIGN_UP: "/sign-up",
};

export const PROTECTED_ROUTES = {
  NEW: "/new",
  SINGLE_SESSION: "/session/:slugid",
  DEV_RUN_INSPECTOR: "/dev/runs/:runId",
};

export const PUBLIC_ROUTES = {
  DEMO: "/demo",
  GUIDE: "/guide",
};

export const authRouthsPaths = [
  {
    path: AUTH_ROUTES.SIGN_IN,
    element: SignInPage,
  },
  {
    path: AUTH_ROUTES.SIGN_UP,
    element: SignUpPage,
  },
];

export const protectedRoutesPaths = [
  {
    path: PROTECTED_ROUTES.NEW,
    element: HomePage,
  },
  {
    path: PROTECTED_ROUTES.SINGLE_SESSION,
    element: SessionPage,
  },
  {
    path: PROTECTED_ROUTES.DEV_RUN_INSPECTOR,
    element: DevRunInspector,
  },
];

// The fixture is static, mutation-disabled, and intentionally has no API data.
// Keeping it public makes the safety/demo story inspectable without credentials.
export const publicRoutesPaths = [
  {
    path: PUBLIC_ROUTES.DEMO,
    element: DemoPage,
  },
  {
    path: PUBLIC_ROUTES.GUIDE,
    element: GuidePage,
  },
];
