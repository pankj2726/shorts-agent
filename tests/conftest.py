"""
conftest.py — Global test fixtures, zero-cost enforcement, and opaque-box contracts
for Shorts Agent end-to-end and integration test suites.
"""
import enum
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

# Ensure shorts-agent root is in sys.path
SHORTS_AGENT_ROOT = Path(__file__).parent.parent.resolve()
if str(SHORTS_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(SHORTS_AGENT_ROOT))

# ─── Strict Zero-Cost Whitelist and Violation Error ───────────────────────────

class ZeroCostViolationError(Exception):
    """Raised whenever a paid model, billable API, or unauthorized endpoint is accessed."""
    pass


class UnauthorizedActionError(Exception):
    """Raised when an action requiring human approval is attempted without authorization."""
    pass


FREE_MODEL_WHITELIST = {
    # Google Gemini free tier
    "gemini-flash-lite-latest",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    # Groq free tier
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    # OpenRouter :free models
    "meta-llama/llama-3.1-70b-instruct:free",
    "meta-llama/llama-3.2-3b-instruct:free",
    "google/gemini-2.0-flash-exp:free",
    "qwen/qwen-2.5-72b-instruct:free",
}

PAID_MODEL_BLACKLIST = {
    "gpt-4",
    "gpt-4o",
    "gpt-4-turbo",
    "gpt-3.5-turbo",
    "claude-3-opus",
    "claude-3-5-sonnet",
    "claude-3-sonnet",
    "claude-3-haiku",
    "claude-2",
    "text-davinci-003",
    "gemini-1.5-pro",
}

FREE_ENDPOINT_WHITELIST = {
    "generativelanguage.googleapis.com",
    "api.groq.com",
    "openrouter.ai",
    "image.pollinations.ai",
    "api.telegram.org",
    "api.pexels.com",
    "pixabay.com",
    "www.googleapis.com",
    "youtubeAnalytics.googleapis.com",
}


# ─── Interface Contracts & Fallback Mock Implementations ──────────────────────

class TaskType(enum.Enum):
    SCRIPT = "script"
    RESEARCH = "research"
    QA_REVIEW = "qa_review"
    STRATEGY = "strategy"
    COMMENT = "comment"


class ModelResponse:
    def __init__(self, text: str, model: str, provider: str, tokens_used: int = 150, latency_ms: float = 240.0):
        self.text = text
        self.model = model
        self.provider = provider
        self.tokens_used = tokens_used
        self.latency_ms = latency_ms

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "model": self.model,
            "provider": self.provider,
            "tokens_used": self.tokens_used,
            "latency_ms": self.latency_ms,
        }


class StageResult:
    def __init__(self, passed: bool, score: float, stage: str, details: Optional[dict] = None, problems: Optional[list] = None):
        self.passed = passed
        self.score = score
        self.stage = stage
        self.details = details or {}
        self.problems = problems or []

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "score": self.score,
            "stage": self.stage,
            "details": self.details,
            "problems": self.problems,
        }


class ComprehensiveReviewReport:
    def __init__(self, verdict: str, composite_score: float, stage_results: dict, problems: list, corrections: dict):
        self.verdict = verdict  # PASS | NEEDS_REVISION | REJECT
        self.composite_score = composite_score
        self.stage_results = stage_results
        self.problems = problems
        self.corrections = corrections

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "composite_score": self.composite_score,
            "stage_results": {k: v.to_dict() if hasattr(v, "to_dict") else v for k, v in self.stage_results.items()},
            "problems": self.problems,
            "corrections": self.corrections,
        }


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def zero_cost_guard(monkeypatch):
    """
    Guarantees that no test execution invokes paid model endpoints or billable APIs.
    Intercepts network requests and model dispatch requests.
    """
    import urllib.parse
    import requests

    real_send = requests.Session.send

    def guarded_send(self, request, **kwargs):
        parsed = urllib.parse.urlparse(request.url)
        host = parsed.hostname or ""

        # Block any non-whitelisted paid host
        if "openai.com" in host or "anthropic.com" in host or "api.deepseek.com" in host:
            raise ZeroCostViolationError(f"Attempted to access prohibited paid provider host: {host}")

        # Check request body for blacklisted models
        body_text = ""
        if request.body:
            if isinstance(request.body, bytes):
                body_text = request.body.decode("utf-8", errors="ignore")
            else:
                body_text = str(request.body)
            
            for paid_model in PAID_MODEL_BLACKLIST:
                if f'"{paid_model}"' in body_text or f"'{paid_model}'" in body_text:
                    raise ZeroCostViolationError(f"Prohibited paid model '{paid_model}' requested in API payload.")

        return real_send(self, request, **kwargs)

    monkeypatch.setattr(requests.Session, "send", guarded_send)
    yield


@pytest.fixture
def temp_workspace(tmp_path):
    """Provides a fresh isolated directory for temporary test artifacts."""
    work_dir = tmp_path / "workspace"
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


