CREATE TABLE IF NOT EXISTS profiles (
    profile TEXT PRIMARY KEY,
    creds_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
