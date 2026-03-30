from dotenv import load_dotenv
import os

# Load .env once for the whole application
load_dotenv()

# --- General ---
APP_VERSION = os.getenv("APP_VERSION", "1.0")

# --- Backend / agent-backend ---
DATABASE_URL = os.getenv("DATABASE_URL")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
APP_EXTERNAL_URL = os.getenv("APP_EXTERNAL_URL", "http://localhost:8000")
BACKEND_API_KEY = os.getenv("BACKEND_API_KEY")
STATE_SIGNING_SECRET = os.getenv("STATE_SIGNING_SECRET")

# --- Notebook / LLM ---
API_ENDPOINT = os.getenv("API_ENDPOINT", APP_EXTERNAL_URL)
GOOGLE_API_KEY_AI = os.getenv("GOOGLE_API_KEY_AI")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
PROFILE_EMAIL = os.getenv("PROFILE_EMAIL")

# --- Slackbot (Flask app) ---
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")
SLACK_TEAM_ID = os.getenv("SLACK_TEAM_ID")

# --- Frontend / proxy ---
BACKEND_INTERNAL_URL = os.getenv("BACKEND_INTERNAL_URL", "http://127.0.0.1:3000/health")

# --- Misc helpers ---
def require_env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Required env var {name} is not set")
    return v
