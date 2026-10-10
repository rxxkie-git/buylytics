"""Webhook signature verification.

Standard library only. No FastAPI, no SQLAlchemy — verification is the security
boundary of the whole webhook path, so it is kept in a module that can be
exhaustively unit-tested anywhere, including an environment with nothing
installed.

The scheme
----------
The sender computes::

    signature = HMAC_SHA256(secret, f"{business_id}.{timestamp}.{raw_body}")

and sends it hex-encoded in the signature header, with ``timestamp`` in the
timestamp header and ``business_id`` in the business header.

Signing the **business id** alongside the body is the important part. A webhook
carries no Clerk session, so the tenant has to come from the request itself — and
an unauthenticated header would be trivially forgeable. Because the id is inside
the signed material, a caller cannot claim to be a different tenant without
holding the shared secret. The tenant is derived from verified data, never
blindly trusted.

Signing the **timestamp** bounds replay. A captured request stays valid only for
``tolerance_seconds``; after that the same bytes are rejected. The
``webhook_events`` unique constraint then handles replays inside the window.

What this module never does
---------------------------
* Return, log or embed the secret, or the expected signature, in any output. A
  rejection says *why* in a coarse machine-readable code and nothing more — an
  error message that echoed the expected value would hand an attacker the answer.
* Use ``==`` on signatures. Every comparison goes through
  :func:`hmac.compare_digest`.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timezone

#: Only algorithm accepted. Named in the header as ``sha256=<hex>``.
SIGNATURE_SCHEME = "sha256"

#: Length of a hex-encoded SHA-256 digest.
_DIGEST_HEX_LENGTH = 64

# Rejection codes. Coarse on purpose: enough for an operator to debug a
# misconfigured sender, not enough to narrow down a forgery attempt.
REASON_OK = "ok"
REASON_MISSING_SECRET = "webhook_secret_not_configured"
REASON_MISSING_SIGNATURE = "missing_signature"
REASON_MISSING_TIMESTAMP = "missing_timestamp"
REASON_MALFORMED_SIGNATURE = "malformed_signature"
REASON_MALFORMED_TIMESTAMP = "malformed_timestamp"
REASON_TIMESTAMP_OUT_OF_RANGE = "timestamp_out_of_range"
REASON_SIGNATURE_MISMATCH = "signature_mismatch"


@dataclass(frozen=True)
class SignatureCheck:
    """Outcome of verifying one delivery.

    ``detail`` is safe to return over HTTP: it describes the shape of the
    problem, never the expected value.
    """

    verified: bool
    reason: str = REASON_OK
    detail: str | None = None

    def __bool__(self) -> bool:
        return self.verified


def canonical_message(business_id: str, timestamp: str, body: bytes) -> bytes:
    """Build the exact byte string that gets signed.

    The raw request body is used verbatim. Re-serialising parsed JSON would
    change whitespace and key order and break every signature, so the caller
    must pass the bytes as received.
    """
    prefix = f"{business_id}.{timestamp}.".encode("utf-8")
    return prefix + (body or b"")


def compute_signature(secret: str, business_id: str, timestamp: str, body: bytes) -> str:
    """Hex HMAC-SHA256 over the canonical message."""
    return hmac.new(
        (secret or "").encode("utf-8"),
        canonical_message(business_id, timestamp, body),
        hashlib.sha256,
    ).hexdigest()


def signature_header_value(secret: str, business_id: str, timestamp: str, body: bytes) -> str:
    """Header value a well-behaved sender would send. Used by tests and docs."""
    digest = compute_signature(secret, business_id, timestamp, body)
    return f"{SIGNATURE_SCHEME}={digest}"


def parse_signature_header(raw: str | None) -> list[str]:
    """Extract candidate hex digests from a signature header.

    Accepts, in order of preference:

    * ``sha256=<hex>``
    * bare ``<hex>``
    * a comma-separated list of either, so a sender rotating secrets can offer
      two digests and have one of them accepted

    Anything that is not a well-formed hex digest of the right length is dropped
    here rather than being fed to the comparison.
    """
    if not raw:
        return []

    candidates: list[str] = []

    for part in str(raw).split(","):
        token = part.strip()

        if not token:
            continue

        if "=" in token:
            scheme, _, value = token.partition("=")
            if scheme.strip().lower() not in (SIGNATURE_SCHEME, "v1"):
                continue
            token = value.strip()

        token = token.lower()

        if len(token) != _DIGEST_HEX_LENGTH:
            continue

        try:
            int(token, 16)
        except ValueError:
            continue

        candidates.append(token)

    return candidates


def parse_timestamp(raw: str | None) -> datetime | None:
    """Parse a timestamp header as Unix epoch seconds or ISO-8601.

    Returns ``None`` if it cannot be understood. Both forms are common among
    webhook senders and supporting both costs nothing.
    """
    if raw is None:
        return None

    text = str(raw).strip()

    if not text:
        return None

    try:
        return datetime.fromtimestamp(float(text), tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        pass

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def check_timestamp(
    raw: str | None,
    *,
    tolerance_seconds: int,
    now: datetime | None = None,
) -> SignatureCheck:
    """Reject a delivery whose timestamp is outside the replay window.

    The window is symmetric. A timestamp far in the future is refused as well as
    one far in the past: a sender with a badly skewed clock is a
    misconfiguration, and accepting it would silently widen the replay window.
    """
    parsed = parse_timestamp(raw)

    if parsed is None:
        return SignatureCheck(
            verified=False,
            reason=REASON_MALFORMED_TIMESTAMP if raw else REASON_MISSING_TIMESTAMP,
            detail="Timestamp header must be Unix epoch seconds or an ISO-8601 datetime.",
        )

    reference = now or datetime.now(tz=timezone.utc)

    if reference.tzinfo is None:  # pragma: no cover - defensive
        reference = reference.replace(tzinfo=timezone.utc)

    skew = abs((reference - parsed).total_seconds())
    tolerance = max(0, int(tolerance_seconds))

    if skew > tolerance:
        return SignatureCheck(
            verified=False,
            reason=REASON_TIMESTAMP_OUT_OF_RANGE,
            detail=f"Timestamp is outside the accepted window of {tolerance}s.",
        )

    return SignatureCheck(verified=True)


def verify(
    secret: str,
    *,
    business_id: str,
    timestamp: str | None,
    body: bytes,
    provided_signature: str | None,
    tolerance_seconds: int,
    now: datetime | None = None,
) -> SignatureCheck:
    """Verify one delivery end to end.

    Order matters: the cheap structural checks run before the HMAC so a flood of
    malformed requests cannot be used to make the server do cryptographic work.
    """
    if not secret:
        return SignatureCheck(
            verified=False,
            reason=REASON_MISSING_SECRET,
            detail="Webhook verification is not configured on this server.",
        )

    if not business_id or not str(business_id).strip():
        return SignatureCheck(
            verified=False,
            reason=REASON_MISSING_SIGNATURE,
            detail="Business header is required for webhook verification.",
        )

    if not provided_signature:
        return SignatureCheck(
            verified=False,
            reason=REASON_MISSING_SIGNATURE,
            detail="Signature header is missing.",
        )

    candidates = parse_signature_header(provided_signature)

    if not candidates:
        return SignatureCheck(
            verified=False,
            reason=REASON_MALFORMED_SIGNATURE,
            detail=(
                f"Signature header must contain a hex-encoded HMAC-SHA256 digest, "
                f"optionally prefixed with '{SIGNATURE_SCHEME}='."
            ),
        )

    timestamp_check = check_timestamp(
        timestamp, tolerance_seconds=tolerance_seconds, now=now
    )

    if not timestamp_check.verified:
        return timestamp_check

    expected = compute_signature(secret, str(business_id).strip(), str(timestamp).strip(), body)

    for candidate in candidates:
        # Constant-time comparison: a byte-by-byte check with early exit leaks
        # how much of a forged signature was correct, which is enough to
        # reconstruct a valid one.
        if hmac.compare_digest(candidate, expected):
            return SignatureCheck(verified=True)

    return SignatureCheck(
        verified=False,
        reason=REASON_SIGNATURE_MISMATCH,
        detail="Signature does not match the request body.",
    )
