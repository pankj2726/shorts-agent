"""
tester_router.py — Workforce Tester Account & Free Router Federation Engine.

Enables connecting tester user accounts/emails to legitimate free AI routers:
  - Google Gemini Free Tier
  - OpenRouter Free (:free models)
  - GroqCloud Developer Free Tier
  - Cerebras Inference Free Tier
  - Cloudflare Workers AI Free Tier (10k daily neurons)
  - Hugging Face Serverless Inference API (Free tier)
  - Mistral AI Free Experimentation Tier
  - Local compute (edge-tts, ffmpeg, local whisper)

Enforces:
  - Strict $0.00 zero-cost constraint across all workflows.
  - Multi-tenant isolation: Tester A's tokens never expose private data.
  - Atomic reservation to prevent double-spending quota.
  - Free-first routing to maximize available token playground.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ─── Supported Free Routers & Providers ────────────────────────────────────────

FREE_ROUTER_CATALOG: Dict[str, Dict[str, Any]] = {
    "google-gemini": {
        "name": "Google Gemini Free Studio",
        "doc_url": "https://ai.google.dev",
        "free_tier": True,
        "default_models": ["gemini-flash-lite-latest", "gemini-2.0-flash"],
        "rate_limit_rpm": 15,
        "rate_limit_rpd": 1500,
        "requires_key": True,
        "env_var": "GEMINI_API_KEY",
    },
    "openrouter": {
        "name": "OpenRouter Free Models",
        "doc_url": "https://openrouter.ai",
        "free_tier": True,
        "default_models": [
            "meta-llama/llama-3.1-70b-instruct:free",
            "meta-llama/llama-3.3-70b-instruct:free",
            "mistralai/mistral-7b-instruct:free",
            "qwen/qwen-2.5-72b-instruct:free",
        ],
        "rate_limit_rpm": 20,
        "rate_limit_rpd": 50,
        "requires_key": True,
        "env_var": "OPENROUTER_API_KEY",
    },
    "groq": {
        "name": "GroqCloud Fast Free Inference",
        "doc_url": "https://console.groq.com",
        "free_tier": True,
        "default_models": [
            "llama-3.3-70b-versatile",
            "qwen/qwen3.8-27b",
            "openai/gpt-oss-120b",
            "whisper-large-v3",
        ],
        "rate_limit_rpm": 30,
        "rate_limit_rpd": 14400,
        "requires_key": True,
        "env_var": "GROQ_API_KEY",
    },
    "cerebras": {
        "name": "Cerebras Inference Free Tier",
        "doc_url": "https://cerebras.ai",
        "free_tier": True,
        "default_models": ["llama3.1-8b", "llama3.1-70b"],
        "rate_limit_rpm": 30,
        "rate_limit_rpd": 14400,
        "requires_key": True,
        "env_var": "CEREBRAS_API_KEY",
    },
    "cloudflare-workers-ai": {
        "name": "Cloudflare Workers AI",
        "doc_url": "https://developers.cloudflare.com/workers-ai",
        "free_tier": True,
        "default_models": ["@cf/meta/llama-3-8b-instruct", "@cf/mistral/mistral-7b-instruct-v0.1"],
        "rate_limit_rpm": None,
        "free_daily_neurons": 10000,
        "requires_key": True,
        "env_var": "CLOUDFLARE_API_TOKEN",
    },
    "huggingface": {
        "name": "Hugging Face Serverless Inference",
        "doc_url": "https://huggingface.co/inference-api",
        "free_tier": True,
        "default_models": ["meta-llama/Llama-3.2-3B-Instruct", "mistralai/Mistral-7B-Instruct-v0.3"],
        "rate_limit_rpm": 10,
        "requires_key": True,
        "env_var": "HF_TOKEN",
    },
    "pollinations": {
        "name": "Pollinations Multimodal Free Tier",
        "doc_url": "https://pollinations.ai",
        "free_tier": True,
        "default_models": ["flux", "turbo"],
        "requires_key": False,
        "env_var": None,
    },
    "local-edge-tts": {
        "name": "Local Neural Speech (edge-tts)",
        "doc_url": "https://github.com/rany2/edge-tts",
        "free_tier": True,
        "default_models": ["en-IN-NeerjaNeural", "en-US-JennyNeural"],
        "requires_key": False,
        "unlimited": True,
    },
    "local-ffmpeg": {
        "name": "Local Render & Subtitle Composition Engine",
        "doc_url": "https://ffmpeg.org",
        "free_tier": True,
        "default_models": ["ffmpeg-h264-aac"],
        "requires_key": False,
        "unlimited": True,
    },
}


# ─── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class TesterAccount:
    """Represents a tester or employee's connected provider account."""
    user_id: str
    email: str
    provider: str
    account_type: str = "tester_delegated"  # organisation_owned | tester_delegated | personal_only
    sharing_mode: str = "delegated"         # shared_pool | delegated | owner_only
    connected_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ"))
    status: str = "active"                  # active | rate_limited | revoked
    available_models: List[str] = field(default_factory=list)
    daily_request_limit: Optional[int] = None
    requests_used_today: int = 0
    reserved_requests: int = 0
    last_reset: str = field(default_factory=lambda: time.strftime("%Y-%m-%d"))


@dataclass
class QuotaReservation:
    """Atomic token / request reservation lock."""
    reservation_id: str
    user_id: str
    provider: str
    model: str
    requests_reserved: int
    created_at: float
    status: str = "active"  # active | committed | released


# ─── Federation Manager ───────────────────────────────────────────────────────

