"""Format dispatch for library items: an EPUB (a zip) or Markdown text.

Stdlib only. culture-library serves books as EPUB and culture-rules' transcripts as
Markdown; the sync hands every fetched file to ONE extractor, so this picks by the bytes
themselves (the zip local-file magic), not by a metadata field a server could get wrong.
Both extractors yield the same :class:`Extracted` and refuse with :class:`EpubRefused`.
"""

from __future__ import annotations

from embodiment.library import markdown
from embodiment.library.epub import Extracted
from embodiment.library.epub import extract as epub_extract

#: The zip local-file header: every EPUB starts with it.
_ZIP_MAGIC = b"PK\x03\x04"


def extract_item(data: bytes) -> Extracted:
    """Zip bytes go to the EPUB extractor, anything else to the Markdown one."""
    if bytes(data[:4]) == _ZIP_MAGIC:
        return epub_extract(data)
    return markdown.extract(data)
