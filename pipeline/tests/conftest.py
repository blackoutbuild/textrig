import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # pipeline/


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: shells out to the renderer or a model")
