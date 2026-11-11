"""
test_anti_slop.py — Unit tests for Anti-Slop Killer and Humanizer.
"""

from agent.modules.anti_slop_killer import AntiSlopKiller


def test_anti_slop_audit_detects_cliches():
    killer = AntiSlopKiller()
    script = (
        "In a world where technology rules, we must delve into the rich tapestry of modern AI. "
        "It is a true testament to human creativity. Remember to like and subscribe."
    )
    audit = killer.audit_script(script)

    assert audit["passed"] is False
    assert audit["verdict"] == "SLOP_DETECTED"
    assert "delve into" in audit["detected_cliches"]
    assert "rich tapestry" in audit["detected_cliches"]
    assert "testament to" in audit["detected_cliches"]


def test_anti_slop_clean_and_humanize():
    killer = AntiSlopKiller()
    script = "Let us delve into this game-changer that will revolutionize everything."
    cleaned = killer.clean_and_humanize(script)

    assert "delve into" not in cleaned.lower()
    assert "game-changer" not in cleaned.lower()
    assert "revolutionize" not in cleaned.lower()
    assert "explore" in cleaned.lower()
    assert "breakthrough" in cleaned.lower()


def test_clean_script_passes_audit():
    killer = AntiSlopKiller()
    script = (
        "There is a ghost ruling these mountains. "
        "Few people ever see it in the wild. "
        "Every step is a battle for survival. "
        "When it moves, the snow hides it completely."
    )
    audit = killer.audit_script(script)

    assert audit["passed"] is True
    assert audit["verdict"] == "CLEAN"
    assert len(audit["detected_cliches"]) == 0
