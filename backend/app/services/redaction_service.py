import re


class RedactionService:
    """Remove credential-shaped values before evidence, prompts, or UI output persist."""

    _patterns = (
        (re.compile(r"(?i)(bearer\s+)[a-z0-9._~+/-]+"), r"\1[REDACTED]"),
        (re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"), "[REDACTED_GITHUB_TOKEN]"),
        (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "[REDACTED_API_KEY]"),
        (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"), "[REDACTED_JWT]"),
        (re.compile(r"(?i)(mongodb(?:\+srv)?://)[^\s'\"]+"), r"\1[REDACTED]"),
        (re.compile(r"(?i)(password|secret|token|api[_-]?key)\s*[:=]\s*[^\s,;]+"), r"\1=[REDACTED]"),
        (
            re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
            "[REDACTED_PRIVATE_KEY]",
        ),
    )

    def redact(self, value: str) -> str:
        result = value
        for pattern, replacement in self._patterns:
            result = pattern.sub(replacement, result)
        return result
