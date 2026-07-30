"""Search normalization — the Python mirror of ``public.mesh_search_norm``.

The database function (migration 0034, search-command-palette.md §2.2) is the
single authoritative normalizer for recall: ``lower(unaccent(normalize(t,
NFKD)))``. This module mirrors it in Python for two purposes that never touch
recall:

* scoring / match classification (:mod:`mesh.search.scoring`) — a candidate
  already recalled by SQL is ranked on its normalized title;
* highlight mapping — match offsets must map back to the ORIGINAL title's
  Unicode code points (spec §3.2: NFKD/unaccent never apply to rendering).

:func:`norm_with_map` processes the string code point by code point so every
output character remembers its source code-point index; the per-code-point
decomposition is observationally identical to whole-string NFKD for the
accent-folding we care about (combining marks are dropped either way).
"""

from __future__ import annotations

import re
import unicodedata

# Normalized query shape that routes to the identifier exact fast path
# (spec §2.2 三条查询路径:canonical uppercase equality after norm).
_IDENTIFIER_SHAPE = re.compile(r"^[a-z0-9]+-\d+$")


def search_norm(text: str) -> str:
    """NFKD + drop combining marks + casefold-free lower — mirrors the SQL fn."""
    decomposed = unicodedata.normalize("NFKD", text)
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return folded.lower()


def is_identifier_shape(norm_query: str) -> bool:
    """True when the normalized query is a complete ``KEY-N`` identifier."""
    return _IDENTIFIER_SHAPE.fullmatch(norm_query) is not None


def norm_with_map(text: str) -> tuple[str, list[int]]:
    """Normalize per code point, returning ``(norm, norm_to_orig)``.

    ``norm_to_orig[i]`` is the index (in ``list(text)`` code points) of the
    source character that produced ``norm[i]``; a highlight range over the
    normalized string maps back to the original title through it. One source
    code point may produce several output characters (NFKD compatibility
    expansions like ``ﬁ → fi``, case folds like ``İ → i̇``) — each records
    the same source index; combining marks produce none and vanish from both
    the output and the map.
    """
    norm_chars: list[str] = []
    mapping: list[int] = []
    for index, codepoint in enumerate(text):
        for decomposed in unicodedata.normalize("NFKD", codepoint):
            if unicodedata.combining(decomposed):
                continue
            for lowered in decomposed.lower():
                norm_chars.append(lowered)
                mapping.append(index)
    return "".join(norm_chars), mapping
