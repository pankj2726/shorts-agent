"""
test_social.py — Unit Tests for Social Intelligence & Community Engine.
"""

from agent.modules.social import SocialIntelligenceEngine


def test_get_audience_persona():
    persona = SocialIntelligenceEngine.get_audience_persona("quicktech")
    assert "target_audience" in persona
    assert len(persona["core_frustrations"]) >= 3
    assert len(persona["content_preferences"]) >= 3
    assert persona["optimal_pacing_wpm"] > 100


def test_analyze_comment_sentiment():
    sample_comments = [
        "This is awesome! Saved me hours of work thanks!",
        "Love this shortcut, subscribed!",
        "Fake video, this didn't work for me.",
        "How do I enable this on Windows 10?",
        "Can you make a video on Mac shortcuts?",
        "Just a normal shortcut.",
    ]

    analysis = SocialIntelligenceEngine.analyze_comment_sentiment(sample_comments)
    assert analysis["total_comments"] == 6
    assert analysis["positive"] >= 2
    assert analysis["negative"] >= 1
    assert analysis["questions"] >= 2
    assert len(analysis["extracted_questions"]) >= 2


def test_discover_competitor_angles():
    benchmarks = SocialIntelligenceEngine.discover_competitor_angles("technology")
    assert len(benchmarks) >= 3
    for b in benchmarks:
        assert "topic" in b
        assert "hook_type" in b
        assert "sample_hook" in b
