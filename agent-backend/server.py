import secrets
import logging
import os
import json
import hmac
import base64
import hashlib
import html
import urllib.request
from datetime import datetime, timezone

import psycopg2
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, PlainTextResponse, HTMLResponse

from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest
from googleapiclient.discovery import build

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

FLOW_STORE = {}
PENDING_STATE_PROFILES = {}
PENDING_FLOWS_BY_STATE_ID = {}
app = FastAPI()

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]

def env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"Missing environment variable: {name}")
    return v

def conn():
    return psycopg2.connect(env("DATABASE_URL"))

def init_db():
    c = conn()
    try:
        with c, c.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS profiles (
                    profile TEXT PRIMARY KEY,
                    creds_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
    finally:
        c.close()

@app.on_event("startup")
def startup():
    init_db()

def client_config():
    return {
        "web": {
            "client_id": env("GOOGLE_CLIENT_ID"),
            "client_secret": env("GOOGLE_CLIENT_SECRET"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [f"{env('APP_EXTERNAL_URL')}/auth/google/callback"],
        }
    }

def sign_state(payload: dict) -> str:
    secret = env("STATE_SIGNING_SECRET").encode("utf-8")
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(secret, raw, hashlib.sha256).digest()
    blob = base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")
    sigb = base64.urlsafe_b64encode(sig).decode("utf-8").rstrip("=")
    return f"{blob}.{sigb}"

def verify_state(state: str) -> dict:
    secret = env("STATE_SIGNING_SECRET").encode("utf-8")
    try:
        blob, sigb = state.split(".", 1)
        raw = base64.urlsafe_b64decode(blob + "==")
        sig = base64.urlsafe_b64decode(sigb + "==")
    except Exception:
        raise HTTPException(status_code=400, detail="Bad state format.")
    expected = hmac.new(secret, raw, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(status_code=400, detail="Bad state signature.")
    return json.loads(raw.decode("utf-8"))

def require_key(request: Request):
    if request.headers.get("X-Backend-Key") != env("BACKEND_API_KEY"):
        raise HTTPException(status_code=401, detail="Missing or invalid X-Backend-Key.")

def load_creds(profile: str) -> Credentials:
    c = conn()
    try:
        with c, c.cursor() as cur:
            cur.execute("SELECT creds_json FROM profiles WHERE profile = %s", (profile,))
            row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Unknown profile '{profile}'. Connect it first.")
        info = json.loads(row[0])
        return Credentials.from_authorized_user_info(info, scopes=SCOPES)
    finally:
        c.close()

def save_creds(profile: str, creds: Credentials):
    c = conn()
    try:
        with c, c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO profiles(profile, creds_json, created_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (profile)
                DO UPDATE SET creds_json = EXCLUDED.creds_json, created_at = EXCLUDED.created_at
                """,
                (profile, creds.to_json(), datetime.now(timezone.utc).isoformat()),
            )
    finally:
        c.close()

def ensure_fresh(creds: Credentials) -> Credentials:
    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleRequest())
    return creds

@app.get("/connect/google")
def connect_google(profile: str = "", state_id: str = ""):
    # OAuth-first flow: if state_id is provided, we'll get the user's email from Google after sign-in
    # and the notebook can poll GET /profiles/by_state?state_id=... to get it. No manual email entry.
    state_id = (state_id or "").strip()
    profile = (profile or "").strip()
    if state_id:
        profile = "__pending__"  # will be replaced with email in callback
    elif not profile:
        raise HTTPException(status_code=400, detail="Provide profile=YOUR_EMAIL or state_id=... for OAuth-first flow.")

    base_url = env("APP_EXTERNAL_URL")
    redirect_uri = f"{base_url}/auth/google/callback"

    flow = Flow.from_client_config(
        client_config(),
        scopes=SCOPES,
        redirect_uri=redirect_uri,
    )

    state = sign_state(
        {
            "profile": profile,
            "state_id": state_id or None,
            "nonce": secrets.token_urlsafe(16),
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    )

    auth_url, _ = flow.authorization_url(
        state=state,
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )

    FLOW_STORE[state] = flow
    if state_id:
        PENDING_FLOWS_BY_STATE_ID[state_id] = flow
    return RedirectResponse(auth_url)

def _err_html(message: str) -> HTMLResponse:
    """Return HTML error page (status 200 so the browser displays it)."""
    escaped = html.escape(str(message))
    return HTMLResponse(
        content=f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Sign-in error</title></head>"
        f"<body style='font-family:sans-serif;max-width:520px;margin:2em auto;padding:1em'>"
        f"<h2>Sign-in error</h2><p>{escaped}</p>"
        f"<p>Close this tab, run the notebook profile cell again, and open the <strong>new</strong> link.</p></body></html>",
        status_code=200,
    )


@app.get("/auth/google/callback")
def google_callback(code: str, state: str):
    try:
        payload = verify_state(state)
    except Exception as e:
        logger.exception("Callback: invalid state")
        return _err_html(f"Invalid state: {e!s}")

    profile = payload["profile"]
    state_id = payload.get("state_id")

    flow = FLOW_STORE.pop(state, None) or (PENDING_FLOWS_BY_STATE_ID.pop(state_id, None) if state_id else None)
    if flow is None:
        logger.warning("Callback: flow not found state_id=%s", state_id)
        return _err_html("Login state expired or server restarted. Run the notebook profile cell again and open the new link.")
    FLOW_STORE.pop(state, None)

    try:
        flow.fetch_token(code=code)
    except Exception as e:
        logger.exception("Callback: token exchange failed")
        return _err_html(f"Token exchange failed: {e!s}")

    creds = flow.credentials

    if state_id and profile == "__pending__":
        access_token = getattr(creds, "token", None) or getattr(creds, "access_token", None)
        if not access_token:
            return _err_html("No access token; cannot fetch email.")
        req = urllib.request.Request(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:
            logger.exception("Callback: userinfo failed")
            return _err_html(f"Userinfo request failed: {e!s}")
        profile = data.get("email", "").strip()
        if not profile:
            return _err_html("Could not get email from Google.")
        PENDING_STATE_PROFILES[state_id] = profile

    save_creds(profile, creds)
    return PlainTextResponse(f"Connected as {profile}. You can close this tab.")

@app.get("/calendar/list")
def calendar_list(request: Request, profile: str, max_results: int = 10):
    require_key(request)
    creds = ensure_fresh(load_creds(profile))
    service = build("calendar", "v3", credentials=creds)

    now = datetime.now(timezone.utc).isoformat()
    events_result = service.events().list(
        calendarId="primary",
        timeMin=now,
        maxResults=int(max_results),
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    events = events_result.get("items", [])
    return [
        {
            "summary": e.get("summary", "(no title)"),
            "start": e["start"].get("dateTime", e["start"].get("date")),
            "end": e["end"].get("dateTime", e["end"].get("date")),
        }
        for e in events
    ]

@app.get("/drive/search")
def drive_search(request: Request, profile: str, query: str):
    require_key(request)
    creds = ensure_fresh(load_creds(profile))
    service = build("drive", "v3", credentials=creds)

    query = (query or "").strip()
    # Break into keywords and OR them for higher recall.
    words = [w.lower() for w in query.replace("\n", " ").split(" ") if len(w.strip()) >= 3]
    seen = []
    for w in words:
        w = w.strip(" ,.;:()[]{}\"")
        if not w or w in seen:
            continue
        seen.append(w)
        if len(seen) >= 6:
            break
    if not seen:
        seen = [query[:40]] if query else ["meeting"]
    clauses = []
    for w in seen:
        w_escaped = w.replace("'", "\\'")
        clauses.append(f"fullText contains '{w_escaped}'")
    q = " or ".join(clauses)
    results = service.files().list(
        q=q,
        pageSize=10,
        fields="files(id, name, mimeType, webViewLink)",
    ).execute()

    items = results.get("files", [])
    return [
        {
            "name": it.get("name"),
            "id": it.get("id"),
            "mimeType": it.get("mimeType"),
            "webViewLink": it.get("webViewLink"),
        }
        for it in items
        if it.get("id") and it.get("name")
    ]

@app.post("/calendar/create")
def calendar_create(
    request: Request,
    profile: str,
    summary: str,
    start_iso: str,
    end_iso: str,
    description: str = "",
    timezone_name: str = "UTC",
):
    """Create a calendar event for a connected profile."""
    require_key(request)
    creds = ensure_fresh(load_creds(profile))
    service = build("calendar", "v3", credentials=creds)

    body = {
        "summary": summary.strip() or "(no title)",
        "description": description or "",
        "start": {"dateTime": start_iso, "timeZone": timezone_name},
        "end": {"dateTime": end_iso, "timeZone": timezone_name},
    }
    ev = service.events().insert(calendarId="primary", body=body).execute()
    return {
        "id": ev.get("id"),
        "htmlLink": ev.get("htmlLink"),
        "summary": ev.get("summary"),
        "start": ev.get("start", {}).get("dateTime"),
        "end": ev.get("end", {}).get("dateTime"),
    }


@app.post("/calendar/update_description")
def calendar_update_description(
    request: Request,
    profile: str,
    event_id: str,
    description: str,
):
    """Overwrite an event description for a connected profile."""
    require_key(request)
    creds = ensure_fresh(load_creds(profile))
    service = build("calendar", "v3", credentials=creds)
    ev = (
        service.events()
        .patch(calendarId="primary", eventId=event_id, body={"description": description or ""})
        .execute()
    )
    return {"id": ev.get("id"), "htmlLink": ev.get("htmlLink")}


@app.get("/profiles/check")
def profiles_check(request: Request, profile: str):
    """Returns whether this profile has connected Google (Calendar/Drive). Used by the notebook."""
    require_key(request)
    profile = profile.strip()
    if not profile:
        raise HTTPException(status_code=400, detail="Profile is required.")
    c = conn()
    try:
        with c, c.cursor() as cur:
            cur.execute("SELECT 1 FROM profiles WHERE profile = %s", (profile,))
            connected = cur.fetchone() is not None
        return {"profile": profile, "connected": connected}
    finally:
        c.close()


@app.get("/profiles/by_state")
def profiles_by_state(request: Request, state_id: str):
    """After OAuth-first connect, notebook polls this with state_id to get the connected profile (email)."""
    require_key(request)
    state_id = state_id.strip()
    if not state_id:
        raise HTTPException(status_code=400, detail="state_id is required.")
    profile = PENDING_STATE_PROFILES.pop(state_id, None)
    if profile is None:
        raise HTTPException(status_code=404, detail="Not found or already consumed. Complete OAuth first.")
    return {"profile": profile}