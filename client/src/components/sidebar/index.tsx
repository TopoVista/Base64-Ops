import { Sidebar, SidebarContent, SidebarFooter, SidebarHeader, SidebarTrigger, useSidebar } from '../ui/sidebar'
import Logo from '../logo'
import NavItems from './navitems'
import ChatSessions from './chat-sessions'
import { useUser } from '@/hooks/use-user'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { logoutMutationFn } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Avatar, AvatarFallback, AvatarImage } from '../ui/avatar'
import { Button } from '../ui/button'
import { LogOut } from 'lucide-react'
import { ModeToggle } from '../mode-toggle'
import { useState } from 'react'
import SessionSearchDialog from './session-search-dialog'

const AppSidebar = () => {
    const {state} = useSidebar()
    const {data} = useUser();
    const queryClient = useQueryClient();
    const navigate = useNavigate();
      const [searchOpen, setSearchOpen] = useState(false);

  const logoutMutation = useMutation({
    mutationFn: logoutMutationFn,
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: ["current-user"] });
      queryClient.removeQueries({ queryKey: ["user-sessions"] });
      queryClient.removeQueries({ queryKey: ["github-repos"] });
      navigate("/");
    },
  });

  const user = data?.user;
  console.log(user,"user")
  const initials =
    user?.name
      ?.split(" ")
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase())
      .join("") ?? "U";
  return (
    <Sidebar collapsible='icon' className='border-r border-sidebar-border'>
       <SidebarHeader className=" w-full mb-2 flex flex-row items-center justify-between 
      py-3! pb-0! pl-3">
        <Logo showText={state === "expanded" } />
        <SidebarTrigger className="hidden lg:flex -m-2 mb-0" />
      </SidebarHeader>

        <SidebarContent className='flex gap-0 px-0 pb-3'>
          <NavItems  onSearchClick={() => setSearchOpen(true)}/>
          {state === "expanded" && <ChatSessions />}
        </SidebarContent>
      <SidebarFooter className="border-t border-sidebar-border p-2">
        {user ? (
          <div
            className={cn(
              "flex items-center gap-3 rounded-lg px-2 py-2",
              state === "collapsed" && "justify-center px-0 flex-col"
            )}
          >
            <Avatar className="size-9">
              <AvatarImage src={user.avatar ?? undefined} alt={user.name} />
              <AvatarFallback>{initials}</AvatarFallback>
            </Avatar>
            {state === "expanded" ? (
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-sidebar-foreground">
                  {user.name}
                </p>
                <p className="truncate text-xs text-sidebar-foreground/60">
                  {user.email}
                </p>
              </div>
            ) : null}
            <div className={cn("flex items-center gap-1",
              state === "collapsed" && "flex-col"
            )}>
              <ModeToggle />
              <Button
                type="button"
                size="icon"
                variant="ghost"
                className="shrink-0 text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
                onClick={() => logoutMutation.mutate()}
                disabled={logoutMutation.isPending}
              >
                <LogOut className="size-4" />
              </Button>
            </div>
          </div>
        ) : null}
      </SidebarFooter>
        <SessionSearchDialog open={searchOpen} onOpenChange={setSearchOpen} />
    </Sidebar>
  )
}

export default AppSidebar
