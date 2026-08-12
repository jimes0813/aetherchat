"""Pure verification rules for side-effecting tool results.

The service converts a persisted tool contract and its returned payload into
one verification state plus structured evidence. It intentionally has no
database, provider, or orchestration dependencies.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

BROWSER_OPEN_TOOL_NAME = "plugin__browser_links__open_url"
IDEMPOTENT_FILE_WRITE_TOOLS = frozenset({
    "write_artifact",
    "plugin__documents__export_to_local",
    "plugin__desktop_control__write_document",
})

type VerificationStatus = Literal[
    "not_applicable",
    "pending",
    "verified",
    "unverified",
    "failed",
    "uncertain",
]
type DigestFunction = Callable[[Any], str]
type VerificationEvidence = list[dict[str, Any]]


class ExecutionVerificationService:
    """Classify tool results without trusting model-generated success text."""

    @staticmethod
    def verify_effect(
        *,
        tool_call: dict[str, Any],
        payload: dict[str, Any],
        tool_run_id: str | None,
        digest: DigestFunction,
    ) -> tuple[VerificationStatus, VerificationEvidence]:
        if not bool(tool_call.get("side_effect")):
            return "not_applicable", []

        evidence: VerificationEvidence = [{
            "kind": "tool_result_digest",
            "sha256": digest(payload),
            "tool_run_id": tool_run_id,
        }]
        if str(payload.get("status") or "error") != "success":
            return "failed", evidence

        result = payload.get("result")
        if not isinstance(result, dict):
            return "unverified", evidence

        nested_result = result.get("result")
        result_candidates = [result]
        if isinstance(nested_result, dict):
            result_candidates.append(nested_result)

        tool_name = str(tool_call.get("tool_name") or "")
        browser_result = result_candidates[-1]
        receipt = browser_result.get("execution_receipt")
        if (
            tool_name == BROWSER_OPEN_TOOL_NAME
            and isinstance(receipt, dict)
            and receipt.get("success") is True
            and receipt.get("verification") == "launcher_accepted"
            and receipt.get("launcher") in {"firefox", "system_default"}
            and receipt.get("browser") in {"firefox", "default"}
            and str(receipt.get("accepted_at") or "").strip()
        ):
            evidence.append({
                "kind": "execution_receipt",
                "verification": str(receipt.get("verification"))[:200],
                "launcher": str(receipt.get("launcher") or "")[:200] or None,
                "accepted_at": (
                    str(receipt.get("accepted_at") or "")[:100] or None
                ),
                "receipt_sha256": digest(receipt),
            })
            return "verified", evidence

        trusted_file_result = next(
            (
                candidate
                for candidate in result_candidates
                if str(candidate.get("path") or "").strip()
            ),
            result,
        )
        path = str(trusted_file_result.get("path") or "").strip()
        if (
            tool_name == "write_artifact"
            and path
            and trusted_file_result.get("version") is not None
        ):
            evidence.append({
                "kind": "artifact_version",
                "path": path[:4000],
                "version": trusted_file_result.get("version"),
            })
            return "verified", evidence

        sha256 = str(
            trusted_file_result.get("sha256")
            or trusted_file_result.get("output_sha256")
            or ""
        ).strip()
        valid_sha256 = len(sha256) == 64 and all(
            character in "0123456789abcdefABCDEF"
            for character in sha256
        )
        if tool_name in IDEMPOTENT_FILE_WRITE_TOOLS and path and valid_sha256:
            evidence.append({
                "kind": "file_digest",
                "path": path[:4000],
                "sha256": sha256.lower(),
            })
            return "verified", evidence
        return "unverified", evidence


execution_verification_service = ExecutionVerificationService()
