import { getUserSessions } from "@/lib/api";
import { useClerkApiToken } from "@/components/clerk-token-provider";
import { useAuth } from "@clerk/clerk-react";
import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { Link, useLocation } from "react-router-dom";
import type { SessionsResponse } from "@/types/session.type";
import { SidebarGroup, SidebarGroupContent, SidebarGroupLabel, SidebarMenu, SidebarMenuButton, SidebarMenuItem, SidebarMenuSkeleton } from "../ui/sidebar";
import { cn } from "@/lib/utils";

const sessionCacheKey = (userId: string) => `base64-ops:sessions:${userId}`;

const readCachedSessions = (userId: string | null | undefined): SessionsResponse | undefined => {
  if (!userId || typeof window === "undefined") return undefined;

  try {
    const cached = window.sessionStorage.getItem(sessionCacheKey(userId));
    if (!cached) return undefined;
    const parsed = JSON.parse(cached) as SessionsResponse;
    return Array.isArray(parsed.sessions) ? parsed : undefined;
  } catch {
    return undefined;
  }
};

const ChatSessions = () => {
  const { pathname } = useLocation();
  const { userId, isLoaded, isSignedIn } = useAuth();
  const { isReady: isTokenReady, hasToken } = useClerkApiToken();
  const { data, isPending, isError, error, refetch } = useQuery({
    // Sessions are tenant data. Tie the cache to Clerk's stable subject so a
    // local account switch cannot reuse another account's session list.
    queryKey: ["user-sessions", userId],
    queryFn: getUserSessions,
    enabled: isLoaded && Boolean(isSignedIn) && isTokenReady && hasToken,
    retry: false,
    // An outage must not make an account look empty after a refresh. This
    // cache is browser-session scoped and keyed by the Clerk subject, so it is
    // never reused across local account switches.
    initialData: () => readCachedSessions(userId),
  });

  const sessions = data?.sessions ?? [];
  useEffect(() => {
    if (!userId || !data || typeof window === "undefined") return;
    try {
      window.sessionStorage.setItem(sessionCacheKey(userId), JSON.stringify(data));
    } catch {
      // Session storage is only resilience UI; failure must not affect data.
    }
  }, [data, userId]);

  return (
    <SidebarGroup>
      <SidebarGroupLabel className="px-2 text-sm text-muted-foreground">
        Sessions
      </SidebarGroupLabel>
      <SidebarGroupContent>
        {!isLoaded || !isTokenReady || isPending ? (
            <SidebarMenu className="gap-1">
                {Array.from({length:6}).map((_,index) => (
                    <SidebarMenuItem key={index}>
                        <SidebarMenuSkeleton />
                    </SidebarMenuItem>
                ))}
            </SidebarMenu>
        ) : isError && sessions.length === 0 ? (
            <div className="space-y-2 px-3 py-4 text-sm text-sidebar-foreground/70" role="alert">
              <p>Saved sessions could not be loaded.</p>
              <p className="text-xs text-sidebar-foreground/55">{error instanceof Error ? error.message : "Try again when the backend connection is restored."}</p>
              <button type="button" className="text-xs font-medium text-primary hover:underline" onClick={() => void refetch()}>Retry</button>
            </div>
        ) : sessions.length === 0 ? (
            <div className="px-3 py-6 text-sm text-sidebar-foreground/60">
                No session yet</div>
        ):(
            <>
            {isError && (
              <div className="mx-2 mb-2 rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-xs text-sidebar-foreground/75" role="status">
                Showing saved sessions while Atlas reconnects. <button type="button" className="font-medium text-primary hover:underline" onClick={() => void refetch()}>Retry</button>
              </div>
            )}
            <SidebarMenu className="gap-1">
                {sessions.map((session) => {
                    const isActive =  pathname.startsWith(`/session/${session.slugId}`)
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
                            <Link to={`/session/${session.slugId}/overview`}>
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
            </>
        )}
      </SidebarGroupContent>
    </SidebarGroup>
  )
}

export default ChatSessions
