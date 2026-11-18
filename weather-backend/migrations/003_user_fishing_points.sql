-- 낚시 페이지: 계정별 즐겨찾는 낚시 포인트 (서버가 처음 쓸 때 app/fishing/favorites.py 에서도 자동으로 만듭니다)
CREATE TABLE IF NOT EXISTS user_fishing_points (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    point_name VARCHAR(80) NOT NULL,
    sort_order TINYINT UNSIGNED NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_user_fishing_points_name (user_id, point_name),
    CONSTRAINT fk_user_fishing_points_user FOREIGN KEY (user_id)
        REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
