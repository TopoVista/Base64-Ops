import { Sidebar, SidebarContent, SidebarFooter, SidebarHeader, SidebarTrigger, useSidebar } from '../ui/sidebar'
import Logo from '../logo'
import NavItems from './navitems'
import ChatSessions from './chat-sessions'
import { useUser, useClerk } from '@clerk/clerk-react'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { cn } from '@/lib/utils'
import { Avatar, AvatarFallback, AvatarImage } from '../ui/avatar'
import { Button } from '../ui/button'
import { LogOut } from 'lucide-react'
import { ModeToggle } from '../mode-toggle'
import { useState } from 'react'
import SessionSearchDialog from './session-search-dialog'

const AppSidebar = () => {
  const { state } = useSidebar()
  const { user, isLoaded } = useUser()
  const { signOut } = useClerk()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [searchOpen, setSearchOpen] = useState(false)
  const [signingOut, setSigningOut] = useState(false)

  const handleSignOut = async () => {
    setSigningOut(true)
    await signOut()
    queryClient.removeQueries({ queryKey: ["backend-user"] })
    queryClient.removeQueries({ queryKey: ["user-sessions"] })
    queryClient.removeQueries({ queryKey: ["github-repos"] })
    navigate("/")
    setSigningOut(false)
  }

  const name = user?.fullName ?? user?.firstName ?? "User"
  const email = user?.primaryEmailAddress?.emailAddress ?? ""
  const avatar = user?.imageUrl

  const initials =
    name
      .split(" ")
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase())
      .join("") ?? "U"

  return (
    <Sidebar collapsible='icon' className='border-r border-sidebar-border'>
      <SidebarHeader className="w-full mb-2 flex flex-row items-center justify-between py-3! pb-0! pl-3">
        <Logo showText={state === "expanded"} />
        <SidebarTrigger className="hidden lg:flex -m-2 mb-0" />
      </SidebarHeader>

      <SidebarContent className='flex gap-0 px-0 pb-3'>
        <NavItems onSearchClick={() => setSearchOpen(true)} />
        {state === "expanded" && <ChatSessions />}
      </SidebarContent>

      <SidebarFooter className="border-t border-sidebar-border p-2">
        {isLoaded && user ? (
          <div
            className={cn(
              "flex items-center gap-3 rounded-lg px-2 py-2",
              state === "collapsed" && "justify-center px-0 flex-col"
            )}
          >
            <Avatar className="size-9">
              <AvatarImage src={avatar} alt={name} />
              <AvatarFallback>{initials}</AvatarFallback>
            </Avatar>
            {state === "expanded" ? (
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-sidebar-foreground">
                  {name}
                </p>
                <p className="truncate text-xs text-sidebar-foreground/60">
                  {email}
                </p>
              </div>
            ) : null}
            <div className={cn("flex items-center gap-1", state === "collapsed" && "flex-col")}>
              <ModeToggle />
              <Button
                type="button"
                size="icon"
                variant="ghost"
                className="shrink-0 text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
                onClick={handleSignOut}
                disabled={signingOut}
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
