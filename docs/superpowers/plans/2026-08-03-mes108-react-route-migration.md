# MES-108 React Route Migration Implementation Plan

> **For agentic workers:** Execute every task with test-first RED/GREEN cycles. Do not advance a model-card status from `pending` until the cited unit, browser, and artifact evidence exists on the current head.

**Goal:** Calibrate the real Mesh React application to the accepted MES-142 blueprint for all 24 blueprint routes, preserve the six intentional React extension families, keep real API/auth/realtime behavior intact, and close the machine-readable MES-108 reconciliation and evidence gates honestly.

**Architecture:** Freeze cross-route visual facts in the semantic token source and shared shell/pattern components before editing feature pages. Then migrate six disjoint vertical page batches that continue composing the shared design system and existing business hooks. A dedicated deterministic Playwright evidence suite must exercise the real React routes against real services, record the declared mouse/keyboard/touch actions, and capture each applicable fixed-environment visual cell through the MES-108 fixture. The external repository-owner decision remains independent and last.

**Tech stack:** React 19, React Router, TypeScript, semantic CSS tokens, Vitest, Playwright Chromium, Node 22.22.0, Python backend, PostgreSQL, Redis, WebSocket, GitHub Actions.

**Fixed inputs:**

- Blueprint revision: `b4d579f436121a92cd2684ccd9e86af41004d71d`
- Model card: `frontend/model-card/mes108-react-migration.json`
- Browser: Chromium, `zh-CN`, UTC, DPR 1, animations disabled
- Viewports: `390x844`, `1440x900`
- Themes: light, dark
- States: default, loading, empty, error

**Runtime:** Prefix every npm/Playwright command with a PATH whose first entry is `/opt/node22/bin`; the system default Node 20 is unsupported.

---

## Task 1: Freeze the honest starting state

**Files:**

- Verify: `frontend/model-card/mes108-react-migration.json`
- Verify: `docs/specs/frontend/mes108-reconciliation/react-migration-model-card.md`

- [ ] Run the model-card audit and its complete Node test suite on Node 22.
- [ ] Record the exact unresolved decomposition in the plan/run notes: 28 reconciliation, 6 states, 30 interactions, 28 visual groups, 5 components, 42 tokens, and 5 calibration risks.
- [ ] Assert that `frontend/e2e/evidence/mes108/` is absent and that release mode fails closed. This is the required RED baseline, not a defect to bypass.

## Task 2: Calibrate the semantic token source with TDD

**Files:**

- Modify: `frontend/src/design/tokenValues.ts`
- Regenerate: `frontend/src/design/tokens.css`
- Regenerate: `frontend/src/design/tokens-dark.css`
- Regenerate: `frontend/src/design/tokens-print.css`
- Modify: `frontend/src/design/__tests__/tokens.test.ts`
- Modify: `frontend/model-card/mes108-react-migration.json`

- [ ] Add failing assertions for the accepted light/dark surface hierarchy, neutral primary action, distinct brand accent, text/border hierarchy, shadow levels, radii, 256px wide sidebar, 48px page bar, motion curve, and Inter/monospace stacks.
- [ ] Where two blueprint facts currently collapse into one React token, introduce distinct semantic tokens rather than making selectors depend on raw values. At minimum keep shell/canvas, line/input-line, primary/brand, and disabled/secondary-text meanings separable.
- [ ] Run the focused token test and confirm RED before implementation.
- [ ] Change only `tokenValues.ts`, regenerate all token CSS, and run focused tests to GREEN.
- [ ] Run both theme contrast gates. If an exact fact violates WCAG, retain an honest pending calibration risk and document the smallest accessible deviation; never weaken the threshold.
- [ ] Mark only token mappings proven by the test and contrast output as `calibrated`.

## Task 3: Calibrate App Shell and shared patterns with TDD

**Files:**

