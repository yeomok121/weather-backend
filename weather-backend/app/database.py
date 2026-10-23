import os
import pymysql
from dotenv import load_dotenv

load_dotenv()

# 이 파일(app/database.py)의 상위 폴더 = weather-backend 폴더 (ca.pem이 있는 곳)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def get_conn():
    kwargs = {}
    ssl_ca = os.getenv("DB_SSL_CA")  # 예: ca.pem (비워두면 SSL 없이 접속: 로컬 MySQL용)
    if ssl_ca:
        kwargs["ssl_ca"] = os.path.join(BASE_DIR, ssl_ca)

    return pymysql.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        charset="utf8mb4",
        connect_timeout=10,
        cursorclass=pymysql.cursors.DictCursor,
        **kwargs,
    )
