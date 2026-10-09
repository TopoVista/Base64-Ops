import { RouterProvider } from "react-router-dom";
import { Suspense } from "react";
import { router } from "./routes";

function App() {
    return <Suspense fallback={<div className="grid min-h-screen place-items-center bg-background text-sm text-muted-foreground">Loading Base64 Ops…</div>}><RouterProvider router={router} /></Suspense>;
}

export default App
