import { PROTECTED_ROUTES } from "@/routes/route";
import { PlusIcon, SearchIcon } from "lucide-react";
import { SidebarGroup, SidebarGroupContent, SidebarMenu, SidebarMenuButton, SidebarMenuItem, SidebarSeparator } from "../ui/sidebar";
import { Link } from "react-router-dom";


const navItems = [
  { title: "New Session", icon: PlusIcon, href: PROTECTED_ROUTES.NEW },
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
