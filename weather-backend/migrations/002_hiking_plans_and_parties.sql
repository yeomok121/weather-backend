CREATE TABLE IF NOT EXISTS hiking_plans (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    name VARCHAR(120) NOT NULL,
    mountain_name VARCHAR(120) NOT NULL,
    course_index SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    course_name VARCHAR(180) NOT NULL,
    weather_date VARCHAR(24) NOT NULL DEFAULT '',
    selected_date_idx TINYINT UNSIGNED NOT NULL DEFAULT 0,
    pace VARCHAR(16) NOT NULL DEFAULT 'normal',
    departure_offset_hours SMALLINT NOT NULL DEFAULT 0,
    departure_at VARCHAR(40) NOT NULL DEFAULT '',
    items_json LONGTEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_hiking_plans_user_updated (user_id, updated_at),
    CONSTRAINT fk_hiking_plans_user FOREIGN KEY (user_id)
        REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS hiking_parties (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    owner_user_id INT NOT NULL,
    plan_id INT NULL,
    name VARCHAR(120) NOT NULL,
    mountain_name VARCHAR(120) NOT NULL,
    course_name VARCHAR(180) NOT NULL,
    weather_date VARCHAR(24) NOT NULL DEFAULT '',
    pace VARCHAR(16) NOT NULL DEFAULT 'normal',
    departure_offset_hours SMALLINT NOT NULL DEFAULT 0,
    departure_at VARCHAR(40) NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_hiking_parties_owner (owner_user_id),
    CONSTRAINT fk_hiking_parties_owner FOREIGN KEY (owner_user_id)
        REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT fk_hiking_parties_plan FOREIGN KEY (plan_id)
        REFERENCES hiking_plans(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS hiking_party_members (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    party_id INT NOT NULL,
    user_id INT NOT NULL,
    role VARCHAR(16) NOT NULL DEFAULT 'member',
    joined_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_hiking_party_member (party_id, user_id),
    CONSTRAINT fk_hiking_party_members_party FOREIGN KEY (party_id)
        REFERENCES hiking_parties(id) ON DELETE CASCADE,
    CONSTRAINT fk_hiking_party_members_user FOREIGN KEY (user_id)
        REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS hiking_party_invites (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    party_id INT NOT NULL,
    invited_user_id INT NOT NULL,
    invited_by_user_id INT NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_hiking_party_invite (party_id, invited_user_id),
    KEY idx_hiking_party_invites_user_status (invited_user_id, status),
    CONSTRAINT fk_hiking_party_invites_party FOREIGN KEY (party_id)
        REFERENCES hiking_parties(id) ON DELETE CASCADE,
    CONSTRAINT fk_hiking_party_invites_invited FOREIGN KEY (invited_user_id)
        REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT fk_hiking_party_invites_sender FOREIGN KEY (invited_by_user_id)
        REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS hiking_party_items (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    party_id INT NOT NULL,
    name VARCHAR(120) NOT NULL,
    quantity SMALLINT UNSIGNED NOT NULL DEFAULT 1,
    assigned_to_user_id INT NULL,
    is_packed TINYINT(1) NOT NULL DEFAULT 0,
    created_by_user_id INT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_hiking_party_item_name (party_id, name),
    CONSTRAINT fk_hiking_party_items_party FOREIGN KEY (party_id)
        REFERENCES hiking_parties(id) ON DELETE CASCADE,
    CONSTRAINT fk_hiking_party_items_assignee FOREIGN KEY (assigned_to_user_id)
        REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT fk_hiking_party_items_creator FOREIGN KEY (created_by_user_id)
        REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
