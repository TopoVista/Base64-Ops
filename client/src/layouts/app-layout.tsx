import AppSidebar from "@/components/sidebar";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { Outlet } from "react-router-dom";
import ProductGuide from "@/components/product-guide";

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
      <ProductGuide />
    </SidebarProvider>
  );
};

export default AppLayout;
