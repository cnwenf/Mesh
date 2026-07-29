"""Analytics request conventions (analytics.md §3).

All endpoints are reads; query parameters follow the house convention —
UUIDs travel as strings parsed in routes (path → 404, query → 400),
domain validation happens in the service/scope layer.
"""
