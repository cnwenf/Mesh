# MES-161 global auxiliary pages and finish plan

## Goal

Finish the global search, analytics, authentication, onboarding, error-state,
responsive, and theme surfaces on the Appica-backed Mesh design foundation
without changing their existing backend contracts.

## Constraints

- Keep routing, authorization, workspace visibility, search ordering, analytics
  metric definitions, onboarding persistence, and theme negotiation unchanged.
- Reuse the design-system adapters and semantic tokens; page code must not import
  the component-library root barrel or add raw color values.
- Preserve keyboard, IME, focus-return, reduced-motion, and 320px reflow
  contracts.
- Add no new third-party dependency or external runtime resource.

## Test-first slices

1. Add failing route tests for a direct public registration entry and a signed-in
   permission-denied recovery page, then wire the reusable page components,
   route manifest, and bilingual catalog entries.
2. Audit the existing create-issue command and invalid-recent reconciliation,
   then add failing coverage for unsafe activation URLs, modified clicks, IME,
   platform keycaps, and shared Appica controls without changing server search
   behavior.
3. Add analytics presentation tests, then move filters, loading surfaces, and
   page structure onto shared design components while retaining query inputs and
   chart semantics.
4. Recheck onboarding and authentication flows against the shared public-flow,
   button, input, badge, and skeleton adapters; cover every behavior change with
   focused unit tests.
5. Add browser coverage for registration, permission recovery, 404, command
   palette, analytics, onboarding, light/dark themes, and desktop/phone viewports.
6. Update the capability matrix, feature specifications, and README with the
   concrete delivered behavior and evidence.

## Verification

- Run targeted Vitest suites during each slice, then the full coverage command
  with the repository's global and per-file 90% gates.
- Run lint, typecheck, build, token generation, Appica foundation, responsive,
  accessibility, contrast, evidence, audit, and license checks.
- Start the production-like stack with generated strong credentials and internal
  data services, then exercise the affected routes through real HTTP APIs and a
  real browser at desktop/phone widths in light/dark themes.
- Review the final diff for source provenance, hard-coded colors, public runtime
  resources, secrets, commit identity, and co-author trailers before publishing.
