"""Shared job-link identity helper.

A job posting's "link" is the pipeline's identity for it throughout scoring, tailoring,
research, and Sheet logging (see CLAUDE.md). LinkedIn (and similar sources) append a
different tracking query string (trackingId, position, refId, pageNum) to the same
posting's URL on every fresh search result -- so re-scraping the same job on a
different day produces a different *exact* link string, which would otherwise defeat
exact-string dedup across separate runs and cause duplicate Drive folders / Sheet rows
for a posting that was already processed.

canonical_link() strips the query string and fragment, keeping scheme+host+path --
where a job board's own unique posting id actually lives -- so the same posting always
canonicalizes to the same key regardless of which day's search result it came from.
Used as the dedup key (never as what's stored/displayed -- the full original link with
its tracking params is still what's written to output and the Sheet, since it's still a
valid, clickable URL to the posting).
"""
from urllib.parse import urlsplit, urlunsplit


def canonical_link(url):
    if not url:
        return url
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
