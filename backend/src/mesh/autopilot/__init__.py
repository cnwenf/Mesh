"""Autopilot module — the AI teammate duty roster (autopilot.md).

A rule = trigger (when) + filter (whether) + ordered actions (what). The
module only DISPATCHES: execution capability comes entirely from the
executor agent (its runtime / skills / permissions). Guardrails
(rate limit / dedup / concurrency / approval gate / kill switch / cascade
depth / budgets) are first-class and default-ON.

Pieces:

* :mod:`mesh.autopilot.cron` — schedule math (5-field cron + explicit IANA
  timezone, misfire policy, next-run preview);
* :mod:`mesh.autopilot.guardrails` — the trigger-time safety gate;
* :mod:`mesh.autopilot.matcher` — outbox relay consumer mapping domain
  events (README §6.6) to rule triggers (§6.9 semantics);
* :mod:`mesh.autopilot.scheduler` — the scan-based scheduler worker
  (PostgreSQL is the ONLY scheduling source of truth, §4.5);
* :mod:`mesh.autopilot.executor` — the run executor / reconciler worker
  (action pipeline, retries, terminal-state observation of
  ``task_executions``);
* :mod:`mesh.autopilot.webhook` — inbound webhook endpoint (HMAC verify →
  dedup → audit → route);
* :mod:`mesh.autopilot.approvals` — the §6.10 approval gate for
  high-risk actions (thin layer over the unified ``approvals`` entity);
* :mod:`mesh.autopilot.service` / :mod:`mesh.autopilot.routes` — console
  API (§3.1).
"""
