"""Derive 'original page' viewing metadata from a Powabase source object.

True per-page renders exist only for UPLOADED documents (PDFs → one PNG per page via
PyMuPDF). A scraped-URL source's ``derivatives["image"]`` entries are embedded content
images pulled out of the page markdown (all tagged ``page: 1``), NOT a visual render of
the page — those are deliberately excluded so the viewer never presents them as
"original pages" and misrepresents the document.
"""

from typing import Any


def build_page_meta(source: dict[str, Any]) -> dict[str, Any]:
    """Return ``{has_page_images, page_count, pages: [{index, page, width, height}]}``.

    ``index`` is the position in ``derivatives["image"]`` (what the BaaS download
    endpoint keys on); ``page`` is the 1-indexed page number for display order.
    """
    derivatives = source.get("derivatives") or {}
    images = derivatives.get("image") or []
    auto = source.get("auto_metadata") or {}
    # URL sources: embedded content images, not page renders → not "original pages".
    if auto.get("source_type") == "url" or not images:
        return {"has_page_images": False, "page_count": 0, "pages": []}

    pages = []
    for i, deriv in enumerate(images):
        meta = deriv.get("metadata") or {}
        pages.append(
            {
                "index": i,
                "page": deriv.get("page") or (i + 1),
                "width": meta.get("width"),
                "height": meta.get("height"),
            }
        )
    pages.sort(key=lambda p: p["page"])
    return {"has_page_images": True, "page_count": len(pages), "pages": pages}
