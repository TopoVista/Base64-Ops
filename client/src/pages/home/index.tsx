import ChatInterface from "@/components/chat"
import { useLocation } from "react-router-dom"

const HomePage = () => {
  const location = useLocation()
  return (
    <div className="w-full min-h-screen overflow-hidden bg-background">
      <ChatInterface key={location.key} />
    </div>
  )
}

export default HomePage
