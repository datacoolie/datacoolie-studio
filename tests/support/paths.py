from pathlib import Path


TESTS_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = TESTS_ROOT.parent
FIXTURE_ROOT = TESTS_ROOT / "fixtures" / "datacoolie"
