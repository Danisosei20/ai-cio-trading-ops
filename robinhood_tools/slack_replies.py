from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ParsedSlackReply:
    kind: str
    value: Decimal | None
    safe_test_only: bool = True


@dataclass(frozen=True)
class SlackTransition:
    state: str
    should_reject: bool = False
    needs_fresh_review: bool = False


def parse_safe_reply(text: str) -> ParsedSlackReply:
    normalized = " ".join(text.strip().upper().split())
    combined_dollar = re.fullmatch(r"YES\s*[,;:-]?\s*\$([0-9]+(?:\.[0-9]{1,2})?)", normalized)
    if combined_dollar:
        return ParsedSlackReply("dollar_amount", Decimal(combined_dollar.group(1)))
    combined_shares = re.fullmatch(
        r"YES\s*[,;:-]?\s*([0-9]+(?:\.[0-9]{1,6})?)\s+SHARES?", normalized
    )
    if combined_shares:
        return ParsedSlackReply("share_quantity", Decimal(combined_shares.group(1)))
    if normalized == "YES":
        return ParsedSlackReply("yes_request_sizing", None)
    if normalized == "NO":
        return ParsedSlackReply("reject", None)
    dollar_choice = re.fullmatch(r"\$([0-9]+(?:\.[0-9]{1,2})?)", normalized)
    if dollar_choice:
        return ParsedSlackReply("dollar_amount", Decimal(dollar_choice.group(1)))
    share_choice = re.fullmatch(r"([0-9]+(?:\.[0-9]{1,6})?) SHARES?", normalized)
    if share_choice:
        return ParsedSlackReply("share_quantity", Decimal(share_choice.group(1)))
    dollar = re.fullmatch(r"TEST SIZE \$([0-9]+(?:\.[0-9]{1,2})?)", normalized)
    if dollar:
        return ParsedSlackReply("dollar_amount", Decimal(dollar.group(1)))
    shares = re.fullmatch(r"TEST SHARES ([0-9]+(?:\.[0-9]{1,6})?)", normalized)
    if shares:
        return ParsedSlackReply("share_quantity", Decimal(shares.group(1)))
    if normalized == "TEST REJECT":
        return ParsedSlackReply("reject", None)
    if "APPROVE" in normalized or normalized.startswith(("BUY ", "SELL ")):
        return ParsedSlackReply("execution_blocked", None)
    return ParsedSlackReply("unrecognized", None)


def reply_acknowledgement(parsed: ParsedSlackReply) -> str:
    if parsed.kind == "dollar_amount":
        result = f"Sizing parsed: dollar amount **${parsed.value}**. Return to Codex for affordability checks and broker review."
    elif parsed.kind == "share_quantity":
        result = f"Sizing parsed: share quantity **{parsed.value}**. Return to Codex for affordability checks and broker review."
    elif parsed.kind == "yes_request_sizing":
        result = "**YES received.** Reply with an exact amount such as `$50` or `0.25 shares`."
    elif parsed.kind == "reject":
        result = "**NO received.** The linked pending approval may be rejected; no order will be placed."
    elif parsed.kind == "execution_blocked":
        result = "Execution-like Slack command was **blocked**."
    else:
        result = "Reply was not recognized as a supported safe test command."
    return result + " Slack cannot approve or place a trade. Real actions require Codex."


def transition_for_reply(parsed: ParsedSlackReply) -> SlackTransition:
    if parsed.kind == "reject":
        return SlackTransition("rejected", should_reject=True)
    if parsed.kind == "yes_request_sizing":
        return SlackTransition("awaiting_size")
    if parsed.kind in {"dollar_amount", "share_quantity"}:
        return SlackTransition("fresh_review_required", needs_fresh_review=True)
    if parsed.kind == "execution_blocked":
        return SlackTransition("blocked")
    return SlackTransition("unrecognized")
