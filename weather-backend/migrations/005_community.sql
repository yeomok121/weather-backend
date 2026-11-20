-- 낚시 커뮤니티(자랑·팁·공지): 글, 사진(서버에서 다시 저장한 JPEG), 공감, 신고
-- 서버가 처음 쓸 때 app/community/store.py 에서도 자동으로 만듭니다.

CREATE TABLE IF NOT EXISTS community_posts (
        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        category VARCHAR(8) NOT NULL,
        title VARCHAR(60) NOT NULL,
        body TEXT NOT NULL,
        point_name VARCHAR(80) NULL,
        species VARCHAR(30) NULL,
        length_cm DECIMAL(5,1) NULL,
        like_count INT NOT NULL DEFAULT 0,
        report_count INT NOT NULL DEFAULT 0,
        hidden TINYINT(1) NOT NULL DEFAULT 0,
        created_at DATETIME NOT NULL,
        KEY idx_community_posts_list (hidden, category, id),
        KEY idx_community_posts_user (user_id, created_at),
        CONSTRAINT fk_community_posts_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS community_photos (
        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        post_id INT NOT NULL,
        sort_order TINYINT UNSIGNED NOT NULL,
        mime VARCHAR(20) NOT NULL,
        width SMALLINT UNSIGNED NOT NULL,
        height SMALLINT UNSIGNED NOT NULL,
        data MEDIUMBLOB NOT NULL,
        KEY idx_community_photos_post (post_id, sort_order),
        CONSTRAINT fk_community_photos_post FOREIGN KEY (post_id) REFERENCES community_posts(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS community_likes (
        post_id INT NOT NULL,
        user_id INT NOT NULL,
        PRIMARY KEY (post_id, user_id),
        CONSTRAINT fk_community_likes_post FOREIGN KEY (post_id) REFERENCES community_posts(id) ON DELETE CASCADE,
        CONSTRAINT fk_community_likes_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS community_reports (
        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        post_id INT NOT NULL,
        user_id INT NOT NULL,
        reason VARCHAR(200) NOT NULL,
        created_at DATETIME NOT NULL,
        UNIQUE KEY uq_community_reports (post_id, user_id),
        CONSTRAINT fk_community_reports_post FOREIGN KEY (post_id) REFERENCES community_posts(id) ON DELETE CASCADE,
        CONSTRAINT fk_community_reports_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
