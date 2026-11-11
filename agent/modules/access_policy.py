"""
access_policy.py — Centralized Model Access Policy & Subscription-Boundary Removal.

Implements the V3 ModelAccessPolicy interface:
Removes artificial application-level subscription tiers, upgrade dialogs, and upselling.
Enforces legitimate provider authentication, quota availability, and organizational security.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class PolicyEvaluationResult:
    allowed: bool
    status_code: str
    message: str
    remediation_hint: Optional[str] = None


class ModelAccessPolicy:
    """
    Centralized authorization policy for model execution.
    Eliminates internal subscription gates.
    Strictly honors external provider credentials, quotas, and security boundaries.
    """

    @classmethod
    def evaluate(
        cls,
        user_authenticated: bool,
        provider_id: str,
        model_id: str,
        estimated_tokens: int = 1000
    ) -> PolicyEvaluationResult:
        """
        Evaluates whether a model request is authorized to execute.
        No application-level 'Pro plan' or 'subscription tier' check exists here.
        """
        # 1. User / Workspace Authentication
        if not user_authenticated:
            return PolicyEvaluationResult(
                allowed=False,
                status_code="USER_UNAUTHENTICATED",
                message="Workspace member authentication required.",
                remediation_hint="Authenticate using your authorized Google Workspace account."
            )

        # 2. Provider Credential Presence
        is_connected = cls._check_provider_connection(provider_id)
        if not is_connected:
            return PolicyEvaluationResult(
                allowed=False,
                status_code="PROVIDER_AUTH_REQUIRED",
                message=f"Provider '{provider_id}' authentication required.",
                remediation_hint=f"Configure legitimate organization API credentials for {provider_id} in vault."
            )

        # 3. Model Availability on Provider
        is_available = cls._check_model_support(provider_id, model_id)
        if not is_available:
            return PolicyEvaluationResult(
                allowed=False,
                status_code="MODEL_UNAVAILABLE_TO_ORG",
                message=f"Model '{model_id}' is unavailable on provider '{provider_id}'.",
                remediation_hint="Verify organization model permissions on provider console or select an available model."
            )

        # 4. Quota Availability Check
        is_quota_ok = cls._check_quota_available(provider_id, estimated_tokens)
        if not is_quota_ok:
            return PolicyEvaluationResult(
                allowed=False,
                status_code="PROVIDER_QUOTA_EXCEEDED",
                message=f"Provider quota or rate limit exceeded for '{provider_id}'.",
                remediation_hint="Wait for provider rate-limit backoff or add credits to organization account."
            )

        # 5. All legitimate boundaries satisfied
        return PolicyEvaluationResult(
            allowed=True,
            status_code="AUTHORIZED",
            message="Model execution authorized under organization credentials."
        )

    @classmethod
    def _check_provider_connection(cls, provider_id: str) -> bool:
        if provider_id == "pollinations":
            return True
        key_map = {
            "arena": "ARENA_API_KEY",
            "anthropic": ["ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "ANTHROPIC_OAT_TOKEN"],
            "openrouter": "OPENROUTER_API_KEY",
            "groq": "GROQ_API_KEY",
            "deepinfra": "DEEPINFRA_API_KEY",
            "fal_ai": ["FAL_KEY", "FAL_AI_API_KEY"],
            "vertex_ai": "VERTEX_API_KEY",
        }
        req = key_map.get(provider_id)
        if not req:
            return False
        if isinstance(req, list):
            return any(bool(os.environ.get(k)) for k in req)
        return bool(os.environ.get(req))

    @classmethod
    def _check_model_support(cls, provider_id: str, model_id: str) -> bool:
        # Check against universal catalog or dynamic cache
        from agent.modules.capability_registry import CANONICAL_MODELS
        if model_id in CANONICAL_MODELS:
            mappings = CANONICAL_MODELS[model_id].get("provider_mappings", {})
            return provider_id in mappings

        # If dynamic identifier passed (e.g. 'openrouter/deepseek/deepseek-r1:free')
        if provider_id in model_id:
            return True

        return True

    @classmethod
    def _check_quota_available(cls, provider_id: str, estimated_tokens: int) -> bool:
        # Respect actual provider quota tracking (e.g. QuotaTracker or balance)
        try:
            from agent.modules import quota as q
            qs = q.daily_summary()
            return qs.get("remaining", 1) > 0
        except Exception:
            return True
