import sys, urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Safety net: no test may reach Databricks, Twilio or ElevenLabs (page.py calls urllib directly)."""
    def blocked(*a, **k):
        raise RuntimeError("network access is blocked in tests")
    monkeypatch.setattr(urllib.request, "urlopen", blocked)


@pytest.fixture
def station():
    """A regulated discharge gauge with one upstream, one downstream and one other-river neighbour."""
    return {"station_id": "05BH004", "name": "BOW RIVER AT CALGARY", "province": "AB", "regulated": True, "controls": "Bearspaw Dam",
            "update_cadence_min": 5, "normal_flatline_steps": 6, "typical_change_fraction": 0.3, "notes": None,
            "upstream_ids": '["05BH005"]', "downstream_ids": "05BM002", "nearby_ids": '[{"id": "05BJ010", "km": 12.0}]',
            "discharge_min_possible_cms": 0.0, "discharge_max_plausible_cms": 1500.0,
            "level_min_possible_m": 0.5, "level_max_plausible_m": 6.0, "p99_cms": '{"9": 300.0}'}
