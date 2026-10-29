"""日报长图渲染：数据 → HTML 模板 → Chromium 截图 PNG（1080px 宽）。"""
import datetime
import html as H

import config

TAG_COLORS = {
    "行情": "#378ADD", "技术": "#1D9E75", "公告": "#534AB7",
    "求助": "#EF9F27", "闲聊": "#D4537E", "其他": "#888780",
}

CSS = """
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:'Microsoft YaHei','PingFang SC','Noto Sans CJK SC',sans-serif;
       background:#eef1f5; width:1080px; }
.page { padding:40px 48px; }
.head { display:flex; justify-content:space-between; align-items:flex-end; margin-bottom:28px; }
.head h1 { font-size:40px; color:#1a2b4c; }
.head h1 .sub { color:#378ADD; }
.head .date { font-size:22px; color:#8a94a6; }
.stats { display:flex; gap:24px; margin-bottom:28px; }
.stat { flex:1; background:#fff; border-radius:16px; padding:28px 0; text-align:center;
        border:1px solid #e3e8f0; }
.stat .num { font-size:52px; font-weight:600; color:#378ADD; }
.stat .lbl { font-size:22px; color:#8a94a6; margin-top:6px; }
.card { background:#fff; border-radius:16px; border:1px solid #e3e8f0;
        padding:32px 36px; margin-bottom:28px; }
.card h2 { font-size:28px; color:#1a2b4c; margin-bottom:24px; }
.card h2 .no { color:#378ADD; margin-right:10px; }
/* 热度条形图 */
.bar-row { display:flex; align-items:center; margin-bottom:16px; }
.bar-row .bl { width:220px; font-size:22px; color:#3c4658; text-align:right;
               padding-right:16px; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }
.bar-track { flex:1; background:#f0f3f8; border-radius:8px; height:34px; }
.bar-fill { height:34px; border-radius:8px; background:linear-gradient(90deg,#5ea3e8,#378ADD);
            display:flex; align-items:center; justify-content:flex-end; padding-right:12px;
            color:#fff; font-size:19px; min-width:56px; }
/* 话题块 */
.topic { border-left:8px solid #ccc; padding:4px 0 4px 24px; margin-bottom:26px; }
.topic .t-head { display:flex; align-items:center; gap:14px; margin-bottom:12px; }
.topic .t-name { font-size:27px; font-weight:600; color:#1a2b4c; }
.topic .t-tag { font-size:18px; color:#fff; border-radius:14px; padding:3px 14px; }
.topic ul { list-style:none; }
.topic li { font-size:23px; color:#3c4658; line-height:1.65; margin-bottom:6px; }
.topic li .who { color:#378ADD; font-weight:600; }
/* 活跃度表 */
table { width:100%; border-collapse:collapse; }
td { font-size:23px; color:#3c4658; padding:12px 8px; border-bottom:1px solid #f0f3f8; }
td.rank { color:#8a94a6; width:70px; }
td.name { overflow:hidden; white-space:nowrap; text-overflow:ellipsis; max-width:300px; }
td.cnt { text-align:right; width:140px; color:#1a2b4c; font-weight:600; }
td.delta { text-align:right; width:140px; }
.up { color:#e24b4a; } .down { color:#1D9E75; } .flat { color:#b4b2a9; }
/* 时段直方图 */
.hours { display:flex; align-items:flex-end; gap:6px; height:150px; margin-top:8px; }
.hcol { flex:1; background:#a9c9ee; border-radius:4px 4px 0 0; min-height:4px; }
.hcol.peak { background:#378ADD; }
.hlabels { display:flex; gap:6px; margin-top:8px; }
.hlabels span { flex:1; text-align:center; font-size:16px; color:#8a94a6; }
.note { font-size:17px; color:#b0b8c4; margin-top:10px; }
/* 页脚 */
.foot { text-align:center; padding:6px 0 20px; font-size:19px; color:#9aa4b2; }
.foot b { color:#378ADD; font-weight:600; }
"""

TAG_EN = {"行情": "Market", "技术": "Tech", "公告": "Notice", "求助": "Help", "闲聊": "Chat", "其他": "Other"}


def _esc(s) -> str:
    return H.escape(str(s or ""))


def _topic_color(tag: str, idx: int) -> str:
    return TAG_COLORS.get(tag, list(TAG_COLORS.values())[idx % len(TAG_COLORS)])


