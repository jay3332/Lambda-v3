CREATE TABLE guilds (
    guild_id BIGINT PRIMARY KEY,
    prefixes TEXT[] NOT NULL DEFAULT ARRAY['>']::TEXT[]
);