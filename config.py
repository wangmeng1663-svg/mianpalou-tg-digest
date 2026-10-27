import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "deepseek").strip().lower()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-2.0-flash")
BOT_USERNAME = os.getenv("BOT_USERNAME", "MianPalouBot")
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
PROXY_URL = os.getenv("PROXY_URL", "").strip()  # 本机跑需梯子时填，如 http://127.0.0.1:7890

TIMEZONE = os.getenv("TZ", "Asia/Shanghai")          # 展示用时区
DEFAULT_SEND_HOUR = int(os.getenv("DEFAULT_SEND_HOUR", "22"))  # 默认发送点（TIMEZONE 整点）
MIN_MESSAGES = int(os.getenv("MIN_MESSAGES", "10"))  # 冷场阈值
MAX_GROUPS = int(os.getenv("MAX_GROUPS", "50"))      # 群数上限（防滥用）
HISTOGRAM_DAYS = 7                                    # 活跃统计窗口

# 语言 → 预估时区：发送时间 = 当地 20:00（换算成 UTC）
# 英文群默认美东（TG 英文群美区占比高）；有需要随时加映射
LANG_TZ = {
    "zh": "Asia/Shanghai",
    "ja": "Asia/Tokyo",
    "ko": "Asia/Seoul",
    "ru": "Europe/Moscow",
    "en": "America/New_York",
}
SEND_LOCAL_HOUR = 20
DEFAULT_LANG = "zh"

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "bot.db"

# 品牌名按日报主体语言渲染
BRANDS = {"zh": "免爬楼"}
DEFAULT_BRAND = "CatchUp"


def brand(lang: str) -> str:
    return BRANDS.get((lang or "").lower(), DEFAULT_BRAND)
