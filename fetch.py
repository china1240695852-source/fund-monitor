# -*- coding: utf-8 -*-
"""
云端基金/股票监控数据抓取（GitHub Actions 定时运行）
读取 data/*.json（每个文件=一个用户的关注列表），合并抓取，生成 docs/data.json。
GitHub Pages 静态托管，手机随时可访问。
"""
import glob
import json
import os
import re
import urllib.request
import urllib.parse
from datetime import datetime, timedelta

# ============ 在这里修改默认关注列表（data/ 目录里每个用户的列表会覆盖） ============
DEFAULT_FUNDS = [
    {"code": "001480", "name": "财通成长优选混合A"},
    {"code": "014915", "name": "财通匠心优选一年持有混合A"},
]
DEFAULT_STOCKS = [
    {"code": "sz000001", "name": "平安银行"},
    {"code": "sh600519", "name": "贵州茅台"},
    {"code": "sz300750", "name": "宁德时代"},
]
# =============================================================================

NEWS_KEYWORDS = [
    "基金", "股市", "A股", "央行", "降息", "加息", "财报", "业绩",
    "IPO", "新能源", "芯片", "AI", "人工智能", "黄金", "美股", "港股",
    "汇率", "GDP", "通胀", "回购", "分红", "证监会", "美联储", "涨停", "跌停",
]


