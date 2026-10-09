import AppSidebar from "@/components/sidebar";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { Outlet } from "react-router-dom";
import { lazy, Suspense } from "react";

// The guide's rich Markdown renderer is useful only after the user opens it.
const ProductGuide = lazy(() => import("@/components/product-guide"));

const AppLayout = () => {
  return (
    <SidebarProvider
      style={
        {
          "--sidebar-width": "23rem",
          "--sidebar-width-mobile": "23rem",
        } as React.CSSProperties
      }
    >
      <AppSidebar />
      <SidebarInset className="h-full overflow-hidden bg-background text-foreground">
        <Outlet />
      </SidebarInset>
      <Suspense fallback={null}>
        <ProductGuide />
      </Suspense>
    </SidebarProvider>
  );
};

export default AppLayout;
