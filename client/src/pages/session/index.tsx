import ChatInterface from "@/components/chat"
import { getSessionBySlug } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom"

const SessionPage = () => {
  const {slugid} = useParams()

   const { data, isPending } = useQuery({
    queryKey: ["session", slugid],
    queryFn: () => getSessionBySlug(slugid ?? ""),
    enabled: Boolean(slugid),
    retry: false,
  });

  if(isPending){
    return (
      <div className="flex h-full min-h-0 w-full items-start justify-center px-6 py-8">
        <div className="w-full max-w-4xl space-y-3">
          <div className="h-5 w-56 animate-pulse rounded-md bg-muted" />
          <div className="h-4 w-full animate-pulse rounded-md bg-muted" />
          <div className="h-4 w-3/4 animate-pulse rounded-md bg-muted" />
        </div>
      </div>
    );
  }

  if(!data && !isPending) return <div>Session not found</div>
  
  return (
    <div className="w-full h-full overflow-hidden">
      <ChatInterface
        key={data.session.slugId}
        initialMessages={data.messages}
        sessionTitle={data.session.title ?? "Untitled Session"}
        slugId={data.session.slugId}
        repoUrl={data.session.repoUrl ?? ""}
        defaultBranch={data.session.defaultBranch ?? "main"}
        branchName={data.session.branchName ?? null}
      />
    </div>
  )
}

export default SessionPage
