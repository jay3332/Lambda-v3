CREATE TABLE custom_commands (
    name TEXT NOT NULL,
    guild_id BIGINT NOT NULL,
    response TEXT NOT NULL,
    is_python BOOLEAN NOT NULL DEFAULT FALSE,
    required_permissions BIGINT NOT NULL DEFAULT 0,
    toggled_users BIGINT[] NOT NULL DEFAULT '{}',
    toggled_roles BIGINT[] NOT NULL DEFAULT '{}',
    toggled_channels BIGINT[] NOT NULL DEFAULT '{}',
    is_whitelist_toggle BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (name, guild_id)
);