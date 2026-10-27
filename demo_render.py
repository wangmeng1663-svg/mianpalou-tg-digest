"""假数据渲染示例日报，验证模板效果。不需要 token 和 LLM。"""
import random

import render


def main():
    random.seed(42)
    hours = [max(0, int(random.gauss(8, 3) * 10)) for _ in range(24)]
    hours[22] = 120
    hours[21] = 96
    hours[14] = 70
    data = {
        "lang": "zh",
        "group": "BTC 交流群",
        "date_range": "10.26 22:00 - 10.27 22:00",
        "stats": {"msgs": 105, "topics_n": 5, "members": 18, "digest_n": 5},
        "top_topics": [
            {"name": "BTC 行情走势", "count": 38},
            {"name": "Pi Cycle 指标", "count": 22},
            {"name": "周末 AMA 安排", "count": 15},
            {"name": "交易所出金", "count": 9},
            {"name": "火锅之争", "count": 6},
        ],
        "topics": [
            {"name": "BTC 行情走势", "tag": "行情", "points": [
                {"text": "昨晚跌破 65000 后群里讨论到深夜，主流判断短期继续盘整", "who": "张三"},
                {"text": "有群友表示在 64000 附近分批建仓，止损设在 62000", "who": "李四"},
                {"text": "也有人认为下周鲍威尔讲话前不宜动手", "who": "老王"},
            ]},
            {"name": "Pi Cycle 指标", "tag": "求助", "points": [
                {"text": "多名新人询问在哪里看 Pi Cycle 指标", "who": "小新"},
                {"text": "有群友分享了查看教程链接", "who": "阿七"},
            ]},
            {"name": "周末 AMA 安排", "tag": "公告", "points": [
                {"text": "群主宣布周末组织 AMA，具体时间待定", "who": "群主"},
            ]},
            {"name": "交易所出金", "tag": "技术", "points": [
                {"text": "讨论了两个交易所的出金手续费对比", "who": "赵四"},
            ]},
            {"name": "火锅之争", "tag": "闲聊", "points": [
                {"text": "晚 8 点起全员争论火锅蘸料，麻酱派与油碟派战成平手", "who": ""},
            ]},
        ],
        "members": [
            {"name": "张三", "count": 21, "delta": 5},
            {"name": "李四", "count": 17, "delta": -3},
            {"name": "老王", "count": 14, "delta": 8},
            {"name": "阿七", "count": 12, "delta": 0},
            {"name": "小新", "count": 9, "delta": 9},
            {"name": "赵四", "count": 7, "delta": -2},
            {"name": "群主", "count": 6, "delta": 1},
        ],
        "hours": hours,
        "bot_username": "MianPalouBot",
    }
    png = render.render_png(data)
    out = "示例日报.png"
    with open(out, "wb") as f:
        f.write(png)
    print("OK ->", out, len(png), "bytes")


if __name__ == "__main__":
    main()
