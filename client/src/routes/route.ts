import SignInPage from "@/pages/auth/sign-in";
import SignUpPage from "@/pages/auth/sign-up";
import HomePage from "@/pages/home";
import SessionPage from "@/pages/session";

export const isAuthRoute = (pathname: string): boolean => {
  return Object.values(AUTH_ROUTES).includes(pathname);
};


export const AUTH_ROUTES = {
  SIGN_IN: '/',
  SIGN_UP: '/sign-up',
};


export const PROTECTED_ROUTES = {
  NEW: '/new',
  SINGLE_SESSION: '/session/:slugid',
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
];