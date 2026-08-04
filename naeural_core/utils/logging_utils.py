import re


CLOUDFLARE_LOG_REDACTION = "[REDACTED]"

_CLOUDFLARED_TOKEN_ARGUMENT_RE = re.compile(
  r"(--token(?:\s+|=))(?:\\\s*)?(?:\"[^\"]*\"|'[^']*'|[^\s;&|]+)",
  flags=re.IGNORECASE,
)
_CLOUDFLARE_TOKEN_ENV_RE = re.compile(
  r"((?:EE_)?CLOUDFLARE_TOKEN(?:_[A-Z0-9_]+)?=|CF_TUNNEL_TOKEN=)"
  r"(?:\"[^\"]*\"|'[^']*'|[^\s;&|]+)",
  flags=re.IGNORECASE,
)


def _is_cloudflare_token_key(key):
  normalized = str(key).strip().upper().replace("-", "_")
  return (
    normalized in {
      "CLOUDFLARE_TOKEN",
      "CF_TUNNEL_TOKEN",
      "EE_CLOUDFLARE_TOKEN",
    }
    or normalized.startswith("EE_CLOUDFLARE_TOKEN_")
  )


def redact_cloudflare_tokens(value):
  """
  Return a detached, log-safe copy with Cloudflare tunnel tokens redacted.

  Parameters
  ----------
  value : object
      Structured configuration data, a command string, or a collection of
      either. Command redaction is limited to ``cloudflared`` invocations.

  Returns
  -------
  object
      A value with the same collection shape and redacted Cloudflare tokens.
  """
  if isinstance(value, dict):
    return {
      key: (
        CLOUDFLARE_LOG_REDACTION
        if _is_cloudflare_token_key(key) and item
        else redact_cloudflare_tokens(item)
      )
      for key, item in value.items()
    }
  if isinstance(value, (list, tuple)):
    redacted = [redact_cloudflare_tokens(item) for item in value]
    is_cloudflared_argv = any(
      isinstance(item, str) and "cloudflared" in item.lower()
      for item in value
    )
    if is_cloudflared_argv:
      redact_next = False
      for idx, item in enumerate(value):
        if redact_next:
          redacted[idx] = CLOUDFLARE_LOG_REDACTION
          redact_next = False
          continue
        if not isinstance(item, str):
          continue
        redacted[idx] = _CLOUDFLARE_TOKEN_ENV_RE.sub(
          lambda match: match.group(1) + CLOUDFLARE_LOG_REDACTION,
          redacted[idx],
        )
        if item.lower() == "--token":
          redact_next = True
        elif item.lower().startswith("--token="):
          redacted[idx] = item.split("=", 1)[0] + "=" + CLOUDFLARE_LOG_REDACTION
    return tuple(redacted) if isinstance(value, tuple) else redacted
  if isinstance(value, str) and "cloudflared" in value.lower():
    redacted = _CLOUDFLARED_TOKEN_ARGUMENT_RE.sub(
      lambda match: match.group(1) + CLOUDFLARE_LOG_REDACTION,
      value,
    )
    return _CLOUDFLARE_TOKEN_ENV_RE.sub(
      lambda match: match.group(1) + CLOUDFLARE_LOG_REDACTION,
      redacted,
    )
  return value
