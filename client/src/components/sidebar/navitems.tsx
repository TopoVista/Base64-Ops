import { PROTECTED_ROUTES, PUBLIC_ROUTES } from "@/routes/route";
import { BookOpenIcon, PlusIcon, SearchIcon } from "lucide-react";
import { SidebarGroup, SidebarGroupContent, SidebarMenu, SidebarMenuButton, SidebarMenuItem, SidebarSeparator } from "../ui/sidebar";
import { Link } from "react-router-dom";


const navItems = [
  { title: "New operation", icon: PlusIcon, href: PROTECTED_ROUTES.NEW },
  { title: "How to use Base64", icon: BookOpenIcon, href: PUBLIC_ROUTES.GUIDE },
];



const NavItems = ({onSearchClick}:{onSearchClick:() => void}) => {
  return (
    <SidebarGroup  className="px-2 pt-2">
      <SidebarGroupContent>
        <SidebarMenu className="space-y-1.5">
            {navItems.map((item) => (
                <SidebarMenuItem key={item.title}>
                    <SidebarMenuButton tooltip={item.title} asChild>
                        <Link to={item.href}>
                        <item.icon />
                        <span>{item.title}</span>
                        </Link>
                    </SidebarMenuButton>
                </SidebarMenuItem>
            ))}
               <SidebarMenuItem>
            <SidebarMenuButton tooltip="Search" onClick={onSearchClick} >
              <SearchIcon />
              <span>Search</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarGroupContent>
      <SidebarSeparator className="my-2" />
    </SidebarGroup>
  )
}

export default NavItems
