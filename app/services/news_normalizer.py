import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.schemas.news import NewsApiArticle, NormalizationResult, NormalizedNewsItem


class NewsNormalizer:
    """Clean raw articles and build deterministic deduplication fields."""

    TRACKING_QUERY_PREFIXES = ("utm_",)
    TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}

    def normalize_batch(self, articles: list[NewsApiArticle]) -> NormalizationResult:
        seen_urls: set[str] = set()
        seen_hashes: set[str] = set()

        normalized_items: list[NormalizedNewsItem] = []
        invalid_count = 0
        duplicate_in_batch_count = 0

        for article in articles:
            normalized = self.normalize_article(article)
            if normalized is None:
                invalid_count += 1
                continue

            if normalized.url in seen_urls or normalized.content_hash in seen_hashes:
                duplicate_in_batch_count += 1
                continue

            seen_urls.add(normalized.url)
            seen_hashes.add(normalized.content_hash)
            normalized_items.append(normalized)

        return NormalizationResult(
            items=normalized_items,
            invalid_count=invalid_count,
            duplicate_in_batch_count=duplicate_in_batch_count,
        )

    def normalize_article(self, article: NewsApiArticle) -> NormalizedNewsItem | None:
        """Normalize one article. Return None if title or URL is unusable."""
        title = self._clean_text(article.title)
        normalized_url = self._normalize_url(article.url)

        if not title or not normalized_url:
            return None

        description = self._clean_text(article.description)
        content = self._clean_text(article.content)
        merged_content = self._build_content(description=description, content=content)

        return NormalizedNewsItem(
            source=self._clean_text(article.source.name) or "unknown",
            title=title,
            url=normalized_url,
            content=merged_content,
            published_at=article.published_at,
            content_hash=self._build_content_hash(
                title=title,
                description=description,
                content=content,
            ),
            raw_payload=article.model_dump(mode="json", by_alias=True),
        )

    def _build_content(self, description: str | None, content: str | None) -> str | None:
        parts = [part for part in (description, content) if part]
        if not parts:
            return None
        return "\n\n".join(parts)

    def _build_content_hash(
        self,
        *,
        title: str,
        description: str | None,
        content: str | None,
    ) -> str:
        digest_input = " | ".join(
            [
                title.lower(),
                (description or "").lower(),
                (content or "").lower(),
            ]
        )
        return hashlib.sha256(digest_input.encode("utf-8")).hexdigest()

    def _clean_text(self, value: str | None) -> str | None:
        if value is None:
            return None

        cleaned = re.sub(r"\s+", " ", value).strip()
        if not cleaned:
            return None

        # NewsAPI often truncates content like "... [+123 chars]".
        cleaned = re.sub(r"\s*\[\+\d+\schars\]$", "", cleaned).strip()
        return cleaned or None

    def _normalize_url(self, value: str | None) -> str | None:
        if not value:
            return None

        parts = urlsplit(value.strip())
        if not parts.scheme or not parts.netloc:
            return None

        filtered_query = [
            (key, query_value)
            for key, query_value in parse_qsl(parts.query, keep_blank_values=True)
            if key not in self.TRACKING_QUERY_KEYS
            and not key.startswith(self.TRACKING_QUERY_PREFIXES)
        ]

        normalized_query = urlencode(filtered_query, doseq=True)
        normalized_path = parts.path or "/"

        return urlunsplit(
            (
                parts.scheme.lower(),
                parts.netloc.lower(),
                normalized_path,
                normalized_query,
                "",
            )
        )
