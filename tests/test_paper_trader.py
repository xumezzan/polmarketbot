from app.services.paper_trader import evaluate_auto_close_case, select_exit_market_price
from tests.helpers import build_test_settings


def test_auto_close_triggers_take_profit() -> None:
    settings = build_test_settings(
        paper_take_profit_delta=0.08,
        paper_stop_loss_delta=0.05,
        paper_max_hold_minutes=360,
    )

    should_close, close_reason, delta = evaluate_auto_close_case(
        settings=settings,
        entry_price=0.55,
        current_price=0.64,
        holding_minutes=30,
    )

    assert should_close is True
    assert close_reason == "take_profit_reached:0.0900>=0.0800"
    assert delta == 0.09


def test_auto_close_triggers_stop_loss() -> None:
    settings = build_test_settings(
        paper_take_profit_delta=0.08,
        paper_stop_loss_delta=0.05,
        paper_max_hold_minutes=360,
    )

    should_close, close_reason, delta = evaluate_auto_close_case(
        settings=settings,
        entry_price=0.55,
        current_price=0.49,
        holding_minutes=20,
    )

    assert should_close is True
    assert close_reason == "stop_loss_reached:-0.0600<=-0.0500"
    assert delta == -0.06


def test_auto_close_triggers_max_hold_time() -> None:
    settings = build_test_settings(
        paper_take_profit_delta=0.08,
        paper_stop_loss_delta=0.05,
        paper_max_hold_minutes=180,
    )

    should_close, close_reason, delta = evaluate_auto_close_case(
        settings=settings,
        entry_price=0.55,
        current_price=0.57,
        holding_minutes=181,
    )

    assert should_close is True
    assert close_reason == "max_holding_time_reached:181.00>=180"
    assert delta == 0.02


def test_auto_close_holds_when_exit_rules_not_met() -> None:
    settings = build_test_settings(
        paper_take_profit_delta=0.08,
        paper_stop_loss_delta=0.05,
        paper_max_hold_minutes=360,
    )

    should_close, close_reason, delta = evaluate_auto_close_case(
        settings=settings,
        entry_price=0.55,
        current_price=0.58,
        holding_minutes=45,
    )

    assert should_close is False
    assert close_reason is None
    assert delta == 0.03


def test_select_exit_market_price_uses_no_price_for_no_side() -> None:
    current_price = select_exit_market_price(
        side="NO",
        yes_price=0.63,
        no_price=0.37,
        last_trade_price=0.62,
    )

    assert current_price == 0.37
