import BaseLayout from "@/layouts/base-layout";
import {
  Route,
  createBrowserRouter,
  createRoutesFromElements,
  ScrollRestoration,
  Outlet,
} from "react-router-dom";
import { authRouthsPaths, protectedRoutesPaths } from "./route";
import AppLayout from "@/layouts/app-layout";
import NotFoundPage from "@/pages/not-found";
import RouteGuard from "./route-guard";

const RootLayout = () => (
  <>
    <ScrollRestoration />
    <Outlet />
  </>
);

export const router = createBrowserRouter(
  createRoutesFromElements(
    <Route element={<RootLayout />}>
      <Route element={<RouteGuard requireAuth={false}/>}>
        <Route element={<BaseLayout />}>
          {authRouthsPaths.map(({ path, element: Element }) => (
            <Route key={path} path={path} element={<Element />} />
          ))}
        </Route>
      </Route>

      <Route element={<RouteGuard requireAuth={true}/>}>
        <Route element={<AppLayout />}>
          {protectedRoutesPaths.map(({ path, element: Element }) => (
            <Route key={path} path={path} element={<Element />} />
          ))}
        </Route>
      </Route>

      <Route path="*" element={<NotFoundPage />}/>
    </Route>
  ),
);
