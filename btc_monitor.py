# -*- coding: utf-8 -*-
"""
BTC 趋势盯盘提醒工具（托尼老师规则版）
======================================================================
只做提醒，不自动下单。满足趋势策略时，通过 Server酱 推送到微信。

策略来自《趋势交易心得》(托尼老师), 做多; 做空为对称相反:
  第一条(方向, 凌驾一切):
      · 日线大方向过滤: 收盘在 EMA200 之上=大多头(只做多); 之下=大空头(只做空)
      · 4小时趋势: 10MA 上穿 30MA(金叉)=上涨趋势; 下穿(死叉)=下降趋势
  开仓:
      · 上涨趋势 + 收盘站上10MA + "放量大阳" => 开多信号
      · 下降趋势 + 收盘跌破10MA + "放量大阴" => 开空信号
  盈亏比过滤(结构法):
      · 止损=信号K线低点(前低), 目标=上方最近阻力(摆动高点)
      · 盈亏比 >= 门槛(默认 2:1) 才提醒, 消息里显示真实盈亏比和等级
  离场提醒:
      · 均线反转(10/30 金叉<->死叉) 或 跌破前低/突破前高 => 提醒考虑离场

数据来源: 交易所公开行情接口(OKX 优先, 币安备用), 免费、无需账户。

======================================================================
                    ↓↓↓  可 修 改 参 数 区  ↓↓↓
   不懂编程也没关系, 只改下面这些 = 号右边的数字/文字即可。
   改完保存, 上传回 GitHub 就会生效。不要动下面"核心代码区"。
======================================================================
"""

# —— 盯哪个币 ——
SYMBOL = "BTC"                 # 币种。想换以太坊改成 "ETH"
QUOTE = "USDT"                 # 计价货币, USDT 约等于美元

# —— 周期 ——
ENTRY_TF = "4H"                # 开单主周期(文档用4小时; 想用6小时写 "6H")
DAILY_FILTER = True            # 是否用日线大方向过滤。True=开; False=只看4小时不过滤
DAILY_TF = "1D"                # 日线周期
ONLY_LONG = True               # 只做多开关: True=只发做多信号(不做空); False=多空都做

# —— 均线设置 ——
MA_FAST = 10                   # 4小时的快线(10MA)
MA_SLOW = 30                   # 4小时的慢线(30MA)
DAILY_TREND_METHOD = "EMA200"  # 日线方向判断: "EMA200"(收盘在均线上/下) 或 "MA_CROSS"(日线也用10/30金叉死叉)
EMA_PERIOD = 200               # 当日线用 EMA200 时的均线周期

# —— 放量大阳/大阴 的灵敏度开关 ——
# 三选一: "标准" / "宽松" / "严格"。想要信号多就写"宽松", 想要少而精就写"严格"。
SENSITIVITY = "标准"
_PRESETS = {
    # 实体≥ATR的几倍 | 成交量>均量的几倍 | 收盘要落在K线(上/下)这个比例内
    "宽松": {"BODY_ATR_MULT": 0.5, "VOL_MULT": 1.2, "CLOSE_POS": 0.50},
    "标准": {"BODY_ATR_MULT": 0.8, "VOL_MULT": 1.5, "CLOSE_POS": 0.33},
    "严格": {"BODY_ATR_MULT": 1.0, "VOL_MULT": 2.0, "CLOSE_POS": 0.25},
}
VOL_MA_LEN = 20                # 计算均量用过去多少根K线

# —— 盈亏比(结构法) ——
# 注: 这套"放量大阳/阴"多为突破型(上方无现成阻力), 盈亏比几乎都算作无穷大、自动达标,
#     所以这个门槛基本不会拦下信号; 消息里已不再显示盈亏比。保留它仅作保险。
RR_MIN = 2.0                   # 盈亏比门槛, 低于它不提醒(对突破型信号基本不触发)
RR_GOOD = 3.0                  # (已不在消息中使用, 保留以兼容)
STOP_BUFFER_PCT = 0.0          # 止损在K线低/高点外再留的缓冲(百分比), 想留0.3%写 0.3
PIVOT_K = 3                    # 识别摆动高/低点时左右各看几根, 越大越稳、越少
PIVOT_LOOKBACK = 60            # 往回找阻力/支撑最多看多少根K线
REQUIRE_RESISTANCE = False     # True=上方必须有阻力才算; False=创新高(上方无阻力)也算盈亏比达标

