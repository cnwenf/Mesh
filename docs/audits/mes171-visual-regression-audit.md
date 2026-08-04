# MES-171 visual regression audit

## Scope and decision rule

The failed CI artifact was reviewed from the `expected`, `actual`, and `diff`
images before any snapshot was replaced. A snapshot was accepted only when the
rendered state retained its required content, hierarchy, controls, theme
semantics, and responsive behavior. A mismatch that exposed a rendering defect
was repaired first and re-rendered before its baseline was updated.

The reviewed manifest contains exactly 62 changed PNG files:

| Suite                        | Exact manifest                                                                                                         |  Count | Review result                                                                                                                                                                                                                  |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------- | -----: | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Core themes                  | `theme-visual.spec.ts-snapshots/{desktop,tablet,wide,phone}/{members,inbox,autopilots}-{light,dark}.png`               |     24 | Intended surface, spacing, border, control, and token changes were retained. Page-title typography was repaired before capture. The phone Autopilot table was repaired into labelled cards.                                    |
| Component fixture            | `styleguide.spec.ts-snapshots/{desktop,tablet,wide,phone}/styleguide-{light,dark}.png`                                 |      8 | All component states and both themes remained present. Invalid typography custom-property references were repaired before capture.                                                                                             |
| Members exceptional states   | `state-matrix.spec.ts-snapshots/state-phone/members-{empty,error,long,offline,permission}-{light,dark}.png`            |     10 | Empty/error actions, long-content wrapping, reconnect state, and permission boundary remained explicit; title hierarchy was repaired before capture. Existing loading baselines already matched and were not changed.          |
| Inbox exceptional states     | `state-matrix.spec.ts-snapshots/state-phone/inbox-{loading,empty,long,offline}-{light,dark}.png`                       |      8 | Skeleton, empty action, long content, and reconnect banner retained correct semantics; title hierarchy was repaired before capture. Existing error and permission baselines already matched and were not changed.              |
| Autopilot exceptional states | `state-matrix.spec.ts-snapshots/state-phone/autopilots-{loading,empty,error,long,offline,permission}-{light,dark}.png` |     12 | The loading root was restored as the visible page container. Long/offline table content now uses labelled cards and wraps unbroken content. Error, empty, permission, actions, status colors, and both themes remained intact. |
| **Total**                    |                                                                                                                        | **62** | **62 reviewed; 62 accepted after required implementation repairs.**                                                                                                                                                            |

## Rendering defects repaired before baseline capture

1. Shared component and styleguide CSS referenced obsolete typography custom
   properties. The invalid declarations made page and fixture headings inherit
   the wrong size. They now use the generated Mesh type-scale variables, with a
   static regression test rejecting the obsolete variable forms.
2. The Autopilot loading state left `data-testid="autopilots-page"` on a
   screen-reader-only sibling instead of the visible content root. The visible
   root now owns the test identifier and contains loading, error, empty, and
   table states.
3. The seven-column Autopilot table compressed into unreadable vertical text on
   the 390px viewport. At the phone container breakpoint, rows now become
   labelled cards; every data cell has its matching header label, long
   unbroken values wrap, and action buttons remain reachable.

## Per-state checks

- Light and dark captures were checked independently; semantic status colors,
  borders, raised surfaces, focusable controls, and primary/secondary action
  hierarchy remain distinguishable.
- Desktop, tablet, wide, and phone captures retain every filter, search field,
  state control, table/card field, and action that belongs to the fixture.
- Loading, empty, error, long, offline, and permission states were compared as
  separate renders. No state was accepted by extrapolating from another state.
- The phone Autopilot normal, long, and offline renders were additionally
  inspected at original resolution. All seven labels are readable, the long
  identifier wraps without horizontal overflow, and both row actions remain
  visible.
- The normal-state Autopilot mask covers only the changing relative-time value,
  not its table cell or phone-card label, so the seven-field structure remains
  part of the pixel comparison.

## Reproduction

The snapshot comparison remains strict at `maxDiffPixelRatio: 0.01`. The visual
server ports can be isolated for parallel local verification without changing
the CI defaults:

```bash
MESH_MOCK_VISUAL_PORT=18911 MESH_VISUAL_FRONTEND_PORT=15199 \
  npm run test:e2e:visual
```

## Verification result

- Full visual gate: 424 passed, 44 declared skips, 0 failed.
- Browser accessibility gate: 136 passed, 10 declared skips, 0 failed,
  including full-route reflow, 320/390px and 200% equivalent overflow checks,
  and coarse-pointer target sizing.
- Browser interaction gate: 86 passed, 8 declared skips, 0 failed.
- MES-160 production-auth stack: 5 passed, 3 project-scoped skips, 0 failed;
  tenant-scoped REST, WebSocket, SSE, and PostgreSQL assertions ran against the
  isolated stack. Its containers, network, volumes, and generated credential
  file were removed after the run.
- Unit coverage: 98.66% lines/statements, 96.79% functions, and 93.96%
  branches; the changed-source per-file 90% gate passed.
