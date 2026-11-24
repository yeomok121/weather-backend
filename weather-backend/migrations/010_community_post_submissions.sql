CREATE TABLE IF NOT EXISTS community_post_submissions (
    user_id INT NOT NULL,
    submission_key BINARY(16) NOT NULL,
    payload_digest BINARY(32) NOT NULL,
    post_id INT NULL,
    PRIMARY KEY (user_id, submission_key),
    CONSTRAINT fk_community_submission_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT fk_community_submission_post FOREIGN KEY (post_id) REFERENCES community_posts(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
