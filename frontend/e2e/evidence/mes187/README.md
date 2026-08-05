# MES-187 real-browser evidence

Generated on 2026-08-05 by the isolated production-auth acceptance stack:

```bash
MES187_FRONTEND_PORT=19487 npm run test:e2e:mes187
```

The run passed all four Playwright projects and used real browser interaction,
API requests, PostgreSQL, Redis, MinIO, API/gateway/worker processes, and the
production frontend build. Datastore ports were not published; the frontend
was bound to loopback only, and the stack and volumes were removed after the
run.

| Viewport/theme           | Label board                     | Agent drawer                      | Project-key disclosure                     |
| ------------------------ | ------------------------------- | --------------------------------- | ------------------------------------------ |
| Desktop 1280×800 / light | `desktop-light-label-board.png` | `desktop-light-member-drawer.png` | `desktop-light-project-key-disclosure.png` |
| Desktop 1280×800 / dark  | `desktop-dark-label-board.png`  | `desktop-dark-member-drawer.png`  | `desktop-dark-project-key-disclosure.png`  |
| Phone 390×844 / light    | `phone-light-label-board.png`   | `phone-light-member-drawer.png`   | `phone-light-project-key-disclosure.png`   |
| Phone 390×844 / dark     | `phone-dark-label-board.png`    | `phone-dark-member-drawer.png`    | `phone-dark-project-key-disclosure.png`    |

The desktop-light journey additionally verifies label-axis quick create and
dynamic realtime convergence, label merge replacement, strict status and
required-field behavior, agent assignment and same-value no-op, project-key
reservation after deletion, avatar clear/HTTPS validation, and final database
state for label links, dynamic issues, required values, deleted projects,
reserved prefixes, and the cleared avatar.
