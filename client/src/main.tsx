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

if (!PUBLISHABLE_KEY) {
  throw new Error("Missing VITE_CLERK_PUBLISHABLE_KEY in .env")
}

createRoot(document.getElementById('root')!).render(
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
