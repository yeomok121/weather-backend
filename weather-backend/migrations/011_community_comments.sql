-- 낚시 커뮤니티 댓글 + 글쓴이 하트. 서버가 처음 쓸 때 app/community/store.py 에서도 자동으로 만듭니다.
CREATE TABLE IF NOT EXISTS community_comments (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    post_id INT NOT NULL,
    user_id INT NOT NULL,
    body VARCHAR(500) NOT NULL,
    author_hearted TINYINT(1) NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL,
    KEY idx_community_comments_post (post_id, id),
    KEY idx_community_comments_user (user_id, created_at),
    CONSTRAINT fk_community_comments_post FOREIGN KEY (post_id) REFERENCES community_posts(id) ON DELETE CASCADE,
    CONSTRAINT fk_community_comments_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
