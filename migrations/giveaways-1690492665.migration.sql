CREATE TABLE IF NOT EXISTS giveaways (
    id BIGSERIAL UNIQUE PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    timer_id BIGINT NOT NULL REFERENCES timers(id) ON DELETE CASCADE,
    level_requirement INT NOT NULL,
    roles_requirement BIGINT[] NOT NULL,
    prize TEXT NOT NULL,
    winners INT NOT NULL
);

CREATE TABLE IF NOT EXISTS giveaway_entrants (
    giveaway_id BIGINT NOT NULL REFERENCES giveaways(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL,
    PRIMARY KEY (giveaway_id, user_id)
);

ALTER TABLE guilds
    ADD COLUMN IF NOT EXISTS giveaway_role_id BIGINT;
