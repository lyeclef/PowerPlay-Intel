import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "powerplay_tests")


@pytest.fixture(autouse=True)
def reset_configuration():
    import config
    config.set_config(config.merge_config({}))
    yield
    config.set_config(config.merge_config({}))
