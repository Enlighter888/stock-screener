import requests
from flask import Flask, render_template, jsonify, request
from concurrent.futures import ThreadPoolExecutor, as_completed
import time, re, threading, uuid, math, json

app = Flask(__name__)

MAX_WORKERS = 30
SINA_H = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}
KLINE_DAYS = 120
scan_progress = {}
scan_progress_lock = threading.Lock()

# ── 工具函数 ──

def is_st(name):
    s = name.upper()
    return "ST" in s or "*ST" in s or s.startswith("S")

def is_cyb_kcb(code):
    return code.startswith(("300", "688"))

def get_pref(code):
    return "sh" if code.startswith(("6", "9")) else "sz"

def nf(v, d=0):
    if v is None: return d
    try: return float(v)
    except: return d

# ── 获取全部A股 ──

def fetch_page(node, page):
    url = (f"http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"Market_Center.getHQNodeData?page={page}&num=100&sort=code&asc=1&node={node}&symbol=&_s_r_a=page")
    try:
        r = requests.get(url, headers=SINA_H, timeout=10)
        d = r.json()
        return d if isinstance(d, list) else []
    except:
        return []

def fetch_stock_list():
    seen = set()
    stocks = []
    def grab(node):
        local = []
        for p in range(1, 26):
            items = fetch_page(node, p)
            if not items: break
            for it in items:
                code = str(it.get("code", ""))
                if code in seen: continue
                seen.add(code)
                local.append({
                    "code": code,
                    "name": str(it.get("name", "")),
                    "price": nf(it.get("trade")),
                    "chg_pct": nf(it.get("changepercent")),
                    "amount": nf(it.get("amount")),
                    "total_mv": nf(it.get("mktcap")) * 10000,
                })
            if len(items) < 100: break
        return local
    with ThreadPoolExecutor(max_workers=2) as ex:
        f1, f2 = ex.submit(grab, "sh_a"), ex.submit(grab, "sz_a")
    return f1.result() + f2.result()

# ── 基础过滤 ──

def basic_filter(stocks, basic_rules):
    ok = []
    for s in stocks:
        if basic_rules.get("ex_st", True) and is_st(s["name"]):
            continue
        if basic_rules.get("ex_cyb_kcb", True) and is_cyb_kcb(s["code"]):
            continue
        if s["price"] < float(basic_rules.get("min_price", 0)):
            continue
        if s["amount"] < float(basic_rules.get("min_amount", 0)):
            continue
        mv = s["total_mv"]
        min_mv = float(basic_rules.get("min_mv", 0))
        max_mv = float(basic_rules.get("max_mv", 9e18))
        if mv < min_mv or mv > max_mv:
            continue
        ok.append(s)
    return ok

# ── K线 ──

def fetch_kline(code, days=KLINE_DAYS):
    pref = get_pref(code)
    url = (f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={pref}{code}&scale=240&ma=no&datalen={days}")
    try:
        r = requests.get(url, headers=SINA_H, timeout=8)
        d = r.json()
        if isinstance(d, list) and len(d) > 10:
            return [{"date": x["day"], "open": float(x["open"]), "high": float(x["high"]),
                     "low": float(x["low"]), "close": float(x["close"]), "volume": float(x["volume"])} for x in d]
        return []
    except:
        return []

# ── RSI ──

def calc_rsi(prices, period):
    if len(prices) < period + 1:
        return 50.0
    gains, losses = 0.0, 0.0
    for i in range(-period, 0):
        diff = prices[i] - prices[i-1]
        if diff > 0: gains += diff
        else: losses -= diff
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss < 0.0001:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)

# ── 计算全部指标 ──

def calc_indi(klines):
    n = len(klines)
    if n < 20:
        return None

    cl = [k["close"] for k in klines]
    hi = [k["high"] for k in klines]
    lo = [k["low"] for k in klines]
    op = [k["open"] for k in klines]
    vo = [k["volume"] for k in klines]

    # 原版指标
    zt = sum(1 for i in range(max(0, n-20), n) if i > 0 and (cl[i]-cl[i-1])/cl[i-1]*100 >= 9.85)
    h20 = max(hi[-20:])
    pull = (h20 - cl[-1]) / h20 * 100
    v20 = max(vo[-20:])
    vr = (sum(vo[-3:])/3) / v20 if v20 > 0 else 1

    # 均线系
    ma5 = sum(cl[-5:]) / 5
    ma10 = sum(cl[-10:]) / 10 if n >= 10 else sum(cl) / n
    ma20 = sum(cl[-20:]) / 20
    ma60 = sum(cl[-60:]) / 60 if n >= 60 else ma20
    ma20p = sum(cl[-21:-1]) / 20 if n >= 21 else ma20
    ma5p = sum(cl[-6:-1]) / 5 if n >= 6 else ma5
    ma10p = sum(cl[-11:-1]) / 10 if n >= 11 else ma10
    ma20p_val = sum(cl[-21:-1]) / 20 if n >= 21 else ma20
    ma60p = sum(cl[-61:-1]) / 60 if n >= 61 else ma60

    # KD + J
    kv, dv = [], []
    for i in range(n):
        ll = min(lo[max(0, i-4):i+1]); hh = max(hi[max(0, i-4):i+1])
        rsv = (cl[i]-ll)/(hh-ll)*100 if (hh-ll) > 0.001 else 50
        k = (rsv + 2*kv[-1])/3 if kv else 50
        d = (k + 2*dv[-1])/3 if dv else 50
        kv.append(k); dv.append(d)
    j = 3 * kv[-1] - 2 * dv[-1]

    # MACD
    e12 = e26 = cl[0]
    for c in cl:
        e12 = e12*11/13 + c*2/13; e26 = e26*25/27 + c*2/27
    e12p = e26p = cl[0]
    for c in cl[:-1]:
        e12p = e12p*11/13 + c*2/13; e26p = e26p*25/27 + c*2/27
    macd_val = 2 * (e12 - e26)
    macd_prev = 2 * (e12p - e26p)

    # RSI
    rsi6 = calc_rsi(cl, 6)
    rsi12 = calc_rsi(cl, 12)
    rsi6p = calc_rsi(cl[:-1], 6) if len(cl) >= 8 else 50
    rsi12p = calc_rsi(cl[:-1], 12) if len(cl) >= 14 else 50

    # 布林(20,2)
    variance = sum((c - ma20)**2 for c in cl[-20:]) / 20
    bb_std = math.sqrt(variance)
    bb_upper = ma20 + 2 * bb_std
    bb_lower = ma20 - 2 * bb_std

    # 量
    ma5_vol = sum(vo[-5:]) / 5

    # VWAP
    vwap20 = sum(cl[i]*vo[i] for i in range(-20,0)) / sum(vo[-20:]) if sum(vo[-20:]) > 0 else ma20
    vwap60 = sum(cl[i]*vo[i] for i in range(-60,0)) / sum(vo[-60:]) if n >= 60 and sum(vo[-60:]) > 0 else vwap20

    # 筹码集中度 proxy: BB宽/价格
    bb_width_ratio = (bb_upper - bb_lower) / ma20 if ma20 > 0 else 1

    # 价格在20日区间位置%(0底部~100顶部)
    lo20 = min(lo[-20:]); hi20 = max(hi[-20:])
    price_pos = (cl[-1] - lo20) / (hi20 - lo20) * 100 if hi20 > lo20 else 50

    # 量价阶梯
    vol_d1 = vo[-3] / ma5_vol if len(vo) >= 3 else 0
    vol_d2 = vo[-2] / vo[-3] if len(vo) >= 3 else 1
    vol_d3 = vo[-1] / vo[-2] if len(vo) >= 2 else 1

    # 均线粘合度
    ma_max = max(ma5, ma10, ma20); ma_min = min(ma5, ma10, ma20)
    ma_spread = (ma_max - ma_min) / ma_min if ma_min > 0 else 0
    ma5_slope = (ma5 - ma5p) / ma5p * 100 if ma5p > 0 else 0

    # 3日涨幅
    gain_3d = (cl[-1] - cl[-4]) / cl[-4] * 100 if n >= 4 else 0

    # 价格变动
    cpct = (cl[-1] - cl[-2]) / cl[-2] * 100 if n >= 2 else 0
    amplitude = (hi[-1] - lo[-1]) / cl[-2] * 100 if n >= 2 else 0
    body = abs(cl[-1] - op[-1])
    total_range = hi[-1] - lo[-1]
    lower_shadow = min(op[-1], cl[-1]) - lo[-1]
    upper_shadow = hi[-1] - max(op[-1], cl[-1])

    # 连阳连板
    up_days = 0; down_days = 0
    for i in range(n-1, 0, -1):
        if cl[i] > op[i]: up_days += 1
        else: break
    for i in range(n-1, 0, -1):
        if cl[i] < op[i]: down_days += 1
        else: break
    consec_zt = 0
    for i in range(n-1, 0, -1):
        if (cl[i] - cl[i-1]) / cl[i-1] * 100 >= 9.85:
            consec_zt += 1
        else: break
    # 20天中下跌天数
    dn20 = sum(1 for i in range(max(0, n-20), n) if i > 0 and cl[i] < cl[i-1])

    # Boolean条件
    kd_gold = kv[-1] > dv[-1] and kv[-2] <= dv[-2] if len(kv) >= 2 else False
    macd_bull = macd_val > 0 and macd_val > macd_prev
    ma20_up = ma20 > ma20p
    ma_bull = ma5 > ma10 > ma20 > ma60 if n >= 60 else False
    one_line_3ma = (cl[-1] > ma5 and cl[-1] > ma10 and cl[-1] > ma20
                    and cl[-2] < ma20p_val and cpct >= 3)
    above_ma60 = cl[-1] > ma60 if n >= 60 else False
    ma5_gold_ma10 = ma5 > ma10 and ma5p <= ma10p
    rsi_gold = rsi6 > rsi12 and rsi6p <= rsi12p
    break_bb_upper = cl[-1] > bb_upper
    below_bb_lower = cl[-1] < bb_lower
    vol_breakout = cpct >= 3 and vo[-1] > 2 * ma5_vol
    doji = total_range > 0 and body / total_range < 0.1 and total_range / cl[-2] * 100 >= 2 if n >= 2 else False
    hammer = (lower_shadow > 2 * body and upper_shadow < body and lower_shadow > 0) if body > 0 else False
    yang_bao_yin = (cl[-1] > op[-2] and op[-1] < cl[-2]
                    and cl[-1] > cl[-2] and cl[-2] < op[-2]
                    and cpct > 0) if n >= 2 else False
    gap_up = lo[-1] > hi[-2] if n >= 2 else False
    new_high_20 = hi[-1] >= max(hi[-20:])

    # 筹码系
    chip_concentrated = bb_width_ratio < 0.15  # 90%集中度<15%
    chip_cost_above = cl[-1] < vwap60  # 主力成本≥现价
    chip_low_overhead = price_pos > 90  # 上方套牢<10%
    chip_stable = abs(vwap20 - vwap60) / cl[-1] < 0.03 if cl[-1] > 0 else False  # 20日筹码稳定

    # 量价精化
    vol_ladder = (len(vo) >= 3 and vol_d1 >= 0.8 and vol_d2 >= 0.9 and vol_d3 >= 1.0
                  and vo[-1] >= 1.2 * ma5_vol and vo[-1] <= 3 * ma5_vol)
    vol_moderate = vo[-1] <= 2.5 * ma5_vol  # 不放爆量(上限宽松版)
    price_creep = 3 <= gain_3d <= 8  # 3日涨幅3-8%

    # 均线粘合
    ma_convergence = ma_spread < 0.02  # 均线间距<2%
    ma_turning = ma5_slope > 0 and ma_bull  # 刚拐头+多头排列

    # 20日平均振幅
    avg_ampl20 = sum((hi[i]-lo[i])/cl[i-1]*100 for i in range(-min(20,n), 0) if i > -n+1) / min(20, n-1) if n >= 3 else amplitude

    # 活跌度
    active_volatility = avg_ampl20 > 1.5  # 20日平均振幅>1.5%

    # 剔除条件字段
    reject_bearish = cl[-1] < ma20 and cl[-1] < ma60 if n >= 60 else False
    reject_weaving = avg_ampl20 < 2  # 近20日平均振幅<2%
    reject_wick = upper_shadow > 0 and (upper_shadow / max(cl[-1], op[-1])) > 0.03 and upper_shadow > 2 * body
    hi_recent = max(hi[-30:]); hi_prior = max(hi[-60:-30]) if n >= 60 else hi_recent
    reject_lower_high = hi_recent < hi_prior * 0.95 if n >= 60 else False
    reject_more_down = dn20 > 12  # 20天中12+天下跌
    reject_inactive = avg_ampl20 <= 1.5  # 20日日均振幅≤1.5%: 活跌度不足

    return {
        "zt_count": zt, "pullback": round(pull,2), "vol_ratio": round(vr,3),
        "ma20": round(ma20,2), "ma20_up": ma20_up,
        "k": round(kv[-1],2), "d": round(dv[-1],2),
        "kd_gold": kd_gold, "macd_bull": macd_bull,
        "close_pct": round(cpct,2), "high_20": round(h20,2), "close": cl[-1],
        "ma5": round(ma5,2), "ma10": round(ma10,2),
        "ma60": round(ma60,2) if n >= 60 else 0,
        "ma_bull": ma_bull, "one_line_3ma": one_line_3ma,
        "above_ma60": above_ma60, "ma5_gold_ma10": ma5_gold_ma10,
        "rsi6": round(rsi6,1), "rsi_gold": rsi_gold,
        "j": round(j,1),
        "bb_upper": round(bb_upper,2), "bb_lower": round(bb_lower,2),
        "break_bb_upper": break_bb_upper, "below_bb_lower": below_bb_lower,
        "vol_breakout": vol_breakout,
        "doji": doji, "hammer": hammer, "yang_bao_yin": yang_bao_yin,
        "gap_up": gap_up, "new_high_20": new_high_20,
        "amplitude": round(amplitude,2), "avg_ampl20": round(avg_ampl20,2),
        "up_days": up_days, "consecutive_zt": consec_zt,
        "vwap20": round(vwap20,2), "vwap60": round(vwap60,2),
        "bb_width_ratio": round(bb_width_ratio,4),
        "price_pos": round(price_pos,1),
        "gain_3d": round(gain_3d,2), "ma_spread": round(ma_spread,4),
        "chip_concentrated": chip_concentrated,
        "chip_cost_above": chip_cost_above, "chip_low_overhead": chip_low_overhead,
        "chip_stable": chip_stable,
        "vol_ladder": vol_ladder, "vol_moderate": vol_moderate,
        "price_creep": price_creep, "ma_convergence": ma_convergence,
        "ma_turning": ma_turning, "active_volatility": active_volatility,
        "reject_bearish": reject_bearish, "reject_weaving": reject_weaving,
        "reject_wick": reject_wick, "reject_lower_high": reject_lower_high,
        "reject_more_down": reject_more_down, "reject_inactive": reject_inactive,
        "closes": json.dumps([round(c, 2) for c in cl[-20:]]),
    }

# ── 自定义公式引擎 ──

def eval_formula(formula, ind):
    if not formula or not formula.strip():
        return True
    env = {k: v for k, v in ind.items() if isinstance(v, (int, float, bool, str))}
    env["True"] = True; env["False"] = False
    allowed = {k: v for k, v in {**env}.items()}
    try:
        code = formula.strip()
        code = re.sub(r'\bAND\b', 'and', code, flags=re.IGNORECASE)
        code = re.sub(r'\bOR\b', 'or', code, flags=re.IGNORECASE)
        code = re.sub(r'\bNOT\b', 'not', code, flags=re.IGNORECASE)
        result = eval(code, {"__builtins__": {}}, allowed)
        return bool(result)
    except Exception as e:
        print(f"[FORMULA ERROR] {formula}: {e}")
        return True

# ── 主流程（带进度反馈）──

def run_scan(params, scan_id=None):
    t0 = time.time()
    basic_rules = params.get("basic", {})
    conditions = params.get("conditions", [])
    reject_conditions = params.get("reject_conditions", [])
    custom_formula = params.get("custom_formula", "")

    def report_progress(done, total, phase=""):
        if scan_id:
            with scan_progress_lock:
                p = scan_progress.get(scan_id)
                if p:
                    p["done"] = done
                    p["total"] = total
                    p["phase"] = phase

    report_progress(0, 0, "正在获取股票列表…")

    stocks = fetch_stock_list()
    report_progress(0, 0, f"共 {len(stocks)} 只, 正在基础过滤…")

    cand = basic_filter(stocks, basic_rules)
    if not cand:
        result = {"results": [], "basic_count": 0, "total": len(stocks), "time": round(time.time()-t0, 1)}
        if scan_id:
            with scan_progress_lock:
                scan_progress[scan_id] = {"done": 0, "total": 0, "phase": "完成", "status": "done", "result": result}
        return result

    report_progress(0, len(cand), f"基础通过 {len(cand)} 只, 正在扫描K线…")

    enabled_conds = [c for c in conditions if c.get("enabled", True)]
    enabled_rejects = [c for c in reject_conditions if c.get("enabled", True)]
    results = []
    lock = threading.Lock()
    prog = {"done": 0, "total": len(cand)}

    def proc(s):
        kl = fetch_kline(s["code"])
        ind = calc_indi(kl) if kl else None
        with lock:
            prog["done"] += 1
            if prog["done"] % 5 == 0 or prog["done"] == prog["total"]:
                report_progress(prog["done"], prog["total"])
        if ind is None:
            return None
        if custom_formula.strip():
            if not eval_formula(custom_formula, ind):
                return None
        else:
            for cond in enabled_conds:
                if not cond.get("enabled", True):
                    continue
                field = cond.get("field", "")
                op = cond.get("op", ">=")
                val = cond.get("value", 0)
                actual = ind.get(field)
                if actual is None:
                    continue
                try:
                    val_f = float(val)
                except:
                    continue
                if op == ">=" and not (actual >= val_f): return None
                if op == "<=" and not (actual <= val_f): return None
                if op == ">"  and not (actual > val_f):  return None
                if op == "<"  and not (actual < val_f):  return None
                if op == "==" and not (actual == val_f): return None
                if op == "!=" and not (actual != val_f): return None
                if op == "bool" and not actual: return None
        # 剔除条件检查 (任一触发则淘汰)
        for rcond in enabled_rejects:
            if not rcond.get("enabled", True):
                continue
            rfield = rcond.get("field", "")
            actual = ind.get(rfield)
            if actual is True:
                return None
        return {"code": s["code"], "name": s["name"],
                "price": s["price"], "chg_pct": s["chg_pct"],
                "amount": s["amount"], "total_mv": s["total_mv"],
                **ind}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for f in as_completed([ex.submit(proc, s) for s in cand]):
            try:
                r = f.result()
                if r:
                    results.append(r)
            except:
                pass

    results.sort(key=lambda x: abs(x["close_pct"]), reverse=True)

    result = {
        "results": results,
        "basic_count": len(cand),
        "total": len(stocks),
        "time": round(time.time()-t0, 1),
    }

    if scan_id:
        with scan_progress_lock:
            scan_progress[scan_id] = {"done": prog["total"], "total": prog["total"], "phase": "完成", "status": "done", "result": result}

    return result

# ── Flask 路由 ──

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/scan", methods=["POST"])
def api_scan():
    """启动扫描（异步），立即返回 scan_id"""
    sid = uuid.uuid4().hex[:8]
    with scan_progress_lock:
        scan_progress[sid] = {"done": 0, "total": 0, "phase": "初始化…", "status": "running", "result": None}
    params = request.get_json() or {}
    threading.Thread(target=run_scan, args=(params, sid), daemon=True).start()
    return jsonify({"success": True, "scan_id": sid})

@app.route("/api/scan-progress/<scan_id>")
def api_scan_progress(scan_id):
    with scan_progress_lock:
        p = scan_progress.get(scan_id)
    if not p:
        return jsonify({"success": False, "error": "not found"})
    return jsonify({
        "success": True,
        "status": p["status"],
        "done": p["done"],
        "total": p["total"],
        "phase": p.get("phase", ""),
        "result": p.get("result"),
    })

@app.route("/api/fields")
def api_fields():
    return jsonify({
        "fields": [
            {"id": "zt_count", "label": "20天涨停次数", "unit": "次", "default": 2},
            {"id": "pullback", "label": "回落幅度", "unit": "%", "default": 12},
            {"id": "vol_ratio", "label": "缩量比(MA3/最高量)", "unit": "", "default": 0.55},
            {"id": "close_pct", "label": "当日涨幅", "unit": "%", "default": 3.0},
            {"id": "rsi6", "label": "RSI6", "unit": "", "default": 30},
            {"id": "amplitude", "label": "当日振幅", "unit": "%", "default": 5},
            {"id": "up_days", "label": "连阳天数", "unit": "天", "default": 3},
            {"id": "consecutive_zt", "label": "连板天数", "unit": "天", "default": 2},
            {"id": "k", "label": "K值", "unit": "", "default": 0},
            {"id": "d", "label": "D值", "unit": "", "default": 0},
            {"id": "j", "label": "J值", "unit": "", "default": 0},
            {"id": "ma5", "label": "MA5", "unit": "元", "default": 0},
            {"id": "ma10", "label": "MA10", "unit": "元", "default": 0},
            {"id": "ma20", "label": "MA20", "unit": "元", "default": 0},
            {"id": "ma60", "label": "MA60", "unit": "元", "default": 0},
            {"id": "high_20", "label": "20日最高", "unit": "元", "default": 0},
            {"id": "vwap20", "label": "VWAP(20)", "unit": "元", "default": 0},
            {"id": "vwap60", "label": "VWAP(60)", "unit": "元", "default": 0},
            {"id": "bb_width_ratio", "label": "布林宽度比", "unit": "", "default": 0},
            {"id": "price_pos", "label": "价格位置%", "unit": "%", "default": 0},
            {"id": "gain_3d", "label": "3日涨幅", "unit": "%", "default": 0},
            {"id": "ma_spread", "label": "均线间距", "unit": "", "default": 0},
        ],
        "bool_fields": [
            {"id": "kd_gold", "label": "KD金叉"},
            {"id": "macd_bull", "label": "MACD柱增长"},
            {"id": "ma20_up", "label": "MA20向上"},
            {"id": "ma_bull", "label": "MA多头排列(5>10>20>60)"},
            {"id": "one_line_3ma", "label": "一阳穿三线"},
            {"id": "above_ma60", "label": "站上MA60"},
            {"id": "ma5_gold_ma10", "label": "MA5金叉MA10"},
            {"id": "rsi_gold", "label": "RSI6金叉RSI12"},
            {"id": "break_bb_upper", "label": "突破布林上轨"},
            {"id": "below_bb_lower", "label": "跌破布林下轨"},
            {"id": "vol_breakout", "label": "放量突破(涨3%+量>2倍MA5)"},
            {"id": "doji", "label": "十字星"},
            {"id": "hammer", "label": "锤子线"},
            {"id": "yang_bao_yin", "label": "阳包阴"},
            {"id": "gap_up", "label": "向上跳空缺口"},
            {"id": "new_high_20", "label": "创20日新高"},
            {"id": "chip_concentrated", "label": "筹码集中(BB宽/价<15%)"},
            {"id": "chip_cost_above", "label": "主力成本≥现价(VWAP>现价)"},
            {"id": "chip_low_overhead", "label": "套牢<10%(价在20日高位90%+)"},
            {"id": "chip_stable", "label": "筹码稳定(VWAP20/60差<3%)"},
            {"id": "vol_ladder", "label": "量价阶梯(3日递增缩)"},
            {"id": "vol_moderate", "label": "不放爆量(量≤2.5倍MA5)"},
            {"id": "price_creep", "label": "小阳慢推(3日涨3-8%)"},
            {"id": "ma_convergence", "label": "均线粘合(5/10/20间距<2%)"},
            {"id": "ma_turning", "label": "均线刚拐头+多头排列"},
            {"id": "active_volatility", "label": "活跌度>1.5%(20日均振幅)"},
        ],
    })

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
