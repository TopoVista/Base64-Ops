import { SignIn } from "@clerk/clerk-react";
import Logo from "@/components/logo";

const SignInPage = () => {
  return (
    <div className="grid min-h-svh lg:grid-cols-2">
      <div className="flex flex-col justify-between gap-10 p-6 md:p-10">
        <Logo />
        <div className="mx-auto flex w-full max-w-sm flex-1 items-center justify-center">
          <SignIn
            routing="hash"
            signUpUrl="/sign-up"
            fallbackRedirectUrl="/new"
            appearance={{
              elements: {
                card: "shadow-none border border-border rounded-xl",
                headerTitle: "text-2xl font-semibold tracking-tight",
                headerSubtitle: "text-sm text-muted-foreground",
                formButtonPrimary:
                  "bg-primary text-primary-foreground hover:bg-primary/90",
                footerActionLink: "text-primary font-medium",
              },
            }}
          />
        </div>
      </div>
      <div className="relative hidden overflow-hidden border-l bg-gradient-to-br from-zinc-950 via-zinc-900 to-zinc-800 lg:block">
        <div className="absolute inset-0 opacity-50 [background-image:radial-gradient(circle_at_20%_20%,rgba(255,255,255,0.16),transparent_30%),radial-gradient(circle_at_80%_10%,rgba(255,255,255,0.12),transparent_22%),radial-gradient(circle_at_50%_80%,rgba(255,255,255,0.08),transparent_28%)]" />
        <div className="relative flex h-full flex-col justify-end p-10 text-white">
          <p className="max-w-md text-3xl font-semibold leading-tight">
            Connect GitHub, spin up a workspace, and let the agent build.
          </p>
          <p className="mt-4 max-w-md text-sm text-white/70">
            One session, one branch, one sandbox.
          </p>
        </div>
      </div>
    </div>
  );
};

export default SignInPage;
