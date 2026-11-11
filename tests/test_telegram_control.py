"""
test_telegram_control.py — Unit tests for Shorts Forge Telegram Master Controller.
"""

from unittest.mock import MagicMock, patch
import pytest

from agent.modules.telegram_control import TelegramAccessController, ShortsForgeCommandHandler


@patch.dict("os.environ", {"TELEGRAM_CHAT_ID": "12345678", "TELEGRAM_WEBHOOK_SECRET": "secret_tok_abc"})
def test_access_controller():
    # Correct chat
    assert TelegramAccessController.is_authorized("12345678") is True
    # Wrong chat
    assert TelegramAccessController.is_authorized("99999999") is False
    # Webhook secret verification
    assert TelegramAccessController.verify_webhook_secret("secret_tok_abc") is True
    assert TelegramAccessController.verify_webhook_secret("wrong_secret") is False


@patch.dict("os.environ", {"TELEGRAM_CHAT_ID": "12345678"})
@patch("agent.modules.telegram_control.notify")
def test_unauthorized_command_blocked(mock_notify):
    handler = ShortsForgeCommandHandler()
    update = {
        "message": {
            "chat": {"id": 99999999},  # Unauthorized chat
            "from": {"id": 99999999, "first_name": "Hacker"},
            "text": "/status"
        }
    }
    result = handler.process_update(update)
    assert result == "UNAUTHORIZED"
    # Verify security alert sent
    assert any("SECURITY_ALERT" in call.args for call in mock_notify.call_args_list)


@patch.dict("os.environ", {"TELEGRAM_CHAT_ID": "12345678"})
@patch("agent.modules.telegram_control.notify")
def test_authorized_help_and_status(mock_notify):
    handler = ShortsForgeCommandHandler()
    # Test /help
    update_help = {
        "message": {
            "chat": {"id": 12345678},
            "from": {"id": 12345678, "first_name": "Pankaj"},
            "text": "/help"
        }
    }
    assert handler.process_update(update_help) == "HELP_DELIVERED"

    # Test /status
    update_status = {
        "message": {
            "chat": {"id": 12345678},
            "from": {"id": 12345678, "first_name": "Pankaj"},
            "text": "/status"
        }
    }
    assert handler.process_update(update_status) == "STATUS_DELIVERED"


@patch.dict("os.environ", {"TELEGRAM_CHAT_ID": "12345678"})
@patch("agent.modules.telegram_control.notify")
def test_authorized_trends_and_score(mock_notify):
    handler = ShortsForgeCommandHandler()
    # Test /trends
    update_trends = {
        "message": {
            "chat": {"id": 12345678},
            "from": {"id": 12345678, "first_name": "Pankaj"},
            "text": "/trends Technology"
        }
    }
    assert handler.process_update(update_trends) == "TRENDS_DELIVERED"

    # Test /score
    update_score = {
        "message": {
            "chat": {"id": 12345678},
            "from": {"id": 12345678, "first_name": "Pankaj"},
            "text": "/score The ghost ruling these mountains will shock you."
        }
    }
    assert handler.process_update(update_score) == "SCORE_DELIVERED"


@patch.dict("os.environ", {"TELEGRAM_CHAT_ID": "12345678"})
@patch("agent.modules.telegram_control.notify")
def test_authorized_create_and_pause_resume(mock_notify):
    handler = ShortsForgeCommandHandler()
    # Test /create
    update_create = {
        "message": {
            "chat": {"id": 12345678},
            "from": {"id": 12345678, "first_name": "Pankaj"},
            "text": "/create Wildlife — Snow Leopard Kingdom"
        }
    }
    res_create = handler.process_update(update_create)
    assert res_create.startswith("CREATED_RUN_")

    # Test /pause & /resume
    update_pause = {"message": {"chat": {"id": 12345678}, "text": "/pause"}}
    assert handler.process_update(update_pause) == "PAUSED"

    update_resume = {"message": {"chat": {"id": 12345678}, "text": "/resume"}}
    assert handler.process_update(update_resume) == "RESUMED"


@patch.dict("os.environ", {"TELEGRAM_CHAT_ID": "12345678"})
@patch("agent.modules.telegram_control.notify")
@patch("agent.modules.telegram_control.answer_callback")
def test_callback_approval(mock_answer, mock_notify):
    handler = ShortsForgeCommandHandler()
    update_cb = {
        "callback_query": {
            "id": "cb_99",
            "message": {"chat": {"id": 12345678}},
            "from": {"id": 12345678, "first_name": "Pankaj"},
            "data": "approve:42"
        }
    }
    result = handler.process_update(update_cb)
    assert result == "APPROVED_42"


@patch.dict("os.environ", {"TELEGRAM_CHAT_ID": "12345678"})
@patch("agent.modules.telegram_control.notify")
def test_models_and_effort_commands(mock_notify):
    handler = ShortsForgeCommandHandler()

    # Test /models listing
    up_models = {"message": {"chat": {"id": 12345678}, "text": "/models"}}
    assert handler.process_update(up_models) == "MODELS_DELIVERED"

    # Test /model switch to deepseek
    up_switch = {"message": {"chat": {"id": 12345678}, "text": "/model deepseek"}}
    assert handler.process_update(up_switch) == "MODEL_SWITCHED_deepseek-r1"

    # Test /effort status
    up_eff = {"message": {"chat": {"id": 12345678}, "text": "/effort"}}
    assert handler.process_update(up_eff) == "EFFORT_DELIVERED"

    # Test /effort set max
    up_eff_set = {"message": {"chat": {"id": 12345678}, "text": "/effort max"}}
    assert handler.process_update(up_eff_set) == "EFFORT_SET_max"
