"use client";

import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Command, CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { getUserSessionsWithSearch } from "@/lib/api";
import { cn } from "@/lib/utils";

type SessionSearchDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

const SessionSearchDialog = ({ open, onOpenChange }: SessionSearchDialogProps) => {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedSearch(search.trim());
    }, 250);

    return () => window.clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    if (!open) {
      setSearch("");
      setDebouncedSearch("");
    }
  }, [open]);

  const { data, isPending } = useQuery({
    queryKey: ["user-sessions", debouncedSearch],
    queryFn: () =>
      getUserSessionsWithSearch({
        search: debouncedSearch || undefined,
        pageSize: 10,
        pageNumber: 1,
      }),
    enabled: open,
    retry: false,
  });

  const sessions = data?.sessions ?? [];

  return (
    <CommandDialog
    className="w-full! max-w-2xl!"
     open={open} 
    onOpenChange={onOpenChange} 
    title="Search sessions" description="Search your chat sessions">
      <Command shouldFilter={false} className="w-full
      bg-transparent py-6 pt-4 space-y-2">
        <CommandInput
          value={search}
          onValueChange={setSearch}
          placeholder="Search sessions..."
          className="py-4!"
          autoFocus
        />
        <CommandList className="max-h-[420px] border-t px-2">
          <CommandEmpty>{isPending ? "Searching..." : "No sessions found."}</CommandEmpty>
          <CommandGroup heading="Sessions">
            {sessions.map((session) => {
              const isActive = pathname.includes(session.slugId);
              return (
                <CommandItem
                  key={session._id}
                  value={`${session.title ?? ""} ${session.repoName ?? ""} ${session.slugId}`}
                  onSelect={() => {
                    navigate(`/session/${session.slugId}`);
                    onOpenChange(false);
                  }}
                  className={cn(isActive && "bg-muted")}
                >
                  <div className="flex min-w-0 flex-1 flex-col">
                    <span className="truncate font-medium">{session.title ?? "Untitled Session"}</span>
                    <span className="truncate text-xs text-muted-foreground">{session.repoName}</span>
                  </div>
                </CommandItem>
              );
            })}
          </CommandGroup>
        </CommandList>
      </Command>
    </CommandDialog>
  );
};

export default SessionSearchDialog;
