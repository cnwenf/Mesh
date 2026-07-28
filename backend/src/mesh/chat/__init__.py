"""Chat module (chat-session.md — 形态 A: real-time 1:1 agent chat).

Submodules:

- ``schemas`` — request bodies (§3.1-§3.2);
- ``service`` — session / message / candidate / distill orchestration;
- ``engine`` — generation runner + delta buffer + execution enqueue (§6.9);
- ``stream`` — SSE wire protocol with Last-Event-ID resume (README §6.8);
- ``channels`` — ``chat_session:{id}`` subscription authorization (§6.7);
- ``routes`` — REST + SSE endpoints (§3.1 / §3.4 / §3.5).

Form B (issue comments / mentions / notifications) is owned by the
comment-inbox module; this module only references it (distillation calls
its create-comment endpoint with ``suppress_triggers``).
"""
