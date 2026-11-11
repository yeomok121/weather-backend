"""공통: DB 테이블 자동 생성(CREATE TABLE IF NOT EXISTS).

테이블 구조를 바꿀 때는 migrations/ 에 SQL 파일도 함께 추가하고 팀에 공유해 주세요.
"""
import threading

_favorites_schema_ready = False
_favorites_schema_lock = threading.Lock()
_hiking_schema_ready = False
_hiking_schema_lock = threading.Lock()
_admin_schema_ready = False
_admin_schema_lock = threading.Lock()

def _ensure_admin_tables(cur):
    global _admin_schema_ready
    if _admin_schema_ready:
        return
    with _admin_schema_lock:
        if _admin_schema_ready:
            return
        cur.execute('''CREATE TABLE IF NOT EXISTS admin_user_metadata (
            user_id INT NOT NULL PRIMARY KEY, role VARCHAR(24) NOT NULL DEFAULT 'member',
            suspended TINYINT(1) NOT NULL DEFAULT 0, token_version INT NOT NULL DEFAULT 0,
            registered_at TIMESTAMP NULL DEFAULT NULL,
            CONSTRAINT fk_admin_metadata_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')
        cur.execute('''CREATE TABLE IF NOT EXISTS admin_audit_log (
            id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY, actor VARCHAR(120) NOT NULL,
            action VARCHAR(40) NOT NULL, target_id INT NULL, target_username VARCHAR(120) NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            KEY idx_admin_audit_target (target_id, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')
        _admin_schema_ready = True

def _ensure_user_favorites_table(cur):
    global _favorites_schema_ready
    if _favorites_schema_ready:
        return
    with _favorites_schema_lock:
        if _favorites_schema_ready:
            return
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_favorites (
                id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                name VARCHAR(120) NOT NULL,
                latitude DECIMAL(10, 7) NOT NULL,
                longitude DECIMAL(10, 7) NOT NULL,
                sort_order TINYINT UNSIGNED NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_user_favorites_name (user_id, name),
                UNIQUE KEY uq_user_favorites_order (user_id, sort_order),
                CONSTRAINT fk_user_favorites_user FOREIGN KEY (user_id)
                    REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        _favorites_schema_ready = True

def _ensure_hiking_tables(cur):
    global _hiking_schema_ready
    if _hiking_schema_ready:
        return
    with _hiking_schema_lock:
        if _hiking_schema_ready:
            return
        statements = [
            """CREATE TABLE IF NOT EXISTS hiking_plans (
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
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
            """CREATE TABLE IF NOT EXISTS hiking_parties (
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
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
            """CREATE TABLE IF NOT EXISTS hiking_party_members (
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
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
            """CREATE TABLE IF NOT EXISTS hiking_party_invites (
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
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
            """CREATE TABLE IF NOT EXISTS hiking_party_items (
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
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
        ]
        statements.append("""CREATE TABLE IF NOT EXISTS hiking_party_schedule_log (
            id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, party_id INT NOT NULL,
            actor_name VARCHAR(120) NOT NULL, old_schedule TEXT NOT NULL, new_schedule TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            KEY idx_party_schedule_log (party_id, id),
            CONSTRAINT fk_party_schedule_log_party FOREIGN KEY (party_id) REFERENCES hiking_parties(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        for statement in statements:
            cur.execute(statement)
        _hiking_schema_ready = True
