# Taste (Continuously Learned by [CommandCode][cmd])

[cmd]: https://commandcode.ai/

# Social Oauth
- When adding a social OAuth integration (e.g., GitHub, Google), follow this structure: 1) Create `src/lib/social-oauth/` with encryption (AES-256-GCM), state (HMAC-signed CSRF), and an index.ts with OAuth URLs/token exchange/refresh/user API helpers, 2) Create `src/models/{provider}-account.model.ts`, 3) Create `src/services/{provider}.service.ts` with connect/callback/getAccessToken/disconnect, 4) Create `src/controllers/{provider}.controller.ts` with Zod validation, 5) Create `src/routes/{provider}.route.ts`, 6) Update env.config.ts, routes/index.ts, and .env/.env.example. Confidence: 0.70

# Resource Scaffolding
- When scaffolding a new resource (e.g., session, product, blog), follow this pattern: 1) Controllers use `asyncHandler` wrapper, and protected routes extract `_id` from `req.user`, 2) Validation schemas go in `src/validators/`, 3) Services export scaffolded function stubs that return placeholder text responses, 4) Routes use `passportAuthenticateJwt` middleware for protected endpoints. Confidence: 0.70
- Controllers must include a `message` field in every JSON response (e.g., `{ message: "Sessions retrieved successfully", sessions }`), not just return data without a message. Follow the existing pattern from auth and github controllers. Confidence: 0.65

# node.js-scaffolding
See [node.js-scaffolding/taste.md](node.js-scaffolding/taste.md)

# React Scaffolding
- When scaffolding a React app, do the shell in two layers. Layer 1 (base scaffold): 1) Install Vite + add tailwindcss + add shadcn/ui with Vite, 2) Create folder structure (pages/, routes/, types/, layout/), 3) Install react-router-dom + @tanstack/react-query + axios and create routes/ folder with index.tsx, route-guard, route.ts, 4) Import router into App.tsx and wrap with QueryClientProvider in main.tsx, 5) Create layout files (base-layout.tsx, app-layout.tsx), 6) Add axios-client.ts, api.ts (with one example endpoint wired to useQuery), env.ts, 7) Create a home/ page with a working example so the user can browse and see it running. Do NOT include auth pages or auth forms in layer 1. Layer 2 (auth pages) is a separate follow-up: sign-in.tsx and sign-up.tsx with react-hook-form + zod + useMutation, auth types, and useUser hook. Confidence: 0.85
- After scaffolding a React app, always start the Vite dev server (`npm run dev`) to verify it runs correctly in the browser, not just verify with `npm run build` or static checks. Confidence: 0.70

# Tooling
- Use npm as the package manager, not pnpm or yarn. Confidence: 0.70

# React Scaffolding
- When scaffolding a React app, do the shell in two layers. Layer 1 (base scaffold): 1) Install Vite + add tailwindcss + add shadcn/ui with Vite, 2) Create folder structure (pages/, routes/, types/, layout/), 3) Install react-router-dom + @tanstack/react-query + axios and create routes/ folder with index.tsx, route-guard, route.ts, 4) Import router into App.tsx and wrap with QueryClientProvider in main.tsx, 5) Create layout files (base-layout.tsx, app-layout.tsx), 6) Add axios-client.ts, api.ts (with one example endpoint wired to useQuery), env.ts, 7) Create a home/ page with a working example so the user can browse and see it running. Do NOT include auth pages or auth forms in layer 1. Layer 2 (auth pages) is a separate follow-up: sign-in.tsx and sign-up.tsx with react-hook-form + zod + useMutation, auth types, and useUser hook. Confidence: 0.85
- After scaffolding a React app, always start the Vite dev server (`npm run dev`) to verify it runs correctly in the browser, not just verify with `npm run build` or static checks. Confidence: 0.70

# React Scaffolding
- When scaffolding a React app, do the shell in two layers. Layer 1 (base scaffold): 1) Install Vite + add tailwindcss + add shadcn/ui with Vite, 2) Create folder structure (pages/, routes/, types/, layout/), 3) Install react-router-dom + @tanstack/react-query + axios and create routes/ folder with index.tsx, route-guard, route.ts, 4) Import router into App.tsx and wrap with QueryClientProvider in main.tsx, 5) Create layout files (base-layout.tsx, app-layout.tsx), 6) Add axios-client.ts, api.ts (with one example endpoint wired to useQuery), env.ts, 7) Create a home/ page with a working example so the user can browse and see it running. Do NOT include auth pages or auth forms in layer 1. Layer 2 (auth pages) is a separate follow-up: sign-in.tsx and sign-up.tsx with react-hook-form + zod + useMutation, auth types, and useUser hook. Confidence: 0.85
- After scaffolding a React app, always start the Vite dev server (`npm run dev`) to verify it runs correctly in the browser, not just verify with `npm run build` or static checks. Confidence: 0.70
