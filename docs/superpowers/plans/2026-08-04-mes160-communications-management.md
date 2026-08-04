# MES-160 communications and management migration plan

## Goal

Migrate the communications and management page families onto the established
Mesh/Appica shell and page patterns without changing their domain API contracts.
Close the cross-workspace routing defect discovered during the migration, and
prove the result against real backend, database, realtime, theme, and responsive
surfaces.

## Constraints

- A canonical `/w/:workspaceSlug/*` route is authoritative. No page may fall
  back to the first membership while a `WorkspaceProvider` is present.
- Flat legacy routes remain supported only through the existing migration layer.
- Business code imports Mesh design components and patterns, never third-party
  components directly.
- Existing REST, SSE, WebSocket, permission, and localization contracts remain
  intact unless a failing regression test demonstrates a defect.
- Tests are written red before each implementation slice. Global and changed
  code coverage must remain at least 90%.

## Test-first slices

1. Add a provider-aware workspace-membership hook contract. Cover second-
   membership selection, provider loading/error barriers, flat-route fallback,
   absent membership, request failure, and stale response cancellation.
2. Migrate inbox and chat to that contract, preserve the canonical workspace in
   navigation, and resolve the current member independently of list contents for
   realtime subscriptions. Add multi-workspace and realtime regression tests.
3. Migrate the unified member/Agent roster and Agent detail. Correct canonical
   links, member-event subscriptions, Agent event payload filtering, and
   type-aware lifecycle actions with regression tests.
4. Migrate runtime, skill, squad, and autopilot pages to provider-aware workspace
   resolution and canonical links. Gate management controls from the resolved
   workspace role and add two-membership tests.
5. Move list-page shells onto `DataView`/`PageHeader` and accessible shared tabs
   while retaining feature test identifiers and domain state.
6. Update the relevant specifications, migration checklist, and README capability
   matrix with the canonical workspace and page-pattern contracts.

## Verification

- Run targeted Vitest suites after each slice, then the full coverage suite and
  changed-code coverage check.
- Run lint, typecheck, build, Appica supply-chain, accessibility, responsive,
  contrast, legacy-token, and dependency-audit gates.
- Start the real backend with unique generated credentials and run canonical
  multi-workspace browser journeys. Assert persisted database state and observed
  WebSocket/SSE behavior.
- Capture/verify inbox, chat, members, Agent, workspace settings, runtimes,
  skills, squads, and autopilots in light/dark themes at desktop and mobile
  viewports.
- Perform a final diff review before publishing a ready pull request.
