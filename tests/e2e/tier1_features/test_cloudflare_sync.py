"""
test_cloudflare_sync.py — Feature 20: Cloudflare Edge Router Synchronization.

Validates that deploy/cloudflare-router/worker.js:
  1. Synchronously mirrors the Infinite Multi-Router Cascade Matrix (IMRCM).
  2. Binds canonical models including 5 newly hired models (claude-3-5-haiku, mistral-large, deepseek-v3, llama-3.1-405b, grok-2/gemini).
  3. Propagates Cloudflare Error 1010 bypass User-Agent and identity headers.
  4. Exposes CORS preflight handling for Android and external clients.
  5. Implements persistent conversation storage synchronization via R2 (WORKSPACE_CACHE).
"""

from __future__ import annotations

from pathlib import Path
import pytest


@pytest.fixture
def worker_js_content() -> str:
    worker_path = Path(__file__).resolve().parent.parent.parent.parent / "deploy" / "cloudflare-router" / "worker.js"
    assert worker_path.exists(), f"worker.js not found at {worker_path}"
    with open(worker_path, "r", encoding="utf-8") as f:
        return f.read()


def test_cloudflare_worker_file_exists_and_valid(worker_js_content):
    """Verify worker.js exists, is non-empty, and exports a default fetch handler."""
    assert len(worker_js_content) > 500
    assert "export default {" in worker_js_content
    assert "async fetch(request, env)" in worker_js_content


def test_cloudflare_worker_contains_canonical_models(worker_js_content):
    """Verify worker.js canonical model catalog includes frontier and hired models."""
    assert "claude-3-5-sonnet" in worker_js_content
    assert "claude-3-5-haiku" in worker_js_content
    assert "deepseek-v3" in worker_js_content
    assert "mistral-large-2411" in worker_js_content
    assert "llama-3-1-405b" in worker_js_content or "llama-3.1-405b" in worker_js_content


def test_cloudflare_worker_implements_imrcm_cascade(worker_js_content):
    """Verify worker.js routes requests across Arena, OpenRouter, and OmniRoute gateways."""
    assert "https://api.arena.ai/v1/chat/completions" in worker_js_content
    assert "https://openrouter.ai/api/v1/chat/completions" in worker_js_content
    assert "api.omniroute.ai" in worker_js_content or "api.opencode.ai" in worker_js_content


def test_cloudflare_worker_cloudflare_error_1010_bypass_headers(worker_js_content):
    """Verify worker.js attaches User-Agent and X-Workspace-Identity to bypass Cloudflare Error 1010."""
    assert "User-Agent" in worker_js_content
    assert "ShortsAgent-CloudflareEdge/1.0" in worker_js_content or "ShortsAgent" in worker_js_content
    assert "X-Workspace-Identity" in worker_js_content
    assert "pankajbhati140@gmail.com" in worker_js_content


def test_cloudflare_worker_cors_and_r2_endpoints(worker_js_content):
    """Verify worker.js implements CORS preflight (OPTIONS) and R2 conversation sync."""
    assert "Access-Control-Allow-Origin" in worker_js_content
    assert "request.method === \"OPTIONS\"" in worker_js_content
    assert "WORKSPACE_CACHE" in worker_js_content
    assert "/v1/conversations" in worker_js_content
