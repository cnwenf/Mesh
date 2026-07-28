"""Unified favorites (README §6.19 — the single pinning/star model).

Member-private rows over a polymorphic target (``issue`` / ``project`` /
``view`` / ``chat_session``). ``target_type='chat_session'`` is the UNIQUE
truth for chat pinning (chat-session.md R3 — ``chat_sessions`` carries no
``is_pinned`` snapshot). PUT is idempotent; dead targets are pruned from
list responses (soft-delete + service-layer consistency, §6.2 rule 4).
"""
