"""Application use cases: single-responsibility business operations.

Each use case represents one atomic business operation (e.g., open a trade,
close a trade, handle a broker fill). They orchestrate domain objects,
repositories, and external services without knowing about HTTP or UI details.
"""
