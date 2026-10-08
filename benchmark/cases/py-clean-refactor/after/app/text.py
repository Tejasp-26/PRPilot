import re

_DISALLOWED = re.compile(r"[^\w -]")
_SEPARATORS = re.compile(r"[ _-]+")


def slugify(title: str) -> str:
    """Lowercase, drop symbols, turn runs of spaces/underscores/hyphens into one hyphen."""
    slug = _DISALLOWED.sub("", title.strip().lower())
    slug = _SEPARATORS.sub("-", slug)
    return slug.strip("-")
