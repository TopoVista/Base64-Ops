import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircle2, CreditCard, Gauge, Loader2, ShieldCheck } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { getUsageEntitlement, startSubscriptionCheckout } from "@/lib/api";

const PLAN_COPY = {
  free: {
    title: "Free",
    description: "Explore evidence-backed investigations with a server-enforced monthly allowance.",
  },
  pro: {
    title: "Pro",
    description: "For individual engineers who need substantially more repository investigations.",
  },
  team: {
    title: "Team",
    description: "For shared operational workflows with higher tenant capacity.",
  },
} as const;

const BillingPage = () => {
  const entitlement = useQuery({ queryKey: ["billing-entitlement"], queryFn: getUsageEntitlement });
  const checkout = useMutation({
    mutationFn: startSubscriptionCheckout,
    onSuccess: ({ checkoutUrl }) => {
      if (checkoutUrl) window.location.assign(checkoutUrl);
    },
  });
  const currentTier = entitlement.data?.tier ?? "free";
  const current = PLAN_COPY[currentTier];

  return (
    <main className="min-h-screen overflow-auto bg-background px-5 py-8 md:px-10">
      <section className="mx-auto max-w-5xl">
        <div className="flex flex-col justify-between gap-4 border-b border-border pb-6 sm:flex-row sm:items-end">
          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-primary">Base64 Ops billing</p>
            <h1 className="text-3xl font-semibold tracking-tight">Usage that stays predictable.</h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
              Agent runs are reserved before expensive repository analysis begins. Your plan is enforced by the API,
              never by the browser.
            </p>
          </div>
          <Badge variant="outline" className="h-7 px-3 capitalize">{current.title} plan</Badge>
        </div>

        <section className="mt-7 grid gap-4 md:grid-cols-[1.2fr_0.8fr]">
          <div className="rounded-xl border border-border bg-card p-5 shadow-sm">
            <div className="flex items-center gap-2 text-sm font-medium"><Gauge className="size-4 text-primary" /> Monthly agent-run allowance</div>
            {entitlement.isLoading ? (
              <div className="mt-7 flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" /> Loading usage…</div>
            ) : entitlement.isError ? (
              <p className="mt-5 text-sm text-destructive">Usage could not be loaded. Retry once the API is available.</p>
            ) : (
              <>
                <div className="mt-6 flex items-baseline gap-2"><span className="text-4xl font-semibold">{entitlement.data?.remaining_agent_runs}</span><span className="text-sm text-muted-foreground">runs remaining this month</span></div>
                <div className="mt-5 h-2 overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full rounded-full bg-primary transition-[width]"
                    style={{ width: `${Math.min(100, ((entitlement.data?.consumed_agent_runs ?? 0) / Math.max(1, entitlement.data?.monthly_agent_run_limit ?? 1)) * 100)}%` }}
                  />
                </div>
                <p className="mt-2 text-xs text-muted-foreground">{entitlement.data?.consumed_agent_runs} of {entitlement.data?.monthly_agent_run_limit} reserved in {entitlement.data?.period_key}.</p>
                <div className="mt-5 flex items-center justify-between gap-3 border-t border-border pt-4 text-xs text-muted-foreground">
                  <span>Observed model tokens</span>
                  <span className="font-medium text-foreground">{(entitlement.data?.consumed_model_tokens ?? 0).toLocaleString()} / {(entitlement.data?.monthly_model_token_limit ?? 0).toLocaleString()}</span>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">{(entitlement.data?.remaining_model_tokens ?? 0).toLocaleString()} tokens remain this month.</p>
              </>
            )}
          </div>
          <div className="rounded-xl border border-primary/25 bg-primary/5 p-5">
            <div className="flex items-center gap-2 text-sm font-medium"><ShieldCheck className="size-4 text-primary" /> Secure billing boundary</div>
            <p className="mt-3 text-sm leading-6 text-muted-foreground">Checkout is hosted by Razorpay. Base64 Ops never handles card or UPI credentials, and subscription changes require a verified provider webhook.</p>
          </div>
        </section>

        <section className="mt-8 grid gap-4 lg:grid-cols-3">
          {(Object.keys(PLAN_COPY) as Array<keyof typeof PLAN_COPY>).map((tier) => {
            const plan = PLAN_COPY[tier];
            const isCurrent = tier === currentTier;
            const isPaid = tier !== "free";
            return <article key={tier} className="flex min-h-72 flex-col rounded-xl border border-border bg-card p-5 shadow-sm">
              <h2 className="text-lg font-semibold">{plan.title}</h2>
              <p className="mt-2 min-h-16 text-sm leading-6 text-muted-foreground">{plan.description}</p>
              <ul className="mt-5 space-y-2 text-sm text-muted-foreground">
                <li className="flex gap-2"><CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-500" />Evidence-first diagnoses</li>
                <li className="flex gap-2"><CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-500" />Approval-gated delivery</li>
                <li className="flex gap-2"><CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-500" />Usage visible before model work</li>
              </ul>
              <div className="mt-auto pt-6">
                {isCurrent ? <Button variant="outline" className="w-full" disabled>Current plan</Button> : isPaid ? (
                  <Button className="w-full" disabled={checkout.isPending} onClick={() => checkout.mutate(tier)}>
                    {checkout.isPending ? <Loader2 className="animate-spin" /> : <CreditCard />} Upgrade to {plan.title}
                  </Button>
                ) : <Button variant="outline" className="w-full" disabled>Included</Button>}
                {checkout.isError && isPaid ? <p className="mt-2 text-xs text-destructive">{checkout.error instanceof Error ? checkout.error.message : "Checkout is unavailable."}</p> : null}
              </div>
            </article>;
          })}
        </section>
      </section>
    </main>
  );
};

export default BillingPage;
