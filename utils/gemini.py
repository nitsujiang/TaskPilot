from google import genai
from google.genai import types
from pydantic import BaseModel
from typing import Optional, List, Literal
import os
import json

gemini_token = os.getenv("GEMINI_API_KEY")
if not gemini_token:
    raise ValueError("GEMINI_API_KEY is not set")

gemini_client = genai.Client(api_key=gemini_token)

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
    Calls Gemini with a prompt.
    If schema is provided, returns a parsed dict with JSON mode enabled.
    Otherwise returns a plain text string.
    """
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
        return {} if schema else ""

    text = response.text
    if text is None:
        print("Empty response from Gemini")
        return {} if schema else ""

    return json.loads(text) if schema else text.strip()