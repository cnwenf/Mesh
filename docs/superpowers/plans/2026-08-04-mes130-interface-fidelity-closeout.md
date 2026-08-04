# MES-130 interface fidelity closeout plan

## Goal

Close the remaining interface-fidelity scope across the authenticated shell,
workspace pages, conversations, people, automation, analytics, settings, and
public flows. Use only runtime-observed design facts from the reference product
while preserving Mesh business behavior, routes, permissions, realtime data,
and accessibility contracts.

## Constraints

- Do not inspect or copy external source code, assets, branding, wording, or
  component markup. Runtime screenshots and computed visual facts are the only
  permitted design input.
- Keep `tokenValues.ts` as the only color, typography, spacing, radius, shadow,
  motion, and shell-dimension source; generated CSS remains immutable.
- Preserve every API, route, workspace isolation, RBAC, realtime, optimistic,
  keyboard, pointer, and touch contract already covered by tests.
- Keep the Mesh adapter layer between features and the component library; do
  not add another UI dependency or a second theme source.
- Every behavior or visual-contract change starts with a failing test. Global
  and per-file unit coverage remain at least 90%.
- Acceptance uses fresh production builds and real browser interaction at
  desktop and phone widths in both themes. Reference screenshots remain in
  ignored runtime storage and never enter Git.

## Test-first slices

1. Freeze the measured neutral palette, dense Inter type scale, 256px desktop
   rail, 14px page-frame radius, restrained shadows, and light/dark mappings in
   the interface baseline. Add failing token and shell geometry tests.
2. Rebuild the authenticated desktop shell around a full-height rail and inset
   bordered page frame. Preserve search, command shortcuts, workspace switch,
   connection status, inbox, mobile navigation, skip-link, and route behavior.
3. Align shared buttons, fields, tabs, tables, headers, cards, menus, dialogs,
   drawers, empty states, and page patterns to the measured density and states.
4. Align workspace home, projects, issues, issue detail, comments, and the
   two-dimensional board using shared page chrome and compact table/column
   structures without changing data flow.
5. Align inbox, chat, members/agents, squads, runtimes, skills, autopilots,
   analytics, integrations, and settings around the shared list/detail and
   settings patterns.
6. Align login and recovery pages with the shared public-flow treatment while
   retaining autofill, validation, error retention, theme, and phone-keyboard
   behavior.
7. Extend visual browser coverage for the shell and each page family across
   desktop/phone, light/dark, and normal/empty/error/offline states. Inspect
   actual/expected/diff before updating any intentional baseline.
8. Update the root README and authoritative Specs, then run source-provenance,
   asset-origin, license, dependency, and secret audits.

## Verification

- Run targeted Vitest suites after each red/green slice, then full frontend
  coverage with global and per-file gates at or above 90%.
- Run token generation idempotence, format, lint, stylelint, typecheck, build,
  contrast, responsive, accessibility, component-foundation, evidence, and
  dependency audit gates.
- Run the isolated production-auth browser stack with generated strong
  credentials and no host-published data-service ports; click, type, filter,
  navigate, switch theme, and exercise keyboard/touch paths as a real user.
- Run backend unit, coverage, migration, and real API/database E2E gates because
  frontend acceptance depends on the real service contract.
- Review the final diff for regressions, external-source residue, assets,
  licenses, secrets, raw colors, duplicate theme sources, and untested paths.
- Publish only after the worktree is clean, commit author and committer are
  `cnwenf <cnwenf@outlook.com>`, no co-author trailer exists, and CI is green.
