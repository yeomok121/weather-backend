CREATE TABLE IF NOT EXISTS admin_member_suspensions (
    user_id INT PRIMARY KEY,
    reason VARCHAR(500) NOT NULL,
    until_at DATETIME NULL,
    CONSTRAINT fk_admin_suspension_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS admin_member_action_notes (
    audit_id BIGINT PRIMARY KEY,
    reason VARCHAR(500) NOT NULL,
    CONSTRAINT fk_admin_member_note_audit FOREIGN KEY (audit_id) REFERENCES admin_audit_log(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
