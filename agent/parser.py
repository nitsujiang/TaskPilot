# parser.py
from agent.prompts import (
    TASK_EXTRACTION_PROMPT,
    CLARIFYING_QUESTION_PROMPT,
    NON_ACTIONABLE_RESPONSE_PROMPT,
)
from utils.slack import send_slack_message_with_fallback, FALLBACK_MESSAGE, BOT_USER_ID, search_workspace_members
from utils.gemini import call_gemini, TaskExtraction
from utils.time import is_valid_deadline, zoneinfo_or_utc
from datetime import datetime
import json
import re


def _build_non_actionable_response(message: str) -> str:
    """Return context-aware guidance for non-actionable messages.

    Uses Gemini first for natural responses, with deterministic fallbacks when
    the model is unavailable.
    """
    llm_prompt = NON_ACTIONABLE_RESPONSE_PROMPT.format(message=message)
    llm_response = call_gemini(llm_prompt)
    if isinstance(llm_response, str) and llm_response.strip():
        return llm_response.strip()

    normalized = re.sub(r"[^a-z0-9\s]", " ", (message or "").lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()

    if "my tasks" in normalized or "assigned" in normalized:
        return "I can do that. Try: `show me my tasks` and I will list your assigned tasks."

    if "meeting" in normalized or "schedule" in normalized:
        return (
            "I can help with that. Try: \"schedule a meeting with @person about <topic> by <date>, low urgency\" "
            "or ask \"show me my tasks\"."
        )

    if "task" in normalized or "todo" in normalized or "remind" in normalized:
        return (
            "I can create and track tasks. Try: \"remind @person to <task> by <date>, high urgency\" "
            "or ask \"show me my tasks\"."
        )

    return (
        "I can help track tasks and schedule meetings. "
        "Try: \"remind @john to fix the login bug by Friday, high urgency\" "
        "or \"show me my tasks\"."
    )

def _recompute_missing_infos(task_data: dict) -> dict:
    """
    Recomputes missing_infos from scratch based on current field values.
    More reliable than trusting the model to track what's been filled in.
    """
    required = ["task", "title", "description", "owners", "urgency"]
    # Todos require an explicit due time. Meetings can proceed without it
    # because we can suggest common slots in a follow-up step.
    if task_data.get("task") == "todo":
        required.append("deadline")
    task_data["missing_infos"] = [field for field in required if not task_data.get(field)]
    # Handle the temporary flag for owners, first check to avoid duplicates
    if "owners" not in task_data["missing_infos"] and task_data.get("_owners_need_clarification"):
        task_data["missing_infos"].append("owners")
        del task_data["_owners_need_clarification"]
    return task_data

def _resolve_invalid_owners(invalid_owners: list) -> tuple[list, str]:
    """
    Attempts to resolve invalid owner names to Slack mentions by searching the workspace.
    Returns (resolved_mentions, suggestion_message).
    resolved_mentions — list of valid <@UID> strings found
    suggestion_message — human readable string of suggestions if any, empty string if none
    """
    resolved = []
    suggestions = []

    for name in invalid_owners:
        # Strip any residual angle brackets or @ symbols from plain names
        clean_name = re.sub(r"[<>@]", "", name).strip()
        matches = search_workspace_members(clean_name)
        if len(matches) == 1:
            # Unambiguous match — resolve automatically
            resolved.append(f"<@{matches[0]['user_id']}>")
        elif len(matches) > 1:
            # Multiple matches — suggest options to user
            options = ", ".join([f"<@{m['user_id']}> ({m['name']})" for m in matches])
            suggestions.append(f"Did you mean one of these for \"{clean_name}\": {options}?")
        # No matches — will be flagged in missing_infos

    suggestion_message = " ".join(suggestions)
    return resolved, suggestion_message

def _validate_owners(task_data: dict) -> dict:
    """
    Validates the owners field to ensure it only contains valid Slack mentions.
    Attempts to resolve invalid entries by searching the workspace.
    If unresolvable, flags owners in missing_infos with suggestions where possible.
    """
    if not task_data.get("owners"):
        return task_data

    # Separate valid mentions from invalid ones
    # U and W prefixes cover standard and Enterprise Grid workspace user IDs
    valid = [o for o in task_data["owners"] if re.match(r"<@[UW][A-Z0-9]+>", o) and o != f"<@{BOT_USER_ID}>"]
    invalid = [o for o in task_data["owners"] if o not in valid]

    if invalid:
        # Attempt to resolve invalid entries via workspace member search
        resolved, suggestion_message = _resolve_invalid_owners(invalid)
        all_owners = list(dict.fromkeys(valid + resolved))

        if suggestion_message:
            # Store suggestions to include in clarifying question context
            task_data["_owners_suggestions"] = suggestion_message

        unresolved_count = len(invalid) - len(resolved)
        if unresolved_count > 0:
            task_data["owners"] = all_owners or None
            task_data["_owners_need_clarification"] = True
        else:
            task_data["owners"] = all_owners

    return task_data

def extract_task(message: str, timezone: str = "UTC") -> dict:
    """
    Takes a Slack message and extracts task details.
    """
    now = datetime.now(tz=zoneinfo_or_utc(timezone)).replace(microsecond=0).isoformat()
    prompt = TASK_EXTRACTION_PROMPT.format(message=message, now=now, timezone=timezone)

    task_data = call_gemini(prompt, schema=TaskExtraction)
    if not task_data:
        return {}

    if task_data.get("deadline") and not is_valid_deadline(task_data["deadline"]):
        print(f"Invalid deadline format detected: {task_data['deadline']}")
        task_data["deadline"] = None

    task_data = _validate_owners(task_data)
    return _recompute_missing_infos(task_data)

def needs_clarification(task_data: dict) -> bool:
    """
    Returns True if critical info is missing or unclear enough to need a follow-up question.
    """
    return len(task_data.get("missing_infos", [])) > 0

def generate_clarifying_question(task_data: dict) -> str:
    missing = set(task_data.get("missing_infos") or [])
    task_type = task_data.get("task")

    def _join(parts: list[str]) -> str:
        if not parts:
            return ""
        if len(parts) == 1:
            return parts[0]
        if len(parts) == 2:
            return f"{parts[0]} and {parts[1]}"
        return f"{', '.join(parts[:-1])}, and {parts[-1]}"

    # Deterministic ordering: ask for core fields first.
    if task_type in {"meeting", "todo"}:
        core_fields = ["title", "description", "urgency", "owners"]
        core_missing = [f for f in core_fields if f in missing]
        if core_missing:
            labels = {
                "title": "title",
                "description": "description",
                "urgency": "urgency (high/medium/low)",
                "owners": "owner(s)",
            }
            asks = [labels[f] for f in core_missing]
            return f"Got it. Could you share the {_join(asks)}?"

        if task_type == "meeting" and "deadline" in missing:
            return "Do you already have a time in mind, or should I suggest common available times?"

        if task_type == "todo" and "deadline" in missing:
            return "Got it. When is this due?"

    prompt = CLARIFYING_QUESTION_PROMPT.format(
        task_data=json.dumps(task_data, indent=2)
    )
    question = call_gemini(prompt)
    # Clean up internal suggestion flags after question is generated
    task_data.pop("_owners_suggestions", None)
    return question

def process_clarification(reply: str, task_data: dict, channel: str, timezone: str = "UTC", thread_ts: str = None) -> dict:
    """
    Takes a user's clarification reply and merges it into the existing task data.
    The distinction is that it handles a reply in an ongoing thread rather than a fresh message,
    so the prompt and merging logic are different.
    """
    now = datetime.now(tz=zoneinfo_or_utc(timezone)).replace(microsecond=0).isoformat()
    prompt = TASK_EXTRACTION_PROMPT.format(message=reply, now=now, timezone=timezone)

    new_data = call_gemini(prompt, schema=TaskExtraction)
    if not new_data:
        return task_data

    # Merge — overwrite fields found in reply.
    # Keep task type stable once established to avoid flipping meeting <-> todo on short clarifications.
    for field in ["title", "description", "deadline", "urgency"]:
        if new_data.get(field) is not None:
            task_data[field] = new_data[field]
    if new_data.get("task") and not task_data.get("task"):
        task_data["task"] = new_data["task"]

    # Owners — append new owners rather than replace
    if new_data.get("owners"):
        # If owners was previously None, treat it as an empty list for merging.
        existing_owners = task_data.get("owners") or []
        # merge without duplicates
        task_data["owners"] = list(dict.fromkeys(existing_owners + new_data["owners"]))

    if task_data.get("deadline") and not is_valid_deadline(task_data["deadline"]):
        print(f"Invalid deadline format detected: {task_data['deadline']}")
        task_data["deadline"] = None

    task_data = _validate_owners(task_data)
    task_data = _recompute_missing_infos(task_data)

    if needs_clarification(task_data):
        question = generate_clarifying_question(task_data)
        send_slack_message_with_fallback(channel, question or FALLBACK_MESSAGE, thread_ts=thread_ts)

    return task_data

def process_message(message: str, channel: str, timezone: str = "UTC", thread_ts: str = None) -> dict:
    """
    Main function -- takes a message and decides what to do.
    """
    print(f"Processing message: {message}\n")

    task_data = extract_task(message, timezone)
    if not task_data:
        print("Failed to extract data from message, skipping.")
        send_slack_message_with_fallback(channel, FALLBACK_MESSAGE, thread_ts=thread_ts)
        return {}

    print(f"Extracted: {json.dumps(task_data, indent=2)}\n")

    # Message was not actionable — guide the user
    if task_data.get("task") is None:
        send_slack_message_with_fallback(
            channel,
            _build_non_actionable_response(message),
            thread_ts=thread_ts
        )
        return {}

    if needs_clarification(task_data):
        question = generate_clarifying_question(task_data)
        send_slack_message_with_fallback(channel, question or FALLBACK_MESSAGE, thread_ts=thread_ts)
    else:
        print("All details present -- ready to save to database and send reminders")

    return task_data