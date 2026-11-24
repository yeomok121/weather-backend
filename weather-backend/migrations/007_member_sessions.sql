CREATE TABLE IF NOT EXISTS member_sessions (
    token_digest BINARY(32) NOT NULL PRIMARY KEY,
    user_id INT NOT NULL,
    token_version INT NOT NULL,
    expires_at BIGINT NOT NULL,
    KEY idx_member_sessions_expiry (expires_at),
    CONSTRAINT fk_member_sessions_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
