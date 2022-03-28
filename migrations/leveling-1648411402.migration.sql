CREATE TABLE levels (
    guild_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    level INTEGER NOT NULL,
    xp BIGINT NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE level_config (
    guild_id BIGINT NOT NULL PRIMARY KEY,
    module_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    role_stack BOOLEAN NOT NULL DEFAULT TRUE,
    base INTEGER NOT NULL DEFAULT 100,
    factor DOUBLE PRECISION NOT NULL DEFAULT 1.3,
    min_gain INTEGER NOT NULL DEFAULT 8,
    max_gain INTEGER NOT NULL DEFAULT 15,
    cooldown_rate INTEGER NOT NULL DEFAULT 1,
    cooldown_per INTEGER NOT NULL DEFAULT 40,
    level_up_message TEXT NOT NULL DEFAULT '{user.mention}, you just leveled up to level **{level}**!',
    special_level_up_messages JSONB NOT NULL DEFAULT '{}'::JSONB,  -- map {level::TEXT: 'message'}
    level_up_channel BIGINT NOT NULL DEFAULT 1,  -- 0 = don't send, 1 = send to source channel, 2 = DM, else ID of channel
    blacklisted_roles BIGINT[] NOT NULL DEFAULT ARRAY[]::BIGINT[],
    blacklisted_channels BIGINT[] NOT NULL DEFAULT ARRAY[]::BIGINT[],
    blacklisted_users BIGINT[] NOT NULL DEFAULT ARRAY[]::BIGINT[],
    level_roles JSONB NOT NULL DEFAULT '{}'::JSONB,  -- map {role_id::TEXT: level}
    multiplier_roles JSONB NOT NULL DEFAULT '{}'::JSONB,  -- map {role_id::TEXT: multiplier}
    multiplier_channels JSONB NOT NULL DEFAULT '{}'::JSONB,  -- map {channel_id::TEXT: multiplier}
    reset_on_leave BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE rank_cards (
    user_id BIGINT NOT NULL PRIMARY KEY,
    background_color INTEGER NOT NULL DEFAULT 1644825,
    background_url TEXT,
    background_blur SMALLINT NOT NULL DEFAULT 0,  -- between 0 and 20
    background_alpha DOUBLE PRECISION NOT NULL DEFAULT 1.00,  -- the alpha of the IMAGE, not the color
    font SMALLINT NOT NULL DEFAULT 0,  -- look in Font enum
    primary_color INTEGER NOT NULL DEFAULT 12434877,
    secondary_color INTEGER NOT NULL DEFAULT 9671571,
    tertiary_color INTEGER NOT NULL DEFAULT 7064552,
    overlay_color INTEGER NOT NULL DEFAULT 15988735,
    overlay_alpha DOUBLE PRECISION NOT NULL DEFAULT 0.15,
    overlay_border_radius SMALLINT NOT NULL DEFAULT 52,  -- between 0 and 80
    avatar_border_color INTEGER NOT NULL DEFAULT 16777215,
    avatar_border_alpha DOUBLE PRECISION NOT NULL DEFAULT 0.09,
    avatar_border_radius SMALLINT NOT NULL DEFAULT 103,  -- between 0 and 139
    progress_bar_color INTEGER NOT NULL DEFAULT 16777215,
    progress_bar_alpha DOUBLE PRECISION NOT NULL DEFAULT 0.16
);