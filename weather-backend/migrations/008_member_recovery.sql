CREATE TABLE IF NOT EXISTS member_recovery_email (
    user_id INT NOT NULL PRIMARY KEY,
    email VARCHAR(254) NOT NULL,
    CONSTRAINT fk_member_recovery_email_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS member_recovery_codes (
    user_id INT NOT NULL,
    purpose VARCHAR(12) NOT NULL,
    email VARCHAR(254) NOT NULL,
    code_digest BINARY(32) NOT NULL,
    token_version INT NOT NULL,
    expires_at BIGINT NOT NULL,
    PRIMARY KEY (user_id, purpose),
    CONSTRAINT fk_member_recovery_codes_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
