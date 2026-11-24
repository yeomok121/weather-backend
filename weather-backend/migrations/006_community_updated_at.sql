-- 낚시 커뮤니티: 글 수정 시각 (수정됨 표시). 서버가 시작 후 처음 쓸 때 칸이 없으면 자동으로 추가합니다. (app/community/store.py)
ALTER TABLE community_posts ADD COLUMN updated_at DATETIME NULL AFTER created_at;
