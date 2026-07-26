"""Comment & inbox module (comment-inbox.md — this module owns seven tables
and is the single notification authority).

Submodules:

* ``markdown`` — server-side Markdown → sanitized HTML / plain text with
  mention extraction (the server parse is authoritative, README §6.9).
* ``mentions`` — §6.9 trigger semantics: mention diff, agent execution
  enqueue through the transactional outbox, chain-depth loop protection.
* ``service`` — comment CRUD / threading / reactions / subscriptions.
* ``notifications`` — §6.13 priority matrix, outbox fan-out handler, inbox
  operations, preferences, delivery ledger, digest loop.
* ``schemas`` / ``routes`` — §3.1/§3.2 HTTP surface (§6.14 envelopes).
* ``channels`` — ``member:{id}:inbox`` realtime subscription authorization.
"""