def load_user_lists():
    """读取 data/*.json，返回 [{username, funds, stocks}]；仓库主目录的 data.json 除外"""
    users = []
    for path in sorted(glob.glob("data/*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                u = json.load(f)
            if not isinstance(u, dict) or not u.get("username"):
                continue
            users.append({
                "username": u["username"],
                "funds": u.get("funds") or [],
                "stocks": u.get("stocks") or [],
            })
        except Exception:
            continue
    if not users:
        users = [{"username": "admin", "funds": DEFAULT_FUNDS, "stocks": DEFAULT_STOCKS}]
    return users


def http_get(url, headers=None, timeout=12, encoding="utf-8"):
    hdrs = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://fund.eastmoney.com/",
    }
    if headers:
        hdrs.update(headers)
    try:
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode(encoding, "replace")
    except Exception:
        return None


def fetch_valuations(codes):
    """基金实时估值：天天基金"""
    if not codes:
        return {}
    url = ("https://fundcomapi.tiantianfunds.com/mm/newCore/FundValuationLast?"
           + urllib.parse.urlencode({
               "FCODES": ",".join(codes),
               "FIELDS": "FCODE,SHORTNAME,GSZZL,GSZ,GSZTIME,FSRQ,DWJZ",
           }))
    text = http_get(url, headers={"Referer": "https://h5.1234567.com.cn/"})
    if not text:
        return {}
    try:
        data = json.loads(text)
        return {it.get("FCODE"): it for it in (data.get("data") or []) if it.get("FCODE")}
    except Exception:
        return {}


def fetch_fund_nav(code):
    """基金最新净值：东方财富"""
    url = f"https://api.fund.eastmoney.com/f10/lsjz?callback=&fundCode={code}&pageIndex=1&pageSize=1"
    text = http_get(url)
    if not text:
        return None
    try:
        data = json.loads(text)
        items = data.get("Data", {}).get("LSJZList", []) or []
        return items[0] if items else None
    except Exception:
        return None


def fetch_fund_history(code, max_rows=120):
    """基金历史净值（预测用）"""
    all_items = []
    page = 1
    while len(all_items) < max_rows and page <= 10:
        url = (f"https://api.fund.eastmoney.com/f10/lsjz?callback=&fundCode={code}"
               f"&pageIndex={page}&pageSize=20")
        text = http_get(url)
        if not text:
            break
        try:
            items = json.loads(text).get("Data", {}).get("LSJZList", []) or []
        except Exception:
            break
        if not items:
            break
        all_items.extend(items)
        page += 1
    rows = []
    for it in all_items:
        try:
            rows.append({
                "date": it.get("FSRQ", ""),
                "nav": float(it.get("DWJZ") or 0),
                "pct": float(it.get("JZZZL") or 0),
            })
        except Exception:
            continue
    rows.reverse()
    return rows[:max_rows]


def predict_next_nav(rows, window=30):
    if len(rows) < 5:
        return None
    recent = rows[-window:]
    last = recent[-1]
    last_nav = last["nav"]
    pcts = [r["pct"] for r in recent if r.get("pct") is not None]
    if len(pcts) < 5 or last_nav <= 0:
        return None
    mu = sum(pcts) / len(pcts)
    sigma = (sum((v - mu) ** 2 for v in pcts) / len(pcts)) ** 0.5
    xs = list(range(len(recent)))
    ys = [r["nav"] for r in recent]
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sxy / sxx if sxx else 0
    trend_pct = slope / last_nav * 100 if last_nav else 0
    pred_pct = mu * 0.6 + trend_pct * 0.4
    pred_nav = last_nav * (1 + pred_pct / 100)
    low = last_nav * (1 + (pred_pct - sigma) / 100)
    high = last_nav * (1 + (pred_pct + sigma) / 100)
    if trend_pct > 0.05:
        trend = "短期趋势上行"
    elif trend_pct < -0.05:
        trend = "短期趋势下行"
    else:
        trend = "短期震荡"
    return {
        "last_nav": round(last_nav, 4),
        "predict_nav": round(pred_nav, 4),
        "predict_pct": round(pred_pct, 2),
        "interval_low": round(low, 4),
        "interval_high": round(high, 4),
        "trend_desc": trend,
        "trend_pct": round(trend_pct, 2),
        "window": len(pcts),
    }


def fetch_stock_quotes(codes):
    """股票实时行情：腾讯"""
    if not codes:
        return {}
    url = "https://qt.gtimg.cn/q=" + ",".join(codes)
    text = http_get(url, encoding="gbk")
    if not text:
        return {}
    out = {}
    for line in text.splitlines():
        m = re.match(r'v_(\w+)="([^"]*)"', line.strip())
        if not m:
            continue
        f = m.group(2).split("~")
        if len(f) < 35:
            continue
        try:
            out[m.group(1)] = {
                "code": m.group(1),
                "name": f[1],
                "price": float(f[3] or 0),
                "prev_close": float(f[4] or 0),
                "open": float(f[5] or 0),
                "change": float(f[31] or 0),
                "pct": float(f[32] or 0),
                "high": float(f[33] or 0),
                "low": float(f[34] or 0),
                "volume": f[6],
                "time": f[30],
            }
        except (ValueError, IndexError):
            continue
    return out


def fetch_stock_kline(code, days=120):
    """股票日K线：腾讯 qfq"""
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,{days},qfq"
    text = http_get(url)
    if not text:
        return []
    try:
        node = json.loads(text).get("data", {}).get(code, {})
        kline = node.get("qfqday") or node.get("day") or []
        rows = []
        for k in kline:
            if len(k) < 6:
                continue
            try:
                rows.append({
                    "date": k[0],
                    "open": float(k[1]),
                    "close": float(k[2]),
                    "high": float(k[3]),
                    "low": float(k[4]),
                    "volume": float(k[5] or 0),
                })
            except (ValueError, IndexError):
                continue
        return rows
    except Exception:
        return []


def fetch_market():
    """大盘指数：新浪"""
    url = "https://hq.sinajs.cn/list=s_sh000001,s_sz399001,s_sz399006,s_sh000300"
    text = http_get(url, headers={"Referer": "https://finance.sina.com.cn"}, encoding="gbk")
    out = []
    if text:
        for m in re.finditer(r's_(\w+)="([^"]*)"', text):
            f = m.group(2).split(",")
            if len(f) < 4:
                continue
            try:
                out.append({
                    "name": f[0].strip(), "price": float(f[1]),
                    "change": float(f[2]), "pct": float(f[3]),
                })
            except (ValueError, IndexError):
                continue
    return out


def fetch_news(limit=50):
    """财经要闻：新浪财经"""
    url = "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2516&k=&num=50&page=1"
    text = http_get(url, headers={"Referer": "https://finance.sina.com.cn/"})
    if not text:
        return []
    try:
        items = json.loads(text).get("result", {}).get("data", []) or []
        out = []
        for x in items:
            t = (x.get("title") or "").strip()
            if not t:
                continue
            try:
                ts = int(x.get("ctime") or 0)
                tm = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if ts else ""
            except Exception:
                tm = ""
            matched = [k for k in NEWS_KEYWORDS if k.lower() in t.lower()]
            out.append({
                "time": tm, "title": t[:120],
                "url": x.get("url") or "https://finance.sina.com.cn/",
                "source": x.get("media_name") or "",
                "matched": matched, "hit_count": len(matched),
            })
            if len(out) >= limit:
                break
        return out
    except Exception:
        return []


def main():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    users = load_user_lists()

    # 合并所有用户的基金/股票（去重），一次抓取
    all_funds = {}
    for u in users:
        for f in u["funds"]:
            all_funds.setdefault(f["code"], {"code": f["code"], "name": f.get("name", f["code"])})
    all_stocks = {}
    for u in users:
        for s in u["stocks"]:
            all_stocks.setdefault(s["code"], {"code": s["code"], "name": s.get("name", s["code"])})

    fund_codes = list(all_funds.keys())
    stock_codes = list(all_stocks.keys())
    vals = fetch_valuations(fund_codes)
    quotes = fetch_stock_quotes(stock_codes)

    # 抓取每只基金详情（历史+预测）
    fund_detail = {}
    for code, meta in all_funds.items():
        v = vals.get(code)
        item = {"code": code, "name": meta["name"]}
        if v and (v.get("GSZZL") is not None):
            item.update({"nav": v.get("GSZ") or v.get("DWJZ"), "pct": v.get("GSZZL"),
                         "time": v.get("GSZTIME"), "type": "estimate"})
        elif v and v.get("DWJZ"):
            item.update({"nav": v.get("DWJZ"), "pct": v.get("GSZZL"),
                         "time": v.get("FSRQ"), "type": "nav"})
        else:
            nv = fetch_fund_nav(code)
            if nv and nv.get("DWJZ"):
                item.update({"nav": nv.get("DWJZ"), "pct": nv.get("JZZZL"),
                             "time": nv.get("FSRQ"), "type": "nav"})
            else:
                item.update({"nav": "", "pct": "", "time": "", "type": "unknown"})
        hist = fetch_fund_history(code)
        item["history"] = hist
        item["predict"] = predict_next_nav(hist)
        fund_detail[code] = item

    # 抓取每只股票详情（K线）
    stock_detail = {}
    for code, meta in all_stocks.items():
        q = quotes.get(code)
        row = {"code": code, "name": meta["name"]}
        if q:
            q["name"] = q.get("name") or meta["name"]
            row.update(q)
        else:
            row.update({"price": "", "pct": "", "error": True})
        row["kline"] = fetch_stock_kline(code)
        stock_detail[code] = row

    # 按用户组织数据（每人只含自己的列表）
    data_users = []
    for u in users:
        data_users.append({
            "username": u["username"],
            "funds": [fund_detail.get(f["code"]) for f in u["funds"] if f["code"] in fund_detail],
            "stocks": [stock_detail.get(s["code"]) for s in u["stocks"] if s["code"] in stock_detail],
        })

    data = {
        "updated": now,
        "users": data_users,
        "market": fetch_market(),
        "news": fetch_news(),
    }

    with open("docs/data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print("OK updated at", now, "users:", len(data_users),
          "funds:", len(fund_codes), "stocks:", len(stock_codes),
          "news:", len(data["news"]))


if __name__ == "__main__":
    main()
