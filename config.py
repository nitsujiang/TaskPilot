from dotenv import load_dotenv
import os

# Load env files from repo root, then agent-backend/.env for keys only present there.
# (load_dotenv does not override existing vars by default.)
_root = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_root, ".env"))
load_dotenv(os.path.join(_root, "agent-backend", ".env"))


# --- Misc helpers ---
def require_env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Required env var {name} is not set")
    return v


# --- General ---
APP_VERSION = os.getenv("APP_VERSION", "1.0")

# --- Backend / agent-backend ---
DATABASE_URL = require_env("DATABASE_URL")
GOOGLE_CLIENT_ID = require_env("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = require_env("GOOGLE_CLIENT_SECRET")
APP_EXTERNAL_URL = os.getenv("APP_EXTERNAL_URL", "http://localhost:8000")
BACKEND_API_KEY = require_env("BACKEND_API_KEY")
STATE_SIGNING_SECRET = require_env("STATE_SIGNING_SECRET")

# --- Notebook / LLM ---
GOOGLE_API_KEY_AI = os.getenv("GOOGLE_API_KEY_AI")
GEMINI_API_KEY = require_env("GEMINI_API_KEY")
PROFILE_EMAIL = os.getenv("PROFILE_EMAIL")

# --- Slackbot (Flask app) ---
SLACK_BOT_TOKEN = require_env("SLACK_BOT_TOKEN")
SLACK_SIGNING_SECRET = require_env("SLACK_SIGNING_SECRET")
SLACK_TEAM_ID = os.getenv("SLACK_TEAM_ID")

# --- Frontend / proxy ---
# Use API_ENDPOINT as the backend base URL. Frontend will append paths like /health.
# If API_ENDPOINT is not set, default to localhost backend used during development.
API_ENDPOINT = os.getenv("API_ENDPOINT", APP_EXTERNAL_URL)