- Modify: `frontend/src/shell/AppShell.tsx`
- Modify: `frontend/src/shell/Sidebar.tsx`
- Modify: `frontend/src/shell/TopBar.tsx`
- Modify: `frontend/src/shell/MobileNav.tsx`
- Modify: `frontend/src/shell/shell.css`
- Modify: `frontend/src/design/components/PageHeader.tsx`
- Modify: `frontend/src/design/components/components.css`
- Modify: `frontend/src/design/patterns/patterns.css`
- Modify: `frontend/src/shortcuts/shortcuts.css`
- Modify: shell/design tests adjacent to those files
- Modify: `frontend/model-card/mes108-react-migration.json`

- [ ] Add failing structure tests for a 256px wide desktop shell, stage surface, workspace/search/create controls, 48px page bar, and a 720px compact transition with equivalent mobile navigation.
- [ ] Add failing tests that prove search, workspace switching, connection status, inbox, help, keyboard shortcuts, skip link, and reduced-motion behavior remain reachable after topology changes.
- [ ] Implement the shell through shared components; do not duplicate a page shell in feature CSS.
- [ ] Calibrate `surface-card` and `command-palette`; keep focus trapping, keyboard selection, and live regions intact.
- [ ] Run shell/design focused suites to GREEN, then their coverage with every changed module at or above 90% lines, branches, and functions.
- [ ] Mark `app-shell`, `surface-card`, `command-palette`, and the corresponding risks calibrated only after the tests pass.

## Task 4: Batch A — public flows and entry pages

**Owns only:** `login`, `register`, `code`, and `home-and-workspace-entry`.

**Files:**

- Modify the page/component/CSS/tests already listed for those model-card entries.
- Add: `frontend/e2e/real-mes108-public-entry.spec.ts`

- [ ] Add failing DOM/state tests for the accepted public-flow hierarchy and all mapped default/loading/error states.
- [ ] Calibrate the shared `PublicFlowShell`, then page-specific composition without changing auth, safe-return, invitation, device-code, password-recovery, or OAuth contracts.
- [ ] Exercise the five declared interactions with real browser input APIs and real API responses.
- [ ] Cover 52 applicable visual cells; retain the 12 declared N/A cells with their existing reasons.

## Task 5: Batch B — issue workflow

**Owns only:** `my`, `issues`, `board`, and `issue`.

**Files:**

- Modify issue/board/comments page CSS/components/tests listed in the model card.
- Add: `frontend/e2e/real-mes108-issues.spec.ts`

- [ ] Add failing state tests for the missing `my` loading/empty/error evidence.
- [ ] Calibrate DataView/list/board/detail surfaces while preserving real filters, quick create, optimistic rollback, drag threshold, keyboard move, touch move sheet, comments, attachments, and realtime reconciliation.
- [ ] Exercise all five declared interactions through mouse, keyboard, and touch paths as declared.
- [ ] Cover all 64 visual cells.

## Task 6: Batch C — collaboration and roster

**Owns only:** `inbox`, `chat`, `members`, `agents`, `agent`, and `member-detail`.

**Files:**

- Modify collaboration/member/agent files and tests listed in the model card.
- Add: `frontend/e2e/real-mes108-collaboration-roster.spec.ts`

- [ ] Add failing tests for the missing Agent-filter loading/empty/error states.
- [ ] Calibrate desktop split views and mobile list/detail routing without replacing API, streaming, attachment, role-change, or runtime behavior.
- [ ] Exercise all six declared interactions with the declared input modes.
- [ ] Cover 92 applicable visual cells and preserve four N/A cells.

## Task 7: Batch D — projects

**Owns only:** `projects`, `project`, and `cycles`.

**Files:**

- Modify project page/CSS/tests listed in the model card.
- Add: `frontend/e2e/real-mes108-projects.spec.ts`

- [ ] Convert repeated page chrome to the shared page patterns before page-specific styling.
- [ ] Preserve real project creation, health update, tabs, settings, filters, and realtime updates.
- [ ] Exercise the three declared interactions and cover all 48 visual cells.

## Task 8: Batch E — platform capabilities

**Owns only:** `skills`, `skill`, `autopilot/automations`, `squads`, `runtimes`, `integrations`, and `approvals`.

**Files:**

- Modify only the feature files/tests listed by those model-card entries.
- Add: `frontend/e2e/real-mes108-platform.spec.ts`

