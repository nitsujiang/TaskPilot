CREATE TABLE tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task TEXT,
    owner TEXT,
    deadline TEXT,
    status TEXT,
    urgency TEXT,
    created_at TEXT
);