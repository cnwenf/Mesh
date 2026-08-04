# MES-161 global auxiliary-page evidence

These 18 screenshots come from `e2e/real-mes161-global.spec.ts`, which runs in
the production-auth Compose stack after the MES-128 keyboard journey. The
browser reaches only the loopback-bound frontend; nginx proxies requests to
the private API, gateway, PostgreSQL, Redis, and MinIO services. No Playwright
route handler or mock server is installed.

The desktop (1440×900) and touch-mobile (390×844) journeys each cover the
standalone registration entry, onboarding checklist, command palette,
Analytics empty state, permission recovery, and workspace-aware 404 recovery.
Analytics, 403, and 404 are captured in both light and dark themes. The test
also queries PostgreSQL inside the private container and asserts one registered
user, one created workspace, and one active membership for each journey.

Reproduce from the repository root:

```bash
./frontend/e2e/mes128-real/gen-stack-env.sh --force
MES128_COMPOSE_PROJECT=mes161 ./frontend/e2e/mes128-real/run-e2e.sh
```

`stack.env` is gitignored, mode 600, and contains fresh strong random
credentials. The runner removes its containers and volumes on exit.
