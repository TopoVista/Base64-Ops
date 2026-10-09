import { Navigate, useParams } from "react-router-dom";
import { createElement, lazy } from "react";

// Pages contain editor, diagram, markdown, and Clerk-aware UI. Load every
// route on demand so one surface does not force its tooling into another.
const SignInPage = lazy(() => import("@/pages/auth/sign-in"));
const SignUpPage = lazy(() => import("@/pages/auth/sign-up"));
const HomePage = lazy(() => import("@/pages/home"));
const DemoPage = lazy(() => import("@/pages/demo"));
const GuidePage = lazy(() => import("@/pages/guide"));
const BillingPage = lazy(() => import("@/pages/billing"));
const SessionPage = lazy(() => import("@/pages/session"));
const DevRunInspector = lazy(() => import("@/pages/dev/DevRunInspector"));

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
  SESSION_WORKSPACE: "/session/:slugid/:section",
  DEV_RUN_INSPECTOR: "/dev/runs/:runId",
  BILLING: "/billing",
};

const SessionRedirect = () => {
  const { slugid } = useParams();
  return createElement(Navigate, { replace: true, to: `/session/${slugid ?? ""}/overview` });
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
    element: SessionRedirect,
  },
  {
    path: PROTECTED_ROUTES.SESSION_WORKSPACE,
    element: SessionPage,
  },
  {
    path: PROTECTED_ROUTES.DEV_RUN_INSPECTOR,
    element: DevRunInspector,
  },
  {
    path: PROTECTED_ROUTES.BILLING,
    element: BillingPage,
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
