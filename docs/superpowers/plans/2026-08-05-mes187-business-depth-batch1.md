# MES-187 business-depth batch 1 plan

## Goal

Close the retained issue, board, label/property, member, and project behavior
gaps from MES-185. Replace projection and UI placeholders with real service
contracts, preserve all existing invariants, and verify the result through
unit, real API/database, and real-browser acceptance.

## Constraints

- `docs/specs/features/*.md`, the root README consistency anchors, and the
  regular checklist semantics in §0.2 are authoritative.
- Keep label, custom-field, issue, member, project, and view writes in their
  owning services. Do not invent frontend-only state that contradicts persisted
  data or realtime events.
- Preserve workspace/project visibility, RBAC, optimistic concurrency,
  transactional outbox, issue assignment idempotency, immutable identifier
  prefixes, and two-step cross-project moves.
- Multi-valued board axes are projections: existing cards cannot be moved or
  manually reordered through those axes. Counts and dynamic columns come from
  the projection response.
- New behavior starts with a failing unit or component test. Changed modules and
  the full suites must meet the 90% coverage gates.
- All visible strings are localized; desktop/phone and light/dark use the same
  semantic DOM and token source.

## Test-first slices

1. Add failing projection tests for label and custom-field filters, one- and
   two-axis grouping, multi-value duplication, null groups, deterministic
   ordering, overall cursors, and dynamic group values. Replace
   `projection_field_pending` with real association-backed projection.
2. Add board component tests that consume response-provided group skeletons,
   render newly created values as columns or lanes, hide move/reorder controls
   for multi-value axes, and render compact label dots with a `+N` overflow.
3. Add label-management tests for source/target selection, affected-count
   confirmation, merge execution, error retention, and post-merge cache or
   realtime convergence. Implement the dialog against the existing merge API.
4. Add issue-detail tests for strict allowed transitions, disabled illegal
   targets, `required_field_missing` inline feedback, agent assignment notice,
   and unchanged-assignee no-op behavior. Keep 409 convergence lossless.
5. Add member tests for agent identity, busy state, capability help, disabled
   owner selection, and the human/agent detail drawer data. Abort detail reads
   when the drawer closes and keep the 1280px layout within the page frame.
6. Add project/profile tests for live key availability, immutable-prefix delete
   disclosure, optimistic rollback, avatar clearing, and server-authoritative
   HTTPS URL validation. Implement only the missing client behavior.
7. Update the feature Specs, README, checklist statuses, MES-185 audit anchors,
   and evidence index only after the corresponding assertions pass.
8. Run real PostgreSQL API journeys and real browser journeys for projection,
   label merge, required fields, strict transitions, assignment, member detail,
   project keys, deletion disclosure, avatar clearing, and 409 rollback.

## Verification

- Backend: targeted unit tests, full `pytest --cov` with ≥90% coverage, Ruff,
  OpenAPI/spec consistency, migrations, and real-service e2e against a real
  PostgreSQL database.
- Frontend: targeted Vitest during each slice; full coverage and per-file gate;
  format, ESLint, Stylelint, typecheck, build, responsive, a11y, contrast,
  semantic-token, evidence, and source audits.
- Browser: production build with real API/database; click, type, select, merge,
  transition, assign, open/close drawers, create/delete projects, clear an
  avatar, and verify 409 recovery. Inspect desktop/phone × light/dark output.
- Review the complete diff for tenant leakage, stale optimistic state, duplicate
  execution enqueue, projection count/cursor errors, accessibility regressions,
  secrets, unsupported assets, and untested branches.
- Before publishing, set the repository identity to
  `cnwenf <cnwenf@outlook.com>`, disable commit hooks that add trailers, verify
  author/committer and commit body, push the issue branch, open a ready PR, and
  hand it to the serialized merge queue without merging it locally.

