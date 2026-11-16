-- Scheduled thesis digests (alpha; docs/database_design.md). One row per digest schedule: the cron
-- expression is evaluated in the IANA timezone, and next_run_at holds the next occurrence in UTC.
-- thesis_ids is a plain array (no join table); the app validates them at registration and only
-- delivers active theses. Cancelling sets is_active = false; rows are never deleted.
CREATE TABLE cron_subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT NOT NULL,
    cron TEXT NOT NULL,
    timezone TEXT NOT NULL,
    thesis_ids UUID[] NOT NULL CHECK (cardinality(thesis_ids) > 0),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    next_run_at TIMESTAMPTZ NOT NULL,
    last_delivered_at TIMESTAMPTZ,
    last_delivery_status TEXT CHECK (last_delivery_status IN ('sent', 'failed', 'disabled', 'skipped')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX cron_subscriptions_due_idx ON cron_subscriptions (next_run_at) WHERE is_active;
