import asyncio

from app.schemas.alert import AlertMessage
from app.services.alerting import build_alert_client, get_alerting_runtime_status
from tests.helpers import build_test_settings


def test_alerting_runtime_status_reports_ready_for_valid_telegram_config() -> None:
    settings = build_test_settings(
        alert_mode="telegram",
        telegram_enabled=True,
        telegram_bot_token="123456:valid-token",
        telegram_chat_id="987654321",
    )

    status = get_alerting_runtime_status(settings)

    assert status == {
        "mode": "telegram",
        "enabled": True,
        "status": "ready",
        "reason": "",
    }


def test_build_alert_client_disables_misconfigured_telegram_alerting() -> None:
    settings = build_test_settings(
        alert_mode="telegram",
        telegram_enabled=True,
        telegram_bot_token="<REAL_TOKEN>",
        telegram_chat_id="987654321",
    )
    client = build_alert_client(settings)

    result = asyncio.run(
        client.send(
            AlertMessage(
                event="test_event",
                level="INFO",
                title="Test",
                text="hello",
            )
        )
    )

    assert result.mode == "telegram"
    assert result.delivered is False
    assert result.error == "telegram_bot_token_placeholder"


def test_build_alert_client_disables_unsupported_mode_instead_of_raising() -> None:
    settings = build_test_settings(alert_mode="pagerduty")
    client = build_alert_client(settings)

    result = asyncio.run(
        client.send(
            AlertMessage(
                event="test_event",
                level="INFO",
                title="Test",
                text="hello",
            )
        )
    )

    assert result.mode == "pagerduty"
    assert result.delivered is False
    assert result.error == "unsupported_alert_mode:pagerduty"