class WorkforceRouterFederation:
    """
    Manages workforce tester connections and federates access across free routers.
    Maintains thread-safe atomic reservations and multi-tenant isolation.
    """

    def __init__(self, state_file: Optional[Path] = None):
        self._lock = threading.RLock()
        self.state_file = state_file or Path(__file__).parent.parent / "state" / "tester_router_state.json"
        self.testers: Dict[str, TesterAccount] = {}
        self.reservations: Dict[str, QuotaReservation] = {}
        self._load_state()

    def _load_state(self) -> None:
        """Load registered tester accounts from persistent state."""
        with self._lock:
            if self.state_file.exists():
                try:
                    with open(self.state_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    for item in data.get("testers", []):
                        acc = TesterAccount(**item)
                        key = f"{acc.email}:{acc.provider}"
                        self.testers[key] = acc
                except Exception as e:
                    logger.warning("Could not load tester router state: %s", e)

    def _save_state(self) -> None:
        """Persist state to disk safely."""
        with self._lock:
            try:
                self.state_file.parent.mkdir(parents=True, exist_ok=True)
                payload = {
                    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "testers": [asdict(t) for t in self.testers.values()],
                }
                tmp = self.state_file.with_suffix(".tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2)
                tmp.replace(self.state_file)
            except Exception as e:
                logger.error("Failed to save tester router state: %s", e)

    def register_tester(
        self,
        user_id: str,
        email: str,
        provider: str,
        sharing_mode: str = "delegated",
        custom_models: Optional[List[str]] = None,
    ) -> TesterAccount:
        """
        Register or update a tester account connection for a free router.
        """
        with self._lock:
            if provider not in FREE_ROUTER_CATALOG:
                raise ValueError(f"Unknown router provider: {provider}")

            catalog_entry = FREE_ROUTER_CATALOG[provider]
            models = custom_models or catalog_entry.get("default_models", [])
            key = f"{email}:{provider}"

            account = TesterAccount(
                user_id=user_id,
                email=email,
                provider=provider,
                sharing_mode=sharing_mode,
                available_models=list(models),
                daily_request_limit=catalog_entry.get("rate_limit_rpd"),
            )
            self.testers[key] = account
            self._save_state()
            logger.info("Registered tester %s for provider %s with %d models", email, provider, len(models))
            return account

    def reserve_capacity(
        self,
        task_modality: str,
        user_id: str,
        preferred_provider: Optional[str] = None,
    ) -> Optional[QuotaReservation]:
        """
        Atomically reserve capacity from eligible free router pool.
        Strictly zero cost: never touches paid tiers.
        """
        with self._lock:
            # 1. Reset daily limits if new day
            today = time.strftime("%Y-%m-%d")
            for t in self.testers.values():
                if t.last_reset != today:
                    t.requests_used_today = 0
                    t.reserved_requests = 0
                    t.last_reset = today

            # 2. Find candidate accounts
            candidates = []
            for t in self.testers.values():
                if t.status != "active":
                    continue
                # Check user access (owner or delegated/shared)
                if t.sharing_mode == "owner_only" and t.user_id != user_id:
                    continue
                # Check remaining capacity
                if t.daily_request_limit is not None:
                    avail = t.daily_request_limit - (t.requests_used_today + t.reserved_requests)
                    if avail <= 0:
                        continue
                candidates.append(t)

            if not candidates:
                # Fall back to organisation default free environment
                return None

            # Sort by remaining headroom
            candidates.sort(
                key=lambda x: (
                    x.daily_request_limit - (x.requests_used_today + x.reserved_requests)
                    if x.daily_request_limit else 999999
                ),
                reverse=True,
            )

            chosen = candidates[0]
            chosen.reserved_requests += 1

            res_id = str(uuid.uuid4())
            res = QuotaReservation(
                reservation_id=res_id,
                user_id=user_id,
                provider=chosen.provider,
                model=chosen.available_models[0] if chosen.available_models else "default",
                requests_reserved=1,
                created_at=time.time(),
            )
            self.reservations[res_id] = res
            self._save_state()
            return res

    def commit_reservation(self, reservation_id: str, requests_consumed: int = 1) -> None:
        """Commit an actual usage deduction after successful execution."""
        with self._lock:
            res = self.reservations.get(reservation_id)
            if not res or res.status != "active":
                return
            res.status = "committed"

            # Find matching account
            for t in self.testers.values():
                if t.provider == res.provider and t.user_id == res.user_id:
                    t.reserved_requests = max(0, t.reserved_requests - res.requests_reserved)
                    t.requests_used_today += requests_consumed
                    break
            self._save_state()

    def release_reservation(self, reservation_id: str) -> None:
        """Release unused reservation lock if task failed or was canceled."""
        with self._lock:
            res = self.reservations.get(reservation_id)
            if not res or res.status != "active":
                return
            res.status = "released"

            for t in self.testers.values():
                if t.provider == res.provider and t.user_id == res.user_id:
                    t.reserved_requests = max(0, t.reserved_requests - res.requests_reserved)
                    break
            self._save_state()

    def get_federated_catalog(self) -> Dict[str, Any]:
        """Return the combined model catalogue available across all connected routers."""
        with self._lock:
            all_models = set()
            active_providers = set()
            for t in self.testers.values():
                if t.status == "active":
                    active_providers.add(t.provider)
                    all_models.update(t.available_models)

            # Add default catalog entries
            for p, entry in FREE_ROUTER_CATALOG.items():
                if p in active_providers or not entry.get("requires_key"):
                    all_models.update(entry.get("default_models", []))

            return {
                "active_providers_count": len(active_providers),
                "total_free_models_available": len(all_models),
                "models": sorted(list(all_models)),
                "budget_constraint": "$0.00 (Zero Expenditure Enforced)",
            }