# —— 离场提醒开关 ——
EXIT_ON_MA_CROSS = True        # 10/30均线反转(金叉<->死叉)时提醒离场
EXIT_ON_STRUCTURE_BREAK = False # 上涨中跌破前低 / 下跌中突破前高 时提醒离场(默认关, 让利润奔跑; 想要提前预警改True)

# —— 防重复刷屏 ——
# 只在"信号K线刚收盘不久"时提醒; 超过这么多分钟就当旧信号、不再补发。
# (GitHub 每15分钟跑一次, 35分钟能稳稳抓到刚收盘那根, 又不会反复刷同一根。)
FRESH_ALERT_MIN = 35

# —— 显示 ——
PRICE_DECIMALS = 1             # 价格显示保留几位小数

# SendKey 不要写在这里! 请放到 GitHub 的 Secrets 里(名字叫 SCT_SENDKEY)。

# ======================================================================
#                    ↑↑↑  可 修 改 参 数 区  ↑↑↑
#                   下面是核心代码区, 一般不用改
# ======================================================================

import os
import sys
import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone

# 按灵敏度套用阈值
_P = _PRESETS.get(SENSITIVITY, _PRESETS["标准"])
BODY_ATR_MULT = _P["BODY_ATR_MULT"]
VOL_MULT = _P["VOL_MULT"]
CLOSE_POS = _P["CLOSE_POS"]

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
HTTP_TIMEOUT = 20
INST = f"{SYMBOL}-{QUOTE}"
BINANCE_SYMBOL = f"{SYMBOL}{QUOTE}"


# ---------------------- 工具: HTTP ----------------------
def _http_get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "btc-monitor/2.0"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------- 取K线数据 ----------------------
# 统一输出: 从旧到新的列表, 每根 {"t":毫秒ts,"o","h","l","c","v"}; 只含已收盘K线。

def fetch_okx(timeframe, limit=300):
    bar = {"1D": "1D", "4H": "4H", "6H": "6H", "1H": "1H", "1W": "1W"}.get(timeframe, timeframe)
    url = f"https://www.okx.com/api/v5/market/candles?instId={INST}&bar={bar}&limit={limit}"
    data = _http_get_json(url)
    if str(data.get("code")) != "0":
        raise RuntimeError(f"OKX 返回错误: {data.get('msg')}")
    out = []
    for r in data.get("data", []):
        if len(r) > 8 and str(r[8]) != "1":
            continue  # 跳过未收盘K线
        out.append({"t": int(r[0]), "o": float(r[1]), "h": float(r[2]),
                    "l": float(r[3]), "c": float(r[4]), "v": float(r[5])})
    out.sort(key=lambda x: x["t"])
    return out


def fetch_binance(timeframe, limit=300):
    interval = {"1D": "1d", "4H": "4h", "6H": "6h", "1H": "1h", "1W": "1w"}.get(timeframe, timeframe.lower())
    url = (f"https://data-api.binance.vision/api/v3/klines?symbol={BINANCE_SYMBOL}"
           f"&interval={interval}&limit={limit}")
    rows = _http_get_json(url)
    out = [{"t": int(r[0]), "o": float(r[1]), "h": float(r[2]),
            "l": float(r[3]), "c": float(r[4]), "v": float(r[5])} for r in rows]
    out.sort(key=lambda x: x["t"])
    if out:
        out = out[:-1]  # 币安最后一根正在走, 剔除
    return out


