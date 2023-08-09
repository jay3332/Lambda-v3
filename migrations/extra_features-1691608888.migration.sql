ALTER TABLE guilds
    ADD COLUMN invite_tracking_channel_id BIGINT,
    ADD COLUMN welcome_message TEXT,
    ADD COLUMN welcome_channel_id BIGINT;

CREATE TABLE IF NOT EXISTS triggers (
    id BIGSERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL REFERENCES guilds(guild_id) ON DELETE CASCADE,
    trigger TEXT NOT NULL,
    type INTEGER NOT NULL,
    response TEXT NOT NULL,
    UNIQUE (guild_id, trigger)
);
