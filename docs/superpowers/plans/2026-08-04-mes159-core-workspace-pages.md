# MES-159 core workspace pages migration plan

## Goal

Migrate the workspace home, project, issue, and board page families onto the
Appica-backed Mesh design foundation while preserving their existing backend,
realtime, routing, accessibility, and optimistic-interaction contracts.

## Constraints

- Keep the Mesh semantic-token layer authoritative; page code must not consume
  library palette values or introduce literal colors.
- Reuse the shared Mesh adapters for Appica components and import any new
  Appica primitive through a package subpath.
- Preserve workspace-scoped canonical routes, role gates, error envelopes,
  cursor pagination, realtime incremental merge, and optimistic rollback.
- Board drag-and-drop continues to use the atomic view move command, including
  WIP enforcement and cross-project confirmation; native drag, keyboard, and
  touch paths must remain equivalent.
- Keep all visible text externalized and preserve desktop/mobile plus
  light/dark behavior.
- Do not copy external implementation code, assets, branding, or wording.

## Test-first slices

1. Add page-family assertions for Appica-backed controls and responsive page
   landmarks, then migrate workspace home and project list/detail controls.
2. Add issue-list/detail assertions for filter, quick-create, activity,
   attachment, and state-transition surfaces, then migrate their controls.
3. Add board assertions for the specified column/list modes, drag handles,
   keyboard movement, touch movement, WIP feedback, and view configuration,
   then migrate their controls. Record the missing two-dimensional swimlane
   projection/move contract as a specification blocker instead of inventing it.
4. Add a supply-chain and page-foundation contract that rejects root-barrel
   library imports and detects regressions to unadapted interactive controls in
   the migrated page families.
5. Add real-stack browser coverage for workspace/project/issue/board flows at
   desktop and phone widths in light and dark themes.
6. Update the feature specifications, frontend capability documentation, and
   root README to identify the migrated page-family baseline and its tests.

## Verification

- Run targeted Vitest suites after every slice, then the complete frontend
  coverage suite with global and per-file coverage at or above 90%.
- Run lint, typecheck, format, build, responsive, accessibility, contrast,
  legacy-token, evidence, Appica-foundation, and dependency-audit gates.
- Start the isolated production stack with strong generated credentials and no
  host-published data-service ports; exercise real API and database-backed
  workspace, project, issue, comment, attachment, state, and board flows.
- Use a real browser to click, type, filter, create, comment, attach, transition,
  drag, keyboard-move, touch-move, switch theme, and verify phone/desktop layout.
- Run backend unit, coverage, and real e2e gates plus specification and source
  provenance checks before handoff.

## Review remediation

- Treat the `WorkspaceProvider` resolved from `/w/:workspaceSlug/*` as the only
  workspace source for the migrated project, issue, and board families. A page
  must never fall back to the first membership when a route slug is present.
- Cover an in-place A-to-B route change with delayed A responses. Each list,
  cursor, loading, and error write must be guarded by the current request
  generation so stale data cannot replace the B workspace state.
- Keep `MES128_FRONTEND_PORT` as the real-stack runner's single public-port
  input and pass the same value to Compose, Playwright, generated URLs, and the
  evidence manifest. Successful production-stack journeys must persist their
  evidence under `MES128_EVIDENCE_DIR`, not only attach it to a transient test
  report.
- Encode every dynamic workspace, project, issue, and view route segment before
  navigation, and keep component tests mounted through the same route/provider
  shape used by production.
