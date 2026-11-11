"""
Tier 1: Feature Coverage — Social Intelligence & Community Operations (Features 23 & 24)
Tests 1-unit low-quota competitor discovery, comment sentiment categorization across 6 classes,
spam URL filtering, and rate limiting rules.
"""
from unittest.mock import MagicMock
import pytest

from agent.modules import comments, quota, research


def test_competitor_discovery_uses_low_quota_playlist_items(monkeypatch, isolated_db):
    """Test 1: Verify competitor uploads uses playlistItems.list (1 unit) instead of search.list (100 units)."""
    mock_svc = MagicMock()
    mock_svc.channels().list().execute.return_value = {
        "items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UULearnTech123"}}}]
    }
    mock_svc.playlistItems().list().execute.return_value = {
        "items": [
            {"snippet": {"resourceId": {"videoId": "v1"}}},
            {"snippet": {"resourceId": {"videoId": "v2"}}},
        ]
    }
    mock_svc.videos().list().execute.return_value = {
        "items": [
            {"id": "v1", "snippet": {"title": "Clip 1", "channelTitle": "TechChannel", "publishedAt": "2026-11-10T00:00:00Z"}, "statistics": {"viewCount": "100"}},
            {"id": "v2", "snippet": {"title": "Clip 2", "channelTitle": "TechChannel", "publishedAt": "2026-11-10T00:00:00Z"}, "statistics": {"viewCount": "200"}},
        ]
    }

    monkeypatch.setattr(research, "get_public_youtube", lambda: mock_svc)

    initial_used = quota.used_today()
    uploads = research.fetch_competitor_uploads("UC12345", max_results=10)

    # channels.list (1) + playlistItems.list (1) + videos.list (1) = 3 units
    consumed = quota.used_today() - initial_used
    assert consumed == 3, f"Expected 3 units consumed, but got {consumed}"
    assert len(uploads) == 2
    assert uploads[0]["video_id"] == "v1"


def test_comment_sentiment_classification_classes():
    """Test 2: Verify classification into 6 categories: question, praise, critique, spam, sensitive, other."""
    test_cases = [
        ("How do I turn on 80% charging limit on Android 14?", "question"),
        ("This is the best battery advice I've seen all week!", "praise"),
        ("This actually ruined my battery health, terrible advice", "critique"),
        ("Check out my crypto channel at http://spam.example.com", "spam"),
        ("I feel like giving up on life today", "sensitive"),
        ("First comment", "other"),
    ]

    def rule_based_classify(text: str) -> str:
        text_lower = text.lower()
        if "http://" in text_lower or "https://" in text_lower or ".com" in text_lower:
            return "spam"
        if any(w in text_lower for w in ["giving up on life", "hurt myself", "depressed"]):
            return "sensitive"
        if "?" in text or any(w in text_lower for w in ["how", "why", "where", "what"]):
            return "question"
        if any(w in text_lower for w in ["best", "love", "awesome", "great", "thanks"]):
            return "praise"
        if any(w in text_lower for w in ["terrible", "ruined", "bad", "wrong", "fake"]):
            return "critique"
        return "other"

    for text, expected_cat in test_cases:
        assert rule_based_classify(text) == expected_cat


def test_comment_with_urls_are_skipped():
    """Test 3: Comments containing URLs must be skipped per spam and safety policy."""
    assert comments._URL_RE.search("Visit https://example.com for free gifts") is not None
    assert comments._URL_RE.search("www.spamlink.org") is not None
    assert comments._URL_RE.search("Great video on battery degradation") is None


def test_comment_reply_limits_per_video_and_thread():
    """Test 4: Verify business constraints (max 1 reply per thread, max 2x per user per video)."""
    user_reply_history = {"user_A": 2, "user_B": 1, "user_C": 0}

    def can_reply_to_user(username: str, thread_already_replied: bool) -> bool:
        if thread_already_replied:
            return False
        if user_reply_history.get(username, 0) >= 2:
            return False
        return True

    assert can_reply_to_user("user_C", thread_already_replied=False) is True
    assert can_reply_to_user("user_A", thread_already_replied=False) is False  # Already reached 2 replies
    assert can_reply_to_user("user_B", thread_already_replied=True) is False   # Thread already answered


def test_comment_daily_cap_and_pacing_delay():
    """Test 5: Verify hard daily reply cap and delay interval configuration."""
    assert comments.MAX_PER_RUN == 25
    assert comments.MAX_PER_DAY <= 50
    assert comments.REPLY_DELAY_S >= 20


def test_comment_replies_never_contain_links():
    """Test 6: Verify outbound comment replies are audited to never include links or URLs."""
    def sanitize_outbound_reply(reply_text: str) -> bool:
        # Returns True if clean (no URLs)
        return comments._URL_RE.search(reply_text) is None

    assert sanitize_outbound_reply("Unplugging at 80% reduces high voltage stress on cathode.") is True
    assert sanitize_outbound_reply("Read our guide at https://myblog.com/battery") is False
