import { getUserSessions } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";
import { Link, useLocation } from "react-router-dom";
import { SidebarGroup, SidebarGroupContent, SidebarGroupLabel, SidebarMenu, SidebarMenuButton, SidebarMenuItem, SidebarMenuSkeleton } from "../ui/sidebar";
import { cn } from "@/lib/utils";

const ChatSessions = () => {
     const { pathname } = useLocation();
  const { data, isPending } = useQuery({
    queryKey: ["user-sessions"],
    queryFn: getUserSessions,
  });

 const sessions  = data?.sessions ?? []
  return (
    <SidebarGroup>
      <SidebarGroupLabel className="px-2 text-sm text-muted-foreground">
        Sessions
      </SidebarGroupLabel>
      <SidebarGroupContent>
        {isPending ? (
            <SidebarMenu className="gap-1">
                {Array.from({length:6}).map((_,index) => (
                    <SidebarMenuItem key={index}>
                        <SidebarMenuSkeleton />
                    </SidebarMenuItem>
                ))}
            </SidebarMenu>
        ):sessions.length === 0 ? (
            <div className="px-3 py-6 text-sm text-sidebar-foreground/60">
                No session yet</div>
        ):(
            <SidebarMenu className="gap-1">
                {sessions.map((session) => {
                    const isActive =  pathname === `/session/${session.slugId}`
                    return (
                    <SidebarMenuItem key={session._id}>
                        <SidebarMenuButton asChild
                        isActive={isActive}
                        className={cn(
                    "h-auto items-start px-2 hover:bg-sidebar-accent/20",
                    isActive ? "bg-sidebar-accent/30!" : "bg-transparent",
                    "pr-12"
                  )}
                        >
                            <Link to={`/session/${session.slugId}`}>
                              <div className="min-w-0 flex-1">
                      <div className="flex items-start justify-between gap-3">
                        <span className="block truncate text-sm font-medium">
                          {session.title}
                        </span>
                      </div>
                      <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                        {session.repoName}
                      </span>
                    </div>
                            </Link>
                        </SidebarMenuButton>
                    </SidebarMenuItem>
                )
                })}

            </SidebarMenu>
        )}
      </SidebarGroupContent>
    </SidebarGroup>
  )
}

export default ChatSessions
