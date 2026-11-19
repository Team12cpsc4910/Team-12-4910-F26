import io
import json
import os
import sys

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(__file__))

if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from routes import rewards


@pytest.mark.parametrize(
    "input_value, expected",
    [
        ("Elden Ring", "elden-ring"),
        ("elden-ring", "elden-ring"),
        (
            "https://gg.deals/game/elden-ring/",
            "elden-ring"
        ),
        (
            "https://gg.deals/game/cyberpunk-2077/",
            "cyberpunk-2077"
        )
    ]
)
def test_slug_lookups(input_value, expected):

    assert rewards.ggdeals_slug(input_value) == expected


def test_invalid_game_url():

    with pytest.raises(rewards.GameLookupError):

        rewards.ggdeals_slug(
            "https://malicious.example/game/elden-ring/"
        )


def test_ggdeals_price_conversion(monkeypatch):

    def mocked_urlopen(request, timeout):

        data = {
            "success": True,
            "data": {
                "elden-ring": {
                    "title": "Elden Ring",
                    "url": "https://gg.deals/game/elden-ring/",
                    "prices": {
                        "currentRetail": "19.99",
                        "currentKeyshops": "10.00",
                        "historicalRetail": "5.00",
                        "currency": "USD"
                    }
                }
            }
        }

        return io.BytesIO(
            json.dumps(data).encode()
        )

    monkeypatch.setattr(
        rewards,
        "urlopen",
        mocked_urlopen
    )

    monkeypatch.setenv(
        "GGDEALS_API_KEY",
        "test-key"
    )

    game = rewards.fetch_ggdeals_game(
        "elden-ring"
    )

    assert game["name"] == "Elden Ring"

    assert game["point_cost"] == 1999

    assert str(game["price_usd"]) == "19.99"


def test_missing_api_key(monkeypatch):

    monkeypatch.delenv(
        "GGDEALS_API_KEY",
        raising=False
    )

    with pytest.raises(rewards.GameLookupError):

        rewards.fetch_ggdeals_game(
            "elden-ring"
        )
