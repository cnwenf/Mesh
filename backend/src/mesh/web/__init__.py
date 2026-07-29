"""Web entry surface: personalized HTML entry + appearance negotiation.

theme.md §2.3 ①: the HTML document entry middleware resolves the requester's
theme negotiation chain from the HttpOnly ``mesh_session`` cookie (auth.md
session model) and injects the non-sensitive binary ``__MESH_APPEARANCE__``
value. Read-only against the session model; never issues credentials.
"""
