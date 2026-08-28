"""PEPPER — descubrimiento dinámico de sistemas legacy."""

from pathlib import Path

__version__ = "0.1.0"

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILES_DIR = REPO_ROOT / "profiles"
SCHEMAS_DIR = REPO_ROOT / "schemas"
SKILLS_DIR = REPO_ROOT / ".claude" / "skills"
