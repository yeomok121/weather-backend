CREATE TABLE IF NOT EXISTS hiking_party_item_modes (
    item_id INT NOT NULL PRIMARY KEY,
    kind VARCHAR(12) NOT NULL,
    unit VARCHAR(12) NOT NULL DEFAULT '',
    CONSTRAINT fk_hiking_gear_mode_item FOREIGN KEY (item_id) REFERENCES hiking_party_items(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS hiking_party_item_members (
    item_id INT NOT NULL,
    party_id INT NOT NULL,
    user_id INT NOT NULL,
    quantity SMALLINT UNSIGNED NOT NULL,
    is_packed TINYINT(1) NOT NULL DEFAULT 0,
    PRIMARY KEY (item_id, user_id),
    CONSTRAINT fk_hiking_gear_members_item FOREIGN KEY (item_id) REFERENCES hiking_party_items(id) ON DELETE CASCADE,
    CONSTRAINT fk_hiking_gear_members_party FOREIGN KEY (party_id, user_id) REFERENCES hiking_party_members(party_id, user_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
