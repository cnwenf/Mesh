"""Global search module (search-command-palette.md §2.2/§3/§4.6).

Server-side object search over the six searchable resource types
(issue / member / agent / project / view / chat_session) with in-query
visibility filtering, a layered scoring ladder and keyset paging through a
signed, query-bound opaque cursor. The single normalization entry point on
the database side is ``public.mesh_search_norm`` (migration 0035); the
Python mirror in :mod:`mesh.search.norm` is used ONLY for scoring and
highlight mapping — recall always goes through the SQL function so index
and query expressions never drift.
"""
