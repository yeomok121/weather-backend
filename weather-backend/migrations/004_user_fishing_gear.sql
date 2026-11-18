-- 낚시 페이지: 계정별 '내 준비물' 세팅 (서버가 처음 쓸 때 app/fishing/gear.py 에서도 자동으로 만듭니다)
CREATE TABLE IF NOT EXISTS user_fishing_gear (
    user_id INT NOT NULL PRIMARY KEY,
    setup_json TEXT NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_user_fishing_gear_user FOREIGN KEY (user_id)
        REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
