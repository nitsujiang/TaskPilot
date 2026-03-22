CREATE TABLE tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task TEXT,
    owner TEXT,
    deadline TEXT,
    status TEXT,
    urgency TEXT,
    created_at TEXT
    -- TODO: add attendees column when meeting scheduling is implemented
);