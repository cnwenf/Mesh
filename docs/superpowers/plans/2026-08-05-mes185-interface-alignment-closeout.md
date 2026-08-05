# MES-185 interface alignment closeout plan

## Goal

Complete the interface-alignment closeout for the existing Mesh web surface
without changing business contracts, canonical routes, permissions, realtime
behavior, or accessibility semantics. Record every checklist assertion as
verified, retained for a later business slice, or dependent on a dedicated
environment instead of treating visual polish as proof of product behavior.

## Constraints

- Mesh specifications and the shared semantic-token layer remain the only
  authorities for behavior and styling.
- Do not introduce external source code, assets, branding, wording, fonts, or
  parallel component implementations.
- Preserve workspace isolation, RBAC, deep links, optimistic convergence,
  realtime merge rules, keyboard paths, and responsive behavior.
- Every behavior or visual-contract change starts with a failing test. Global
  and changed-file unit coverage remain at least 90%.
- Browser acceptance uses fresh production builds at desktop and phone widths
  in light and dark themes; only actual passing assertions may close a normal
  checklist item.

## Test-first slices

1. Freeze the measured public-flow, application-shell, board, settings, phone,
   and theme geometry in shared tokens and contract tests.
2. Add the profile route and connect display-name and HTTPS-avatar updates to
   the existing account API while preserving the canonical user model.
3. Align the settings layout, navigation, field descriptions, validation, and
   compact reflow through shared design patterns.
4. Enrich the board card presentation only with fields available in the
   projection response, preserving virtualization, drag, WIP, and realtime
   behavior.
5. Complete the account menu, shared relative-time presentation, and the
   workspace home summaries with their existing APIs and deep links.
6. Audit every open checklist assertion and classify it as verified, retained
   business scope, or environment/optional scope with an explicit reason.
7. Refresh the desktop/phone and light/dark visual baselines only after actual,
   expected, and diff inspection confirms an intentional change.
8. Update the authoritative specifications, root documentation, and the final
   audit with reproducible verification results.

## Verification

- Run targeted Vitest suites after every slice, then the complete frontend
  coverage suite with global and per-file coverage at or above 90%.
- Run format, lint, stylelint, typecheck, build, contrast, responsive,
  accessibility, semantic-token, evidence, and source-audit gates.
- Run the production-auth browser stack and exercise real navigation, form
  edits, theme switching, keyboard paths, and phone reflow.
- Run backend tests and real API/database smoke coverage for contracts consumed
  by changed frontend paths.
- Review the final diff for permission, routing, realtime, accessibility,
  responsive, secret, dependency, asset, and untested-branch regressions.
- Publish only with a clean worktree, the required commit identity, no co-author
  trailer, and a passing CI handoff.

