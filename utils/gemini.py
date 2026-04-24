from google import genai
from google.genai import types
from pydantic import BaseModel
from typing import Optional, List, Literal
import os
import json
import re

gemini_token = os.getenv("GEMINI_API_KEY")
if not gemini_token:
    raise ValueError("GEMINI_API_KEY is not set")

gemini_client = genai.Client(api_key=gemini_token)

# Set when the last `call_gemini` hit an API/transport error (not empty JSON). Consumed via `take_last_gemini_user_hint()`.
_last_gemini_user_hint: str | None = None


def _set_failure_hint_from_error(exc: BaseException) -> None:
    global _last_gemini_user_hint
    text = f"{type(exc).__name__} {exc}".lower()
    code_blob = str(getattr(exc, "message", exc))
    if hasattr(exc, "response") and exc.response is not None:
        try:
            code_blob += str(getattr(exc.response, "data", exc.response))
        except Exception:
            pass
    blob = f"{text} {code_blob}".lower()

    if re.search(r"\b429\b|resource_exhausted|quota|rate.?limit|generate_requests|billing|free_tier", blob):
        _last_gemini_user_hint = (
            "The AI service hit a usage or quota limit right now. "
            "Please try again in a few minutes, or check your Gemini API plan and project quota."
        )
        return
    if re.search(r"\b503\b|\b502\b|unavailable|overloaded|high demand|try again later", blob):
        _last_gemini_user_hint = (
            "The AI service is temporarily busy or unavailable. Please try again in a few minutes."
        )
        return
    if re.search(r"\b500\b|internal error|internal_error", blob):
        _last_gemini_user_hint = (
            "The AI service returned an error. Please try again shortly; if it keeps happening, check API status and your key."
        )
        return
    if re.search(r"api key|invalid.*key|401|403|permission|unauthorized", blob):
        _last_gemini_user_hint = (
            "There is a problem with the Gemini API key or permissions. Check GEMINI_API_KEY in your environment."
        )
        return
    _last_gemini_user_hint = (
        "I could not reach the AI service just now. Please try again in a moment."
    )


def take_last_gemini_user_hint() -> str | None:
    """Return the user-facing message for the last failed Gemini call, if any, and clear it."""
    global _last_gemini_user_hint
    h = _last_gemini_user_hint
    _last_gemini_user_hint = None
    return h


class TaskExtraction(BaseModel):
    task: Optional[Literal["todo", "meeting"]] = None
    title: Optional[str] = None
    description: Optional[str] = None
    owners: Optional[List[str]] = None
    deadline: Optional[str] = None
    urgency: Optional[Literal["high", "medium", "low"]] = None
    missing_infos: List[Literal["task", "title", "description", "owners", "deadline", "urgency"]] = []


def call_gemini(prompt: str, schema: type[BaseModel] = None) -> str | dict:
    """
    Call Gemini with ``prompt``.

    If ``schema`` is set, return a parsed dict (JSON mode). Otherwise return plain text.

    On transport/API failure, return an empty dict or string and set a user hint via
    ``take_last_gemini_user_hint()`` (quota, busy, key issues). User typos alone do not trigger these.
    """
    global _last_gemini_user_hint
    _last_gemini_user_hint = None

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=schema
    ) if schema else None

    try:
        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=config
        )
    except Exception as e:
        print(f"Failed to call Gemini: {e}")
        _set_failure_hint_from_error(e)
        return {} if schema else ""

    text = response.text
    if text is None:
        print("Empty response from Gemini")
        return {} if schema else ""

    if schema:
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError) as e:
            print(f"Invalid JSON from Gemini: {e}")
            # Model bug / garbled output — not a user typo; avoid blaming quota
            return {}
    return text.strip()