- [ ] Calibrate shared monitoring/detail/settings patterns; finish `progress` within runtimes.
- [ ] Preserve real create/edit/toggle/test-run, skill authorization, squad movement, runtime logs/reconnect, integration configuration, and approval persistence.
- [ ] Exercise all seven declared interactions.
- [ ] Cover 108 applicable visual cells and preserve four N/A cells.

## Task 9: Batch F — management and global finish

**Owns only:** `usage/analytics`, `settings`, `states`, and `not-found`.

**Files:**

- Modify analytics/settings/styleguide/not-found files and tests listed in the model card.
- Add: `frontend/e2e/real-mes108-management.spec.ts`

- [ ] Finish `kpi-card` calibration within analytics.
- [ ] Preserve settings permissions, theme negotiation, notifications, security, audit, danger confirmation, and the public styleguide fixture.
- [ ] Exercise all four declared interactions.
- [ ] Cover 48 applicable visual cells and preserve 16 N/A cells.

## Task 10: Produce real current-head browser evidence

**Files:**

- Modify: `frontend/playwright.mes108.config.ts`
- Use: `frontend/e2e/mes108-evidence-fixture.mjs`
- Add/modify: the six MES-108 E2E specs above
- Add: `frontend/e2e/evidence/mes108/baselines/*.png`
- Add: `frontend/e2e/evidence/mes108/*.png`
- Modify: `frontend/model-card/mes108-react-migration.json`

- [ ] Start PostgreSQL, Redis, backend, and frontend with strong unique credentials, loopback-only application exposure, no host-published data-store ports, Redis protected mode, and a restricted application database role.
- [ ] Seed deterministic real data through supported APIs; do not mock persistence or WebSocket results.
- [ ] Run every exact evidence test under `playwright.mes108.config.ts`; use concrete `mouse`, `keyboard`, and `touchscreen` APIs so the reporter can attest the declared modes.
- [ ] Capture every claimed actual through `mes108Screenshot.capture(path)` and compare against independently reviewed baselines with RGBA exact comparison.
- [ ] Do not create a baseline by copying the same actual capture. A baseline must come from the accepted target and be reviewed as such.
- [ ] Bind artifact hashes, dimensions, spec, exact test title, project, environment digest, and comparison result into the model card.
- [ ] Run the runtime evidence producer against the exact current head and prove 30/30 interactions plus 412/412 applicable visual cells.

## Task 11: Reconcile the model card and documentation

**Files:**

- Modify: `frontend/model-card/mes108-react-migration.json`
- Regenerate: `docs/specs/frontend/mes108-reconciliation/react-migration-model-card.md`
- Modify: `frontend/model-card/README.md`
- Modify: `README.md`
- Modify: affected feature Specs

- [ ] Mark each page/extension `calibrated` or `reused` only when its implementation, state, interaction, and visual evidence is complete.
- [ ] Resolve all five component records and all six calibration risks with evidence-backed notes.
- [ ] Regenerate the Markdown review document and fail on drift.
- [ ] Run audit mode and confirm zero unresolved implementation/evidence records.
- [ ] Run release mode without owner approval and confirm it fails only on the independent repository-owner decision.

## Task 12: Full verification, independent review, and PR update

- [ ] Run frontend full coverage and per-file 90% gates, typecheck, production build, ESLint, Stylelint, Prettier, contrast, visual, and MES-108 evidence suites.
- [ ] Run backend unit coverage and real API/database/WebSocket E2E affected by the browser journeys; both overall and changed modules must remain at least 90%.
- [ ] Run clean-room source, asset/license, secret, commit-message, and reference scans with zero findings.
- [ ] Request an independent review of `origin/main...HEAD`, focused on behavior preservation, evidence integrity, accessibility, visual reconciliation, and release-gate integrity. Fix every actionable finding with a fresh RED/GREEN cycle.
- [ ] Commit as `cnwenf <cnwenf@outlook.com>`, verify author and committer, and verify no co-author trailer in every outgoing commit.
- [ ] Push fast-forward to `agent/mesh/0f518724`, update draft PR #120, and re-read the real PR/check state.
- [ ] Ask the repository owner for the exact current-head/card/baseline decision only after all machine and human evidence is frozen. Never reuse an approval command from an older head.
