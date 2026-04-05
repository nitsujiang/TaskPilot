CREATE TABLE IF NOT EXISTS tasks (
    id BIGSERIAL PRIMARY KEY,
    task_type TEXT,
    title TEXT,
    description TEXT,
    owners_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    channel TEXT,
    thread_ts TEXT,
    deadline TIMESTAMPTZ,
    status TEXT, -- Not part of the extraction but useful for marking tasks as complete
    urgency TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tasks_deadline ON tasks(deadline);
CREATE INDEX IF NOT EXISTS idx_tasks_channel_thread ON tasks(channel, thread_ts);