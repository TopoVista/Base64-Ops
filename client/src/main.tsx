import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ClerkProvider } from '@clerk/clerk-react'
import './index.css'
import App from './App.tsx'
import { ThemeProvider } from './components/theme-provider.tsx'
import { Toaster } from './components/ui/sonner.tsx'
import { TooltipProvider } from './components/ui/tooltip.tsx'
import ClerkTokenProvider from './components/clerk-token-provider.tsx'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Session reads are cheap and server-throttled. Keeping this one query
      // fresh lets newly failed GitHub Actions runs surface in an open
      // workspace without requiring a manual log paste or navigation.
      refetchInterval: (query) => query.queryKey[0] === "session" ? 60_000 : false,
      refetchIntervalInBackground: false,
    },
  },
})

const PUBLISHABLE_KEY = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY

const root = createRoot(document.getElementById('root')!)

const ConfigurationRequired = () => (
  <main className="grid min-h-screen place-items-center bg-background p-6 text-foreground">
    <section className="w-full max-w-lg rounded-2xl border border-border bg-card p-7 shadow-sm">
      <p className="text-sm font-medium text-muted-foreground">Base64 Ops configuration required</p>
      <h1 className="mt-1 text-2xl font-semibold tracking-tight">Authentication is not configured</h1>
      <p className="mt-3 text-sm leading-6 text-muted-foreground">
        This deployment is missing its public authentication setting. Ask the workspace administrator to set
        <code className="mx-1 rounded bg-muted px-1.5 py-0.5 font-mono text-xs text-foreground">VITE_CLERK_PUBLISHABLE_KEY</code>
        in the frontend environment, then redeploy or reload this page.
      </p>
      <p className="mt-4 text-xs leading-5 text-muted-foreground">
        No session, repository, approval, or replay data was requested.
      </p>
    </section>
  </main>
)

if (!PUBLISHABLE_KEY) {
  root.render(
    <StrictMode>
      <ConfigurationRequired />
    </StrictMode>,
  )
} else {
  root.render(
  <StrictMode>
    <ClerkProvider publishableKey={PUBLISHABLE_KEY} afterSignOutUrl="/">
      <QueryClientProvider client={queryClient}>
        <ClerkTokenProvider>
          <ThemeProvider defaultTheme="light" storageKey="vite-ui-theme">
            <TooltipProvider>
              <App />
              <Toaster richColors />
            </TooltipProvider>
          </ThemeProvider>
        </ClerkTokenProvider>
      </QueryClientProvider>
    </ClerkProvider>
  </StrictMode>,
)
}