def fetch_klines(timeframe, limit=300):
    need = max(EMA_PERIOD, MA_SLOW) + 5
    last_err = None
    for name, fn in [("币安数据站", fetch_binance), ("OKX", fetch_okx)]:
        try:
            data = fn(timeframe, limit)
            if data and len(data) >= need:
                print(f"  [数据] {timeframe} 用 {name} 成功, 共 {len(data)} 根已收盘K线")
                return data
            last_err = RuntimeError(f"{name} 数据不足({len(data) if data else 0}根, 需{need}根)")
        except Exception as e:
            last_err = e
            print(f"  [数据] {timeframe} 用 {name} 失败: {e}")
    raise RuntimeError(f"所有数据源都失败: {last_err}")


# ---------------------- 指标计算 ----------------------
def sma(values, period):
    out, s = [], 0.0
    for i, v in enumerate(values):
        s += v
        if i >= period:
            s -= values[i - period]
        out.append(s / period if i >= period - 1 else sum(values[:i + 1]) / (i + 1))
    return out


def ema(values, period):
    k = 2.0 / (period + 1)
    out, prev = [], values[0]
    for i, v in enumerate(values):
        prev = v if i == 0 else (v * k + prev * (1 - k))
        out.append(prev)
    return out


def atr(klines, period=14):
    trs = []
    for i in range(1, len(klines)):
        h, l, pc = klines[i]["h"], klines[i]["l"], klines[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if not trs:
        return 0.0
    return sum(trs[-period:]) / min(period, len(trs))


def find_pivot_highs(klines, k):
    highs = [x["h"] for x in klines]
    return [i for i in range(k, len(highs) - k)
            if all(highs[i] > w for w in highs[i - k:i] + highs[i + 1:i + 1 + k])]


def find_pivot_lows(klines, k):
    lows = [x["l"] for x in klines]
    return [i for i in range(k, len(lows) - k)
            if all(lows[i] < w for w in lows[i - k:i] + lows[i + 1:i + 1 + k])]


# ---------------------- 日线方向 ----------------------
def daily_direction(daily_klines):
    closes = [k["c"] for k in daily_klines]
    if DAILY_TREND_METHOD.upper() == "EMA200":
        e = ema(closes, EMA_PERIOD)[-1]
        ref = e
        d = "up" if closes[-1] > e else ("down" if closes[-1] < e else "flat")
        return d, ref
    f = sma(closes, MA_FAST)[-1]
    s = sma(closes, MA_SLOW)[-1]
    return ("up" if f > s else ("down" if f < s else "flat")), s


# ---------------------- 放量大阳/大阴 ----------------------
def is_volume_big_candle(klines, idx, direction):
    c = klines[idx]
    rng = c["h"] - c["l"]
    if rng <= 0:
        return False, {}
    body = abs(c["c"] - c["o"])
    a = atr(klines[:idx + 1], 14)
    if direction == "long" and not (c["c"] > c["o"]):
        return False, {}
    if direction == "short" and not (c["c"] < c["o"]):
        return False, {}
    if a > 0 and body < a * BODY_ATR_MULT:
        return False, {}
    if direction == "long" and c["c"] < c["h"] - CLOSE_POS * rng:
        return False, {}
    if direction == "short" and c["c"] > c["l"] + CLOSE_POS * rng:
        return False, {}
    start = max(0, idx - VOL_MA_LEN)
    prev_vols = [x["v"] for x in klines[start:idx]]
    vol_ma = sum(prev_vols) / len(prev_vols) if prev_vols else 0.0
    if vol_ma <= 0 or c["v"] < vol_ma * VOL_MULT:
        return False, {}
    return True, {"vol_ratio": c["v"] / vol_ma, "atr": a}


# ---------------------- 开仓信号 ----------------------
def evaluate_entry(kl, daily_dir):
    closes = [k["c"] for k in kl]
    fast = sma(closes, MA_FAST)
    slow = sma(closes, MA_SLOW)
    i = len(kl) - 1
    sig = kl[i]

    if fast[i] > slow[i]:
        direction = "long"
    elif fast[i] < slow[i]:
        direction = "short"
    else:
        return None

    if ONLY_LONG and direction == "short":
        return None

    # 日线大方向过滤
    if DAILY_FILTER and daily_dir in ("up", "down"):
        if direction == "long" and daily_dir != "up":
            return None
        if direction == "short" and daily_dir != "down":
            return None

    # 收盘要站上/跌破快线(10MA)
    if direction == "long" and not (sig["c"] > fast[i]):
        return None
    if direction == "short" and not (sig["c"] < fast[i]):
        return None

    ok, extra = is_volume_big_candle(kl, i, direction)
    if not ok:
        return None

    entry = sig["c"]
    sub = kl[max(0, i - PIVOT_LOOKBACK):i + 1]
    if direction == "long":
        stop = sig["l"] * (1 - STOP_BUFFER_PCT / 100.0)
        risk = entry - stop
        if risk <= 0:
            return None
        prices = [sub[j]["h"] for j in find_pivot_highs(sub, PIVOT_K) if sub[j]["h"] > entry]
        if prices:
            target = min(prices)
            rr = (target - entry) / risk
        elif REQUIRE_RESISTANCE:
            return None
        else:
            target, rr = None, float("inf")
    else:
        stop = sig["h"] * (1 + STOP_BUFFER_PCT / 100.0)
        risk = stop - entry
        if risk <= 0:
            return None
        prices = [sub[j]["l"] for j in find_pivot_lows(sub, PIVOT_K) if sub[j]["l"] < entry]
        if prices:
            target = max(prices)
            rr = (entry - target) / risk
        elif REQUIRE_RESISTANCE:
            return None
        else:
            target, rr = None, float("inf")

    if rr < RR_MIN:
        return None

    return {"type": "entry", "t": sig["t"], "direction": direction, "entry": entry,
            "stop": stop, "target": target, "rr": rr, "ma_fast": fast[i], "ma_slow": slow[i],
            "daily_dir": daily_dir, "vol_ratio": extra.get("vol_ratio", 0.0)}


# ---------------------- 离场提醒 ----------------------
def evaluate_exits(kl):
    closes = [k["c"] for k in kl]
    fast = sma(closes, MA_FAST)
    slow = sma(closes, MA_SLOW)
    i = len(kl) - 1
    sig = kl[i]
    out = []

    if EXIT_ON_MA_CROSS and i >= 1:
        prev = fast[i - 1] - slow[i - 1]
        now = fast[i] - slow[i]
        if prev >= 0 and now < 0:
            out.append({"type": "exit_long_cross", "t": sig["t"], "price": sig["c"],
                        "ma_fast": fast[i], "ma_slow": slow[i]})
        elif prev <= 0 and now > 0:
            out.append({"type": "exit_short_cross", "t": sig["t"], "price": sig["c"],
                        "ma_fast": fast[i], "ma_slow": slow[i]})

    if EXIT_ON_STRUCTURE_BREAK:
        tdir = "up" if fast[i] > slow[i] else ("down" if fast[i] < slow[i] else "flat")
        sub = kl[max(0, i - PIVOT_LOOKBACK):i + 1]
        if tdir == "up":
            lows = find_pivot_lows(sub, PIVOT_K)
            if lows and sig["c"] < sub[lows[-1]]["l"]:
                out.append({"type": "exit_long_break", "t": sig["t"], "price": sig["c"],
                            "level": sub[lows[-1]]["l"]})
        elif tdir == "down":
            highs = find_pivot_highs(sub, PIVOT_K)
            if highs and sig["c"] > sub[highs[-1]]["h"]:
                out.append({"type": "exit_short_break", "t": sig["t"], "price": sig["c"],
                            "level": sub[highs[-1]]["h"]})
    if ONLY_LONG:
        out = [e for e in out if e["type"].startswith("exit_long")]
    return out


# ---------------------- 消息 ----------------------
def fmt(p):
    if p is None:
        return "上方无明显阻力(创新高)"
    return f"{p:,.{PRICE_DECIMALS}f}"


def rr_text(rr, target):
    if target is None:
        return "上方无阻力, 盈亏比极佳 ⭐"
    tag = "⭐ 优秀" if rr >= RR_GOOD else "✅ 达标"
    return f"{rr:.2f} : 1  {tag}"


def _tstr(t):
    return datetime.fromtimestamp(t / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


_TF_MIN = {"1H": 60, "2H": 120, "4H": 240, "6H": 360, "12H": 720, "1D": 1440, "1W": 10080}


def is_fresh(candle_open_ms):
    """信号K线是否刚收盘不久(在 FRESH_ALERT_MIN 分钟内), 用来避免补发旧信号/重复刷屏。"""
    close_ms = candle_open_ms + _TF_MIN.get(ENTRY_TF, 240) * 60 * 1000
    age_min = (datetime.now(timezone.utc).timestamp() * 1000 - close_ms) / 60000.0
    return age_min <= FRESH_ALERT_MIN


def build_entry_message(tf, s):
    is_long = s["direction"] == "long"
    dir_cn = "做多" if is_long else "做空"
    title = f"{'🟢📈' if is_long else '🔴📉'} {SYMBOL}开{'多' if is_long else '空'} 现价{fmt(s['entry'])} | {tf}"
    dmethod = "EMA200" if DAILY_TREND_METHOD.upper() == "EMA200" else "10/30均线"
    daily_cn = {"up": "多头", "down": "空头", "flat": "不明", None: "未过滤"}.get(s["daily_dir"], "未过滤")
    if s["target"] is None:
        tgt = ("上方暂无明显阻力，按趋势持有" if is_long else "下方暂无明显支撑，按趋势持有")
    else:
        tgt = f"{fmt(s['target'])}（{'上方最近阻力' if is_long else '下方最近支撑'}）"
    desp = (
        f"**现价 {fmt(s['entry'])}，{tf} {MA_FAST}/{MA_SLOW} 均线{'金叉' if is_long else '死叉'} + 放量{'大阳' if is_long else '大阴'}，符合开{'多' if is_long else '空'}条件** {'📈' if is_long else '📉'}\n\n"
        f"**币种**: {INST}\n\n"
        f"**周期**: {tf}(主) + 日线{dmethod}方向过滤\n\n"
        f"**方向**: {dir_cn} {'📈' if is_long else '📉'}\n\n"
        f"**信号**: 放量{'大阳' if is_long else '大阴'} + {tf} {MA_FAST}/{MA_SLOW}均线{'金叉(上涨趋势)' if is_long else '死叉(下降趋势)'}\n\n"
        f"**日线大方向**: {daily_cn}\n\n"
        f"**入场参考价**: {fmt(s['entry'])}\n\n"
        f"**止损**: {fmt(s['stop'])} (信号K线{'低' if is_long else '高'}点)\n\n"
        f"**参考目标**: {tgt}\n\n"
        f"**{MA_FAST}MA / {MA_SLOW}MA**: {fmt(s['ma_fast'])} / {fmt(s['ma_slow'])}\n\n"
        f"**成交量**: 约为近{VOL_MA_LEN}根均量的 {s['vol_ratio']:.2f} 倍\n\n"
        f"**信号K线时间(UTC)**: {_tstr(s['t'])}\n\n"
        f"---\n\n⚠️ 仅为策略提醒, 非投资建议。请复核后手动下单, 注意仓位与风险。"
    )
    return title, desp


def build_exit_message(tf, e):
    t = e["type"]
    if t == "exit_long_cross":
        title = f"⚪ {SYMBOL} 多单离场提醒(10/30死叉) | {tf}"
        reason = f"{MA_FAST}MA 下穿 {MA_SLOW}MA(死叉), 4小时趋势转为下降。"
    elif t == "exit_short_cross":
        title = f"⚪ {SYMBOL} 空单离场提醒(10/30金叉) | {tf}"
        reason = f"{MA_FAST}MA 上穿 {MA_SLOW}MA(金叉), 4小时趋势转为上涨。"
    elif t == "exit_long_break":
        title = f"⚠️ {SYMBOL} 多单离场提醒(跌破前低) | {tf}"
        reason = f"收盘跌破最近摆动低点 {fmt(e['level'])}(道氏理论上涨趋势破坏)。"
    else:
        title = f"⚠️ {SYMBOL} 空单离场提醒(突破前高) | {tf}"
        reason = f"收盘突破最近摆动高点 {fmt(e['level'])}(道氏理论下降趋势破坏)。"
    desp = (
        f"**币种**: {INST}\n\n**周期**: {tf}\n\n"
        f"**离场原因**: {reason}\n\n"
        f"**当前价**: {fmt(e['price'])}\n\n"
        f"**K线时间(UTC)**: {_tstr(e['t'])}\n\n"
        f"---\n\n⚠️ 趋势可能反转/结束。若持有相应仓位, 考虑按计划止盈或离场。仅为提醒, 非投资建议。"
    )
    return title, desp


def send_serverchan(title, desp):
    sendkey = os.environ.get("SCT_SENDKEY", "").strip()
    if not sendkey:
        print("  [推送] 未设置 SCT_SENDKEY, 仅控制台打印预览")
        print("  标题:", title)
        print("  " + desp.replace("\n", "\n  "))
        return False
    url = f"https://sctapi.ftqq.com/{sendkey}.send"
    payload = urllib.parse.urlencode({"title": title, "desp": desp}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"User-Agent": "btc-monitor/2.0"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            res = json.loads(resp.read().decode("utf-8"))
        if str(res.get("code")) == "0":
            print("  [推送] 微信推送成功 ✅")
            return True
        print(f"  [推送] Server酱返回: {res}")
    except Exception as e:
        print(f"  [推送] 推送失败: {e}")
    return False


# ---------------------- 去重状态 ----------------------
def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ---------------------- 主流程 ----------------------
def run_test_message():
    print("发送测试消息...")
    send_serverchan(f"✅ {SYMBOL} 盯盘助手 测试消息",
                    "如果你在微信收到这条, 说明 Server酱 配置成功! 🎉\n\n接下来它会在趋势信号出现时自动提醒你。")


def main():
    if os.environ.get("TEST_MSG") == "1" or (len(sys.argv) > 1 and sys.argv[1] == "test"):
        run_test_message()
        return

    print(f"=== BTC 盯盘运行 @ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} "
          f"| 灵敏度={SENSITIVITY} | 盈亏比门槛={RR_MIN}:1 ===")
    state = load_state()
    changed = False

    # 日线大方向
    daily_dir = None
    if DAILY_FILTER:
        try:
            daily = fetch_klines(DAILY_TF)
            daily_dir, ref = daily_direction(daily)
            print(f"  [日线] 大方向={daily_dir} (方法={DAILY_TREND_METHOD}, 参考值={ref:.{PRICE_DECIMALS}f})")
        except Exception as e:
            print(f"  [日线] 取数据失败, 本次不做日线过滤: {e}")

    # 主周期
    try:
        kl = fetch_klines(ENTRY_TF)
    except Exception as e:
        print(f"  主周期取数据失败, 结束: {e}")
        return

    # 开仓信号
    entry = evaluate_entry(kl, daily_dir)
    if entry and not is_fresh(entry["t"]):
        print(f"  有开{entry['direction']}信号但该K线已收盘过久(非最新), 跳过, 不补发旧信号。")
    elif entry:
        key = f"{INST}:{ENTRY_TF}:entry:{entry['direction']}"
        if state.get(key) == entry["t"]:
            print(f"  已对这根K线提醒过开{entry['direction']}, 不重复。")
        else:
            print(f"  ★ 开仓信号: {entry['direction']} 盈亏比={entry['rr']:.2f}")
            send_serverchan(*build_entry_message(ENTRY_TF, entry))
            state[key] = entry["t"]
            changed = True
    else:
        print("  无符合条件的开仓信号。")

    # 离场提醒
    for e in evaluate_exits(kl):
        if not is_fresh(e["t"]):
            continue
        key = f"{INST}:{ENTRY_TF}:{e['type']}"
        if state.get(key) == e["t"]:
            continue
        print(f"  ◆ 离场提醒: {e['type']}")
        send_serverchan(*build_exit_message(ENTRY_TF, e))
        state[key] = e["t"]
        changed = True

    if changed:
        save_state(state)
        print("状态已更新。")
    print("=== 本次运行结束 ===")


if __name__ == "__main__":
    main()
