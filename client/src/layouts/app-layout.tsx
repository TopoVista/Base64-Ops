import AppSidebar from "@/components/sidebar";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { Outlet } from "react-router-dom";

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
      <SidebarInset className="h-full overflow-hidden bg-zinc-950 text-foreground">
        <Outlet />
      </SidebarInset>
    </SidebarProvider>
  );
};

export default AppLayout;
