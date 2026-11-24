-- 낚시 커뮤니티 댓글 신고. 서버가 처음 쓸 때 app/community/store.py 에서도 자동으로 만들고 칸을 추가합니다.
ALTER TABLE community_comments ADD COLUMN report_count INT NOT NULL DEFAULT 0 AFTER author_hearted;
ALTER TABLE community_comments ADD COLUMN hidden TINYINT(1) NOT NULL DEFAULT 0 AFTER report_count;
CREATE TABLE IF NOT EXISTS community_comment_reports (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    comment_id INT NOT NULL,
    user_id INT NOT NULL,
    reason VARCHAR(200) NOT NULL,
    created_at DATETIME NOT NULL,
    UNIQUE KEY uq_community_comment_reports (comment_id, user_id),
    CONSTRAINT fk_community_comment_reports_comment FOREIGN KEY (comment_id) REFERENCES community_comments(id) ON DELETE CASCADE,
    CONSTRAINT fk_community_comment_reports_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