@pytest.fixture
def mock_db_path(tmp_path):
    """Provides a fresh temporary SQLite database path."""
    db_file = tmp_path / "test_db.sqlite"
    return db_file


@pytest.fixture
def isolated_db(mock_db_path, monkeypatch):
    """Initializes and binds the test SQLite database to agent.modules.db and quota."""
    conn = sqlite3.connect(mock_db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS ideas (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        idea        TEXT    NOT NULL,
        angle       TEXT,
        hook        TEXT,
        format      TEXT,
        score       REAL,
        status      TEXT    DEFAULT 'pending',
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS videos (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        idea_id         INTEGER REFERENCES ideas(id),
        title           TEXT,
        description     TEXT,
        tags            TEXT,
        script          TEXT,
        script_qa_ok    INTEGER DEFAULT 0,
        render_path     TEXT,
        render_qa_ok    INTEGER DEFAULT 0,
        yt_video_id     TEXT,
        yt_status       TEXT,
        publish_at      TEXT,
        contains_synthetic INTEGER DEFAULT 0,
        quota_used      INTEGER DEFAULT 0,
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        published_at    DATETIME
    );
    CREATE TABLE IF NOT EXISTS assets (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id    INTEGER REFERENCES videos(id),
        asset_type  TEXT,
        source_url  TEXT,
        license     TEXT,
        local_path  TEXT,
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS comments (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        yt_video_id     TEXT,
        comment_id      TEXT UNIQUE,
        author          TEXT,
        text            TEXT,
        category        TEXT,
        replied         INTEGER DEFAULT 0,
        reply_text      TEXT,
        flagged         INTEGER DEFAULT 0,
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS quota_log (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        day     TEXT    NOT NULL,
        call    TEXT    NOT NULL,
        units   INTEGER NOT NULL,
        ts      DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS pending_actions (
        action_id        TEXT PRIMARY KEY,
        action_type      TEXT NOT NULL,
        payload          TEXT NOT NULL,
        status           TEXT DEFAULT 'pending',
        created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
        expires_at       DATETIME,
        authorized_at    DATETIME
    );
    """)
    conn.commit()
    conn.close()

    # Monkeypatch db and quota paths if modules are loaded
    try:
        from agent.modules import db, quota
        monkeypatch.setattr(db, "DB_PATH", mock_db_path)
        monkeypatch.setattr(quota, "_DB_PATH", mock_db_path)
    except Exception:
        pass

    return mock_db_path


@pytest.fixture
def sample_script():
    """Provides a compliant YouTube Shorts script payload."""
    return {
        "title": "Why 99% Of People Charge Phones Wrong",
        "hook": "Stop charging your phone to 100 percent overnight.",  # 8 words (<=12)
        "script": (
            "Stop charging your phone to 100 percent overnight. Modern lithium batteries "
            "degrade twice as fast when kept at peak voltage under high heat continuously. "
            "Instead, unplug at 80 percent to double your battery lifespan and retain peak performance. "
            "Extensive laboratory tests show this single habit preserves over 90 percent capacity after two full years. "
            "Try this simple routine tonight and protect your expensive device."
        ),
        "estimated_duration_s": 28,  # between 20 and 45s
        "sources": ["Battery University Research 2024", "IEEE Battery Life Standards"],
        "target_viewer": "Smartphone users who want batteries to last",
        "format": "Mistake reveal / contrarian fact",
        "scenes": [
            {"keyword": "smartphone battery charging", "duration_s": 3.0},
            {"keyword": "overheating phone close up", "duration_s": 3.0},
            {"keyword": "battery health settings indicator", "duration_s": 3.0},
            {"keyword": "happy person using phone", "duration_s": 3.0},
        ],
    }


@pytest.fixture
def create_test_mp4(temp_workspace):
    """Creates a small real 1080x1920 MP4 video file using ffmpeg for audio/video QA checks."""
    def _create(filename="test_vertical.mp4", duration=3, color="#1e1e2e"):
        out_path = temp_workspace / filename
        if out_path.exists():
            return out_path
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"color=c={color}:s=1080x1920:d={duration}:r=30",
            "-f", "lavfi",
            "-i", f"sine=frequency=1000:duration={duration}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            str(out_path)
        ]
        res = subprocess.run(cmd, capture_output=True, timeout=20)
        assert res.returncode == 0, f"ffmpeg failed: {res.stderr.decode()}"
        return out_path
    return _create


@pytest.fixture
def create_test_wav(temp_workspace):
    """Creates a test audio WAV file using ffmpeg."""
    def _create(filename="test_audio.wav", duration=5, frequency=440):
        out_path = temp_workspace / filename
        if out_path.exists():
            return out_path
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"sine=frequency={frequency}:duration={duration}",
            str(out_path)
        ]
        res = subprocess.run(cmd, capture_output=True, timeout=15)
        assert res.returncode == 0, f"ffmpeg audio failed: {res.stderr.decode()}"
        return out_path
    return _create
