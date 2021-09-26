CREATE TABLE IF NOT EXISTS rank_cards (
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
