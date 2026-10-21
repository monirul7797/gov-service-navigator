"""Make the stdlib-only helper scripts in ``deploy/`` importable by the tests."""
import sys
from pathlib import Path

DEPLOY_DIR = Path(__file__).resolve().parents[1]
if str(DEPLOY_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOY_DIR))
