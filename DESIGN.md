---
version: alpha
name: "Base64 Ops"
description: "An evidence-first AI operations workspace with an editorial public front door and a high-trust developer workspace."
colors:
  ink: "#071735"
  action: "#0A5BEA"
  verified: "#C7FB38"
  rule: "#D7E3F5"
  canvas: "#FFFFFF"
  workspace: "#091726"
  muted: "#526887"
typography:
  sans:
    fontFamily: "Geist Variable, Arial, sans-serif"
  mono:
    fontFamily: "JetBrains Mono, ui-monospace, monospace"
  display:
    fontFamily: "Impact, Arial Narrow, Geist Variable, sans-serif"
rounded:
  DEFAULT: "0.5rem"
  sm: "0.25rem"
  md: "0.5rem"
  lg: "0.75rem"
spacing:
  page-gutter: "3.5rem"
  content-max: "90rem"
  public-section: "5.7rem"
components:
  button:
    action: "Cobalt filled, square-to-slightly-rounded 8px geometry; reserved lime for verified approval."
  workspace:
    surface: "Deep navy operational window with 1px blue-gray rules and dense source/evidence UI."
  public-nav:
    surface: "True-white editorial header with a hairline rule."
---

# Base64 Ops Design System

## Overview

### Creative North Star

Base64 Ops should feel like an impeccably annotated production incident report that has become a working tool: source evidence, diffs, and approval scope are visible before persuasion. Its distinctive public signature is a bright editorial page that opens into a dark, real-looking operational workspace—not a floating generic dashboard.

### Product context and register

- **Audience and primary job:** Engineers and engineering leaders investigating repository and CI failures, then reviewing constrained fixes. Product facts come from `README.md` and `docs/PRODUCT_STRATEGY.md`.
- **Target market(s) and evidence:** English-language developer tooling; no Japan-market claim or locale is defined.
- **Locale(s) and language policy:** English owned UI. Use plain, direct verbs and keep technical paths in monospace.
- **Usage scene:** A public landing page explains the trust model; the authenticated workspace supports dense, time-sensitive evidence review.
- **Register:** Hybrid. `/` is editorial/product marketing; `/sign-in` and authenticated routes are operational product surfaces.
- **Memorable signature:** The proof chain—failure, evidence, exact diff, human approval—appears as a connected engineering artifact across public and product routes.
- **Restraint:** Marketing uses open white space and a single deep workspace window. Product routes prioritise evidence density over decorative flourishes.
- **Anti-references:** Warm venture-capital SaaS gradients, anonymous metric cards, stock developer photography, and AI-autopilot claims. The product must make consent and scope visible.
- **Token ownership/runtime mapping:** `DESIGN.md` documents the durable visual decisions. `client/src/index.css` is the canonical runtime implementation; public tokens are scoped below `.landing-page`, existing workspace tokens remain the authenticated product source.

## Colors

`ink` (#071735) carries headline authority and dark workflow bands. `canvas` (#FFFFFF) is deliberately true white. `action` (#0A5BEA) is the only primary navigational/action color. `verified` (#C7FB38) is semantic: it means a verified safe state or approval and must never become general decoration. `rule` (#D7E3F5) creates the technical document grid and quiet dividers. `workspace` (#091726) contains diagnostic UI, while `muted` (#526887) is supportive body text. Existing authenticated light/dark variables in `client/src/index.css` preserve semantic danger, warning, and focus colors.

## Typography

Geist Variable is the primary UI and body face. `Impact`/`Arial Narrow` is intentionally limited to landing-page thesis statements and major editorial headings; product controls never use it. JetBrains Mono is used for source paths, diffs, identifiers, and compact evidence values. Use sentence case, direct verbs, and line lengths that keep diagnostic prose readable.

## Layout

Public surfaces are centered at 90rem with a 3.5rem desktop gutter, a 2rem tablet gutter, and a 1.25rem mobile gutter. Public sections alternate open white canvas and one dense dark workflow band; they do not stack repeated cards. Authenticated surfaces may be denser but must retain visible focus, readable source text, and stable scroll ownership. Responsive collapse moves public columns to a single readable flow without hiding actions.

## Elevation & Depth

The public page is flat by default. Only the hero workspace gets a controlled deep shadow to establish it as a working artifact. The dark workflow band uses tonal separation and thin rules, not soft floating cards. Existing app dialogs and overlays keep their established token system.

## Shapes

Use near-square engineering geometry: 0–8px corners and 1px rules. Circular markers are reserved for sequence or status. Do not introduce rounded-pill marketing labels. Buttons are compact rectangular actions; destructive actions remain visually distinct in the authenticated product.

## Components

### Foundational visual states

Every interactive control has hover, focus-visible, active, and disabled treatment. Public preview stage controls update the visible workflow explanation; reduced-motion preference suppresses transitions. Product feedback uses the existing app-owned toast/dialog system rather than browser dialogs.

### Buttons and actions

Use cobalt filled buttons for navigation or an active intentional action. Pair a text link with a primary action when appropriate. Lime approval actions always describe the scope and must represent an approval/verified-safe state, never a casual CTA.

### Navigation and data display

The public header is minimal: product, workflow, safety, sign in, and one action. Evidence views use source rows, diffs, approval scope, and proof-chain markers instead of generic metric tiles.

### Forms and overlays

Clerk remains the authentication provider. Auth routes preserve only approved internal post-auth destinations using `client/src/lib/auth-redirect.ts`; sign-in and sign-up remain distinct URLs. Service availability is communicated inline on the sign-in page.

### Iconography

Use Lucide outline icons at 14–18px for controls and source types. Icons clarify a technical object or action; they do not replace important labels.

### Motion

Motion is subtle, limited to control feedback and the landing proof preview. No looping decorative animation. Respect `prefers-reduced-motion`.

### Content and data visualization

Product voice is specific, evidence-led, and human-in-control: “Review exact change,” not “Magic fix.” A diff uses red/green only for removed/added code and never as the sole carrier of meaning.

## Do's and Don'ts

- **Do:** Show the source, scope, and human decision beside a proposed repair.
- **Do:** Use the public landing page to lead directly into a working, mutation-disabled demo or the sign-in flow.
- **Don't:** Use verified lime outside approval/verification semantics.
- **Don't:** turn the product into a generic AI chat or an ungrounded autonomous-code claim.
