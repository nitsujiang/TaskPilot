from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import time
import re
from config import SLACK_BOT_TOKEN

if not SLACK_BOT_TOKEN:
    raise ValueError("SLACK_BOT_TOKEN is not set")

slack_client = WebClient(token=SLACK_BOT_TOKEN)
FALLBACK_MESSAGE = "Sorry, I had trouble processing that message. Please try again or contact your admin."

# Fetch bot user ID at startup to identify and filter out self-mentions
bot_info = slack_client.auth_test()
BOT_USER_ID = bot_info["user_id"]

def get_user_timezone(user_id: str) -> str:
    try:
        user_info = slack_client.users_info(user=user_id)
        return user_info["user"].get("tz", "UTC")
    except SlackApiError as e:
        print(f"Failed to fetch timezone for {user_id}: {e.response['error']}")
        return "UTC"


def get_user_email(user_id: str) -> str | None:
    """Return a Slack user's profile email if available."""
    try:
        user_info = slack_client.users_info(user=user_id)
        email = (user_info.get("user", {}).get("profile", {}).get("email") or "").strip()
        return email or None
    except SlackApiError as e:
        print(f"Failed to fetch email for {user_id}: {e.response['error']}")
        return None


def resolve_owner_mentions_to_emails(owners: list[str]) -> list[str]:
    """
    Convert owners like <@U123ABC> to workspace emails via users.info.
    Returns unique emails in input order.
    """
    out = []
    for owner in owners or []:
        m = re.match(r"<@([UW][A-Z0-9]+)>", owner or "")
        if not m:
            continue
        email = get_user_email(m.group(1))
        if email and email not in out:
            out.append(email)
    return out

_members_cache = None
_members_cache_time = 0
MEMBERS_CACHE_TTL = 300  # 5 minutes; would be stale if members are added/removed but reduces API calls for large workspaces

def search_workspace_members(query: str) -> list:
    """
    Searches workspace members by name query.
    Returns list of {name, user_id} for non-bot, active members that match.
    Caches the full member list for 5 minutes to avoid repeated API calls.
    """
    global _members_cache, _members_cache_time

    try:
        if _members_cache is None or time.time() - _members_cache_time > MEMBERS_CACHE_TTL:
            response = slack_client.users_list()
            _members_cache = [
                m for m in response["members"]
                if not m["is_bot"] and not m["deleted"]
            ]
            _members_cache_time = time.time()

        return [
            {"name": m["real_name"], "user_id": m["id"]}
            for m in _members_cache
            if query.lower() in m["real_name"].lower()
        ]
    except SlackApiError as e:
        print(f"Failed to fetch workspace members: {e.response['error']}")
        return []

def send_slack_message(channel: str, text: str, thread_ts: str = None) -> bool:
    try:
        slack_client.chat_postMessage(channel=channel, text=text, thread_ts=thread_ts)
        return True
    except SlackApiError as e:
        print(f"Failed to send message to Slack: {e.response['error']}")
        return False

def send_slack_message_with_fallback(channel: str, text: str, thread_ts: str = None) -> None:
    if not send_slack_message(channel, text, thread_ts):
        send_slack_message(channel, FALLBACK_MESSAGE, thread_ts)