def build_html(d: dict) -> str:
    lang = d.get("lang", "zh")
    zh = lang.startswith("zh")
    L = {
        "t1": "话题热度 Top5" if zh else "Top 5 Hot Topics",
        "t2": "重点话题" if zh else "Key Topics",
        "t3": "成员活跃度" if zh else "Most Active Members",
        "t4": "活跃时段分布" if zh else "Hourly Activity",
        "msgs": "消息数" if zh else "Messages",
        "topics": "话题数" if zh else "Topics",
        "members": "参与人数" if zh else "Participants",
        "hot": "精华话题" if zh else "Digest Items",
        "rank": "排名" if zh else "#",
        "name": "成员" if zh else "Member",
        "cnt": "消息" if zh else "Msgs",
        "note": "时间均为北京时间" if zh else "Times shown in UTC+8",
        "del": "原始消息已删除，仅保留本图" if zh else "Raw messages deleted after digest",
        "by": "由" if zh else "by",
        "want": "想让你的群也有？点我头像" if zh else "Want this for your group? Tap my avatar",
        "ai": "内容由 AI 生成，仅供参考" if zh else "AI-generated, for reference only",
    }
    s = d["stats"]
    max_cnt = max([t["count"] for t in d["top_topics"]] or [1]) or 1
    max_h = max(d["hours"] or [1]) or 1

    bars = "".join(
        f'<div class="bar-row"><div class="bl">{_esc(t["name"])}</div>'
        f'<div class="bar-track"><div class="bar-fill" style="width:{max(8, t["count"] / max_cnt * 100):.0f}%">'
        f'{t["count"]}</div></div></div>'
        for t in d["top_topics"]
    )
    topics_html = ""
    for i, t in enumerate(d["topics"]):
        color = _topic_color(t.get("tag", ""), i)
        tag_txt = t.get("tag", "其他") if zh else TAG_EN.get(t.get("tag", ""), "Other")
        pts = ""
        for p in t["points"]:
            if p.get("who"):
                pts += f'<li>· <span class="who">{_esc(p["who"])}</span>：{_esc(p["text"])}</li>'
            else:
                pts += f"<li>· {_esc(p['text'])}</li>"
        topics_html += (
            f'<div class="topic" style="border-left-color:{color}">'
            f'<div class="t-head"><span class="t-name">{i + 1}. {_esc(t["name"])}</span>'
            f'<span class="t-tag" style="background:{color}">{_esc(tag_txt)}</span></div>'
            f"<ul>{pts}</ul></div>"
        )

    def _delta(v):
        if v > 0:
            return f'<span class="up">↑{v}</span>'
        if v < 0:
            return f'<span class="down">↓{-v}</span>'
        return '<span class="flat">—</span>'

    rows = "".join(
        f'<tr><td class="rank">{i + 1}</td><td class="name">{_esc(m["name"])}</td>'
        f'<td class="cnt">{m["count"]}</td><td class="delta">{_delta(m["delta"])}</td></tr>'
        for i, m in enumerate(d["members"])
    )
    max_h = max(d["hours"] or [1]) or 1
    cols = "".join(
        f'<div class="hcol{" peak" if h == max_h and h > 0 else ""}" style="height:{max(3, h / max_h * 140):.0f}px"></div>'
        for h in d["hours"]
    )
    labels = "".join(f"<span>{h:02d}</span>" if h % 3 == 0 else "<span></span>" for h in range(24))
    brand = config.brand(lang)

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{CSS}</style></head>
<body><div class="page">
  <div class="head">
    <h1>{_esc(brand)} <span class="sub">|</span> {_esc(d["group"])}</h1>
    <div class="date">{_esc(d["date_range"])}</div>
  </div>
  <div class="stats">
    <div class="stat"><div class="num">{s["msgs"]}</div><div class="lbl">{L["msgs"]}</div></div>
    <div class="stat"><div class="num">{s["topics_n"]}</div><div class="lbl">{L["topics"]}</div></div>
    <div class="stat"><div class="num">{s["members"]}</div><div class="lbl">{L["members"]}</div></div>
    <div class="stat"><div class="num">{s["digest_n"]}</div><div class="lbl">{L["hot"]}</div></div>
  </div>
  <div class="card"><h2><span class="no">1.</span>{L["t1"]}</h2>{bars}</div>
  <div class="card"><h2><span class="no">2.</span>{L["t2"]}</h2>{topics_html}</div>
  <div class="card"><h2><span class="no">3.</span>{L["t3"]}</h2>
    <table><tr><td class="rank">{L["rank"]}</td><td class="name">{L["name"]}</td>
    <td class="cnt">{L["cnt"]}</td><td class="delta"></td></tr>{rows}</table></div>
  <div class="card"><h2><span class="no">4.</span>{L["t4"]}</h2>
    <div class="hours">{cols}</div><div class="hlabels">{labels}</div>
    <div class="note">{L["note"]}</div></div>
  <div class="foot">{L["del"]} · {L["ai"]}<br>
    {L["by"]} <b>@{_esc(d["bot_username"])}</b> · {L["want"]}</div>
</div></body></html>"""


def render_png(data: dict) -> bytes:
    """同步阻塞，调用方需放线程池。首次运行需 playwright install chromium。"""
    from playwright.sync_api import sync_playwright

    html = build_html(data)
    with sync_playwright() as p:
        browser = p.chromium.launch(args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",  # 1G 小机保命：不用 /dev/shm，防内存爆
            "--disable-gpu",
            "--disable-extensions",
        ])
        # device_scale_factor=1：2 倍缩放的位图内存直接翻倍，1G 小机撑不住（三次实测僵死）
        # 1080px 宽在手机端已足够清晰
        page = browser.new_page(viewport={"width": 1080, "height": 900}, device_scale_factor=1)
        page.set_content(html, wait_until="networkidle")
        buf = page.screenshot(full_page=True)
        browser.close()
        return buf
