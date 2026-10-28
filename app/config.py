"""App settings, read from environment variables (see .env.example)."""
import os
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Tiny .env loader so the project has no extra dependency for it."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


BASE_DIR = Path(__file__).resolve().parent.parent
_load_dotenv(BASE_DIR / ".env")

CLINIC_NAME = os.getenv("CLINIC_NAME", "Sunrise Multi-Speciality Clinic")
CLINIC_PHONE = os.getenv("CLINIC_PHONE", "+91 90000 00000")
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "clinic.db"))
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "change-me")
TIMEZONE_OFFSET_MIN = int(os.getenv("TIMEZONE_OFFSET_MIN", "330"))  # IST

# Optional LLM (any OpenAI-compatible API: OpenAI, Groq, Together, Ollama...).
# If no key is set, the assistant uses its built-in rule-based parser.
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "llama-3.1-8b-instant")
