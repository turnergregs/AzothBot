"""Search across cards, aspects and rites.

Mirrors the Codex's `ContentSearch._matches_query` (scripts/UI/codex/
content_search.gd in the azoth repo) so the same query means the same thing in
both places. The valuable part is the **deep search**: `actions`, `triggers` and
`properties` are JSON, so `query="Magnify"` finds every card carrying that
property even though the word never appears in its rules text.

Pure functions over dicts -- no network, no rendering. The command layer fetches
and draws.
"""
from __future__ import annotations

# Content types, in the vocabulary the commands use. "rite" is what the database
# still calls an "event"; see azoth_commands/rites.py.
KINDS = ("card", "aspect", "rite")
KIND_TABLE = {"card": "cards", "aspect": "aspects", "rite": "events"}

ELEMENTS = ("blood", "sol", "anima")
COLOURLESS = "colourless"           # a card with element = null

# Fields the free-text query scans directly, mirroring the Codex.
_TEXT_FIELDS = ("name", "text", "type", "flavor")


def _deep_contains(value, needle: str) -> bool:
    """Recursive substring search through nested JSON.

    Action/trigger/property payloads are arbitrarily nested dicts and lists, and
    the useful queries ("Draw", "Magnify", "{link.size}") live in their keys and
    values rather than in any flat column.
    """
    if isinstance(value, dict):
        return any(_deep_contains(k, needle) or _deep_contains(v, needle)
                   for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(_deep_contains(v, needle) for v in value)
    return needle in str(value).lower()


def matches_query(item: dict, query: str) -> bool:
    """True when `query` appears anywhere the Codex would look."""
    if not query:
        return True
    needle = query.strip().lower()
    if not needle:
        return True

    for field in _TEXT_FIELDS:
        if needle in str(item.get(field) or "").lower():
            return True
    for subtype in item.get("subtypes") or []:
        if needle in str(subtype).lower():
            return True
    for field in ("valence", "attunement", "foresight"):
        if item.get(field) is not None and needle in str(item[field]).lower():
            return True
    for field in ("actions", "triggers", "properties"):
        if _deep_contains(item.get(field) or [], needle):
            return True
    return False


def _element_matches(item: dict, element: str) -> bool:
    actual = item.get("element")
    if element == COLOURLESS:
        return actual in (None, "", "default")
    return str(actual or "").lower() == element.lower()


def has_action(item: dict, action: str) -> bool:
    """True when the item runs a named action, at any nesting depth.

    Actions nest: a Split action carries sub-actions, a trigger carries its own
    list. A top-level scan would miss most of them.
    """
    target = action.strip().lower()

    def walk(node) -> bool:
        if isinstance(node, dict):
            if str(node.get("name") or "").lower() == target:
                return True
            return any(walk(v) for v in node.values())
        if isinstance(node, (list, tuple)):
            return any(walk(v) for v in node)
        return False

    return walk(item.get("actions") or []) or walk(item.get("triggers") or [])


def matches(item: dict, kind: str, *, query=None, content_type=None, element=None,
            valence=None, subtype=None, card_type=None, action=None) -> bool:
    """Every filter is ANDed; an unset filter is ignored."""
    if content_type and kind != content_type:
        return False
    if element and not _element_matches(item, element):
        return False
    if valence is not None and item.get("valence") != valence:
        return False
    if subtype:
        wanted = subtype.strip().lower()
        if not any(str(s).lower() == wanted for s in item.get("subtypes") or []):
            return False
    if card_type and str(item.get("type") or "").lower() != card_type.lower():
        return False
    if action and not has_action(item, action):
        return False
    return matches_query(item, query)


# Sort keys. `element` uses a canonical order rather than alphabetical, matching
# the Codex's ELEMENT_SORT_ORDER -- alphabetising elements is meaningless.
_ELEMENT_ORDER = {"anima": 0, "blood": 1, "sol": 2, "default": 3, None: 4, "": 4}


def sort_key(sort: str):
    if sort == "valence":
        # Null valence (catalysts) sorts last rather than first.
        return lambda r: (r[0].get("valence") is None, r[0].get("valence") or 0,
                          str(r[0].get("name") or ""))
    if sort == "element":
        return lambda r: (_ELEMENT_ORDER.get(r[0].get("element"), 9),
                          str(r[0].get("name") or ""))
    return lambda r: str(r[0].get("name") or "").lower()


def search(pool, sort: str = "name", **filters):
    """Filter and sort `pool`, a sequence of (item, kind) pairs."""
    hits = [(item, kind) for item, kind in pool if matches(item, kind, **filters)]
    hits.sort(key=sort_key(sort))
    return hits


def describe(filters: dict) -> str:
    """A short human summary of the active filters, for the reply line."""
    parts = []
    if filters.get("query"):
        parts.append(f'"{filters["query"]}"')
    for key in ("content_type", "element", "card_type", "subtype", "action"):
        if filters.get(key):
            parts.append(f"{key.replace('_', ' ')}: {filters[key]}")
    if filters.get("valence") is not None:
        parts.append(f"valence: {filters['valence']}")
    return " · ".join(parts) if parts else "no filters"
