# MES-158 Appica foundation and shell migration plan

## Goal

Adopt the MIT-licensed `@appica/ui-react` 1.0.0 package as the component and
theme foundation for Mesh while preserving the existing API, routing,
realtime, accessibility, and three-level theme-negotiation contracts.

## Constraints

- Import components from package subpaths so unused components remain
  tree-shakeable.
- Keep Mesh's server-injected/no-flash theme negotiation authoritative. The
  library provider must not create a second user preference or storage key.
- Map library tokens to Mesh semantic tokens in one generated bridge; business
  components continue to consume semantic values rather than raw colors.
- Preserve current route targets, workspace role gates, realtime state, and
  keyboard semantics in the application shell.
- Pin the library version, retain its MIT notice, and fail CI on version,
  license, notice, or root-barrel import drift.

## Test-first slices

1. Add foundation tests that require the pinned package, provider bridge,
   generated token aliases, light/dark class synchronization, and subpath-only
   imports. Run them red before implementation.
2. Add shell assertions for Appica navigation/input slots while retaining the
   current links, active states, search behavior, and accessibility names.
3. Install `@appica/ui-react@1.0.0`, Tailwind 4, and the Vite integration with
   exact versions. Add the third-party notice and supply-chain checker.
4. Generate the Appica-to-Mesh token bridge from `tokenValues.ts`; wire the
   library stylesheet before Mesh overrides and synchronize `light`/`dark`
   classes from the existing provider.
5. Adapt shared Button, IconButton, Input, Kbd, Avatar, Badge, and Skeleton
   primitives, then migrate Sidebar and TopBar to Appica navigation/input
   components through package subpaths.
6. Update the theme/design specifications, README capability matrix, and CI
   idempotence/supply-chain gates.

## Verification

- Targeted Vitest suites while iterating, then full `npm run test:coverage`
  with global and per-file thresholds at or above 90%.
- `lint`, `typecheck`, responsive/a11y/legacy-token/evidence/contrast gates,
  production build, exact-version/license checker, and `npm audit`.
- Existing real backend e2e plus a real browser walkthrough of shell routes,
  navigation, search, theme switching, and responsive controls.
- Backend unit/e2e coverage and repository spec/provenance checks required by
  the project gate.
