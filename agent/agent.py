from google import genai
from dotenv import load_dotenv
from agent.prompts import TASK_EXTRACTION_PROMPT, CLARIFYING_QUESTION_PROMPT
import os
import json

load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


def extract_task(message: str) -> dict:
    """
    Takes a Slack message and extracts task details.
    """
    prompt = TASK_EXTRACTION_PROMPT.format(message=message)

    response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)

    # Parse the JSON response
    text = response.text
    if text is None:
        print("Warning: No response from model")
        return {}
    # strip markdown code blocks if present
    text = text.strip()
    text = text.replace("```json", "").replace("```", "").strip()

    task_data = json.loads(text)
    return task_data


def needs_clarification(task_data: dict) -> bool:
    """
    Returns True if critical info is missing
    """
    return len(task_data.get("missing_info", [])) > 0


def generate_clarifying_question(task_data: dict) -> str:
    """
    Generates a follow-up slack message if info is missing.
    """

    prompt = CLARIFYING_QUESTION_PROMPT.format(
        task=task_data.get("task", ""), missing_info=task_data.get("missing_info", [])
    )

    response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)

    text = response.text
    if text is None:
        print("Warning: No response from model")
        return ""
    return text.strip()


def process_message(message: str):
    """
    Main function -- takes a message and decides what to do.
    """
    print(f"Processing message: {message}\n")

    task_data = extract_task(message)
    print(f"Extracted: {json.dumps(task_data, indent=2)}\n")

    if needs_clarification(task_data):
        question = generate_clarifying_question(task_data)
        print(f"Clarifying question to post in Slack:\n{question}")
    else:
        print("All details present -- ready to save to database and send reminders")

    return task_data
