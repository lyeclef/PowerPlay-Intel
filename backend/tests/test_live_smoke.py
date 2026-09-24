"""Opt-in public API smoke checks. Never sends admin credentials or mutates state."""
import os

import pytest
import requests

BASE = os.environ.get("POWERPLAY_TEST_URL", "").rstrip("/")
pytestmark = pytest.mark.skipif(not BASE, reason="Set POWERPLAY_TEST_URL for live smoke checks")


@pytest.mark.parametrize("route,key", [("categories","categories"),
    ("config/thresholds","config"), ("leaderboard?limit=2","wallets")])
def test_public_endpoint(route, key):
    response = requests.get(f"{BASE}/api/{route}", timeout=30)
    assert response.status_code == 200
    assert key in response.json()
