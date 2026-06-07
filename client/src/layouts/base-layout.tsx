import { Outlet } from "react-router-dom"

const BaseLayout = () => {
  return (
    <div className="w-full flex-col h-full min-h-0">
      <div className="w-full h-full flex items-center justify-center">
        <div className="mx-auto w-full h-full">
            <Outlet/>
        </div>
      </div>
    </div>
  )
}

export default BaseLayout
