"""
멀티 전략 관리자 — 4개 전략을 병렬로 운용하고 성과를 비교

전략별 시드: 각 25,000,000원 (총 1억원)
─────────────────────────────────────
🔵 Dual Momentum     — 모멘텀 + 추세 필터 + 역변동성
🟢 Value Investing   — 저평가 + 재무지표 + 저변동성
🟡 News Sentiment    — 뉴스/SNS 감성 + 거래량 급등
🔴 Scalping          — 단기 브레이크아웃 + 볼린저밴드
"""
import json, time, sys, os
from datetime import datetime

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)

from api_client import KISClient
from notifier import send_telegram, git_push

PORTFOLIOS_DIR = os.path.join(DIR, "portfolios")
STRATEGIES_DIR = os.path.join(DIR, "strategies")
JOURNAL_FILE   = os.path.join(DIR, "TRADING_JOURNAL.md")

STRATEGY_META = {
    "dual_momentum":   {"name": "🔵 Dual Momentum",   "stop_loss": -7.0},
    "value_investing": {"name": "🟢 가치투자",           "stop_loss": -5.0},
    "news_sentiment":  {"name": "🟡 뉴스/SNS 감성",     "stop_loss": -8.0},
    "scalping":        {"name": "🔴 스캘핑",            "stop_loss": -3.0},
}


# ──────────────────────────────────────
# 포트폴리오 I/O
# ──────────────────────────────────────

def load_portfolio(strategy: str) -> dict:
    path = os.path.join(PORTFOLIOS_DIR, f"{strategy}.json")
    with open(path) as f:
        return json.load(f)


def save_portfolio(strategy: str, p: dict):
    path = os.path.join(PORTFOLIOS_DIR, f"{strategy}.json")
    with open(path, "w") as f:
        json.dump(p, f, ensure_ascii=False, indent=2)


def get_current_price(client: KISClient, ticker: str) -> int | None:
    try:
        data = client.get_price(ticker)
        if data:
            return int(data.get("stck_prpr", 0))
    except Exception:
        pass
    return None


# ──────────────────────────────────────
# 시장 데이터 갱신
# ──────────────────────────────────────

def refresh_market_data(client: KISClient) -> dict:
    from config import ETF_WATCHLIST
    all_data = {}
    for ticker, name in ETF_WATCHLIST.items():
        chart = client.get_daily_chart(ticker, days=30)
        if chart:
            all_data[ticker] = {"name": name, "prices": chart}
        time.sleep(0.3)
    path = os.path.join(DIR, "market_data.json")
    with open(path, "w") as f:
        json.dump(all_data, f, ensure_ascii=False)
    print(f"[시장데이터] {len(all_data)}개 ETF 갱신 완료")
    return all_data


# ──────────────────────────────────────
# 전략 로드 & 실행
# ──────────────────────────────────────

def load_strategy(strategy_key: str):
    """전략 모듈 동적 로드"""
    import importlib.util
    path = os.path.join(STRATEGIES_DIR, f"{strategy_key}.py")
    if not os.path.exists(path):
        # dual_momentum은 quant_engine 사용
        if strategy_key == "dual_momentum":
            return None
        raise FileNotFoundError(f"전략 파일 없음: {path}")
    spec = importlib.util.spec_from_file_location(strategy_key, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_strategy_analysis(strategy_key: str, market_data: dict) -> list:
    """전략별 매수 후보 분석"""
    if strategy_key == "dual_momentum":
        from quant_engine import run_full_analysis
        _, allocations = run_full_analysis()
        # dual_momentum 포트폴리오 25M 기준으로 수량 재계산
        p = load_portfolio("dual_momentum")
        cash = p["cash"]
        for a in allocations:
            ticker = a.get("ticker", "")
            # market_data에서 현재가 추출
            price = 0
            if ticker in market_data and market_data[ticker]["prices"]:
                p0 = market_data[ticker]["prices"][0]
                price = int(p0.get("close", 0) or p0.get("stck_clpr", 0))
            if not price:
                price = a.get("price", 0)
            w = a.get("weight", 0.25)
            a["shares"] = int(cash * w / price) if price > 0 else 0
            a["price"] = price
        return [a for a in allocations if a.get("shares", 0) > 0]

    mod = load_strategy(strategy_key)
    class_map = {
        "value_investing": "ValueInvestingStrategy",
        "news_sentiment":  "NewsSentimentStrategy",
        "scalping":        "ScalpingStrategy",
    }
    cls_name = class_map.get(strategy_key)
    if not cls_name or not hasattr(mod, cls_name):
        print(f"[경고] {strategy_key}: 클래스 {cls_name} 없음")
        return []
    strategy = getattr(mod, cls_name)()
    p = load_portfolio(strategy_key)
    candidates   = strategy.analyze(market_data)
    allocations  = strategy.get_allocations(candidates, p["cash"])
    return allocations


# ──────────────────────────────────────
# 매수 실행
# ──────────────────────────────────────

def execute_buy(client: KISClient, strategy_key: str, allocations: list, market_data: dict = None):
    p = load_portfolio(strategy_key)
    meta = STRATEGY_META[strategy_key]
    bought = []

    for a in allocations:
        ticker = a["ticker"]
        shares = a.get("shares", 0)
        if shares <= 0:
            continue

        # 1순위: 이미 계산된 price, 2순위: market_data 캐시, 3순위: API
        price = int(a.get("price", 0))
        if not price and market_data and ticker in market_data:
            md_prices = market_data[ticker].get("prices", [])
            if md_prices:
                price = int(md_prices[0].get("close", 0) or md_prices[0].get("stck_clpr", 0))
        if not price:
            price = get_current_price(client, ticker)
            time.sleep(0.7)
        if not price:
            print(f"[{strategy_key}] {a['name']} 시세조회 실패")
            continue

        cost = price * shares
        if cost > p["cash"]:
            shares = p["cash"] // price
            if shares <= 0:
                continue
            cost = shares * price

        existing = p["holdings"].get(ticker, {"shares": 0, "avg_price": price})
        if existing["shares"] > 0:
            total_s = existing["shares"] + shares
            avg_p   = (existing["avg_price"] * existing["shares"] + cost) // total_s
        else:
            total_s, avg_p = shares, price

        p["holdings"][ticker] = {
            "shares": total_s, "avg_price": avg_p,
            "name": a["name"], "buy_date": datetime.now().isoformat()
        }
        p["cash"] -= cost
        p["trades"].append({
            "type": "BUY", "ticker": ticker, "name": a["name"],
            "shares": shares, "price": price, "amount": cost,
            "date": datetime.now().isoformat(), "strategy": strategy_key
        })

        reason  = a.get("reason", "퀀트 조건 충족")
        score   = a.get("score", 0)
        weight  = a.get("weight_pct", 0)

        print(f"[{meta['name']}] 매수: {a['name']} {shares:,}주 × {price:,}원 = {cost:,}원")

        # 텔레그램 알림
        send_telegram(f"""{meta['name']} <b>매수</b>
📌 {a['name']} ({ticker})
📊 {shares:,}주 × {price:,}원 = {cost:,}원
📋 <b>투자 이유</b>
{reason}
🤖 점수: {score} | 비중: {weight}%""")

        # 일지 기록
        _write_buy_journal(strategy_key, meta["name"], ticker, a["name"],
                           shares, price, cost, reason, score, weight, a)
        bought.append(a)

    save_portfolio(strategy_key, p)
    if bought:
        names = ", ".join(a["name"] for a in bought)
        git_push(f"[{meta['name']} 매수] {datetime.now().strftime('%Y-%m-%d')} — {names}")
    return bought


# ──────────────────────────────────────
# 손절 체크
# ──────────────────────────────────────

def check_stop_loss_all(client: KISClient):
    for key, meta in STRATEGY_META.items():
        p = load_portfolio(key)
        stop = meta["stop_loss"]
        sold_any = False

        for ticker, h in list(p["holdings"].items()):
            if h["shares"] <= 0:
                continue
            cur = get_current_price(client, ticker)
            time.sleep(0.6)
            if not cur:
                continue

            pct = (cur - h["avg_price"]) / h["avg_price"] * 100
            if pct <= stop:
                revenue = cur * h["shares"]
                profit  = (cur - h["avg_price"]) * h["shares"]
                p["cash"] += revenue
                p["holdings"][ticker]["shares"] = 0
                p["trades"].append({
                    "type": "SELL", "ticker": ticker, "name": h["name"],
                    "shares": h["shares"], "price": cur, "amount": revenue,
                    "profit": profit, "profit_rate": round(pct, 2),
                    "date": datetime.now().isoformat(), "reason": "STOP_LOSS",
                    "strategy": key
                })
                reason = f"손절선 {stop}% 도달 ({pct:+.2f}%) — 자동 청산"
                print(f"[{meta['name']}] 🔴 손절: {h['name']} {pct:+.2f}%")
                send_telegram(f"⚠️ {meta['name']} <b>손절</b>\n{h['name']} {pct:+.2f}%\n손실: {profit:,}원")
                _write_sell_journal(key, meta["name"], ticker, h["name"],
                                    h["shares"], cur, revenue, profit, pct, reason)
                sold_any = True

        if sold_any:
            save_portfolio(key, p)
            git_push(f"[{meta['name']} 손절] {datetime.now().strftime('%Y-%m-%d %H:%M')}")


# ──────────────────────────────────────
# 전략 비교 리포트
# ──────────────────────────────────────

def generate_comparison_report(client: KISClient, market_data: dict = None) -> str:
    lines = []
    total_assets_all = 0
    total_seed_all   = 0
    summary_data = {}

    for key, meta in STRATEGY_META.items():
        p = load_portfolio(key)
        total_eval = 0
        total_cost = 0
        hold_lines = []

        for ticker, h in p["holdings"].items():
            if h["shares"] <= 0:
                continue
            cur = 0
            if market_data and ticker in market_data:
                md = market_data[ticker].get("prices", [])
                if md:
                    cur = int(md[0].get("close", 0) or md[0].get("stck_clpr", 0))
            if not cur:
                cur = get_current_price(client, ticker)
                time.sleep(0.5)
            if not cur:
                cur = h["avg_price"]  # fallback: 매수가 사용

            ea = cur * h["shares"]
            ca = h["avg_price"] * h["shares"]
            pct = (cur - h["avg_price"]) / h["avg_price"] * 100
            total_eval += ea
            total_cost += ca
            sign = "+" if pct >= 0 else ""
            hold_lines.append(f"    {h['name'][:12]:12s} {sign}{pct:.2f}%")

        total_profit = total_eval - total_cost
        cash = p["cash"]
        total_assets = total_eval + cash
        ret = total_profit / total_cost * 100 if total_cost > 0 else 0.0

        # 포트폴리오에 수익률 업데이트
        p["total_profit"] = total_profit
        p["total_return_pct"] = round(ret, 2)
        save_portfolio(key, p)

        seed = p["seed"]
        total_assets_all += total_assets
        total_seed_all   += seed

        emoji = "📈" if ret >= 0 else "📉"
        sign  = "+" if ret >= 0 else ""
        lines.append(f"{meta['name']}")
        lines.append(f"  {emoji} 수익률: {sign}{ret:.2f}%  |  평가손익: {sign}{total_profit:,}원")
        lines.append(f"  총자산: {total_assets:,}원  (현금: {cash:,}원)")
        if hold_lines:
            lines.extend(hold_lines)
        lines.append("")

        summary_data[key] = {
            "name": meta["name"], "return_pct": round(ret, 2),
            "total_profit": total_profit, "total_assets": total_assets
        }

    # 전체 합산
    overall_profit = total_assets_all - total_seed_all
    overall_ret    = overall_profit / total_seed_all * 100 if total_seed_all > 0 else 0.0
    sign = "+" if overall_profit >= 0 else ""

    # 순위 정렬
    ranked = sorted(summary_data.items(), key=lambda x: x[1]["return_pct"], reverse=True)
    rank_txt = "\n".join(
        f"  {i+1}위 {v['name']:14s} {'+' if v['return_pct']>=0 else ''}{v['return_pct']:.2f}%"
        for i, (k, v) in enumerate(ranked)
    )

    report = f"""
╔══════════════════════════════════════════════════╗
  📊 멀티 전략 비교 리포트  {datetime.now().strftime('%Y-%m-%d %H:%M')}
╠══════════════════════════════════════════════════╣
  총 운용 자산: {total_assets_all:>14,}원
  전체 수익:   {sign}{overall_profit:>13,}원  ({sign}{overall_ret:.2f}%)
╠══════════════════════════════════════════════════╣
{chr(10).join('  ' + l for l in lines)}
  🏆 전략 순위
{rank_txt}
╚══════════════════════════════════════════════════╝"""

    print(report)

    # summary.json 업데이트
    summary_path = os.path.join(PORTFOLIOS_DIR, "summary.json")
    with open(summary_path, "w") as f:
        json.dump({
            "total_seed": total_seed_all,
            "total_assets": total_assets_all,
            "overall_profit": overall_profit,
            "overall_return_pct": round(overall_ret, 2),
            "strategies": summary_data,
            "last_updated": datetime.now().isoformat()
        }, f, ensure_ascii=False, indent=2)

    # 텔레그램 전송
    tg = f"📊 <b>멀티전략 일일결산</b> {datetime.now().strftime('%Y-%m-%d')}\n"
    tg += f"전체 수익: {sign}{overall_profit:,}원 ({sign}{overall_ret:.2f}%)\n\n"
    for i, (k, v) in enumerate(ranked):
        s = "+" if v["return_pct"] >= 0 else ""
        tg += f"{'🥇🥈🥉🏅'[i]} {v['name']} {s}{v['return_pct']:.2f}%\n"
    send_telegram(tg)

    # 일지 기록 + GitHub push
    _write_daily_journal(datetime.now().strftime("%Y-%m-%d"), summary_data,
                         overall_profit, overall_ret)
    git_push(f"[멀티전략 결산] {datetime.now().strftime('%Y-%m-%d')} 전체수익 {sign}{overall_ret:.2f}%")

    return report


# ──────────────────────────────────────
# 투자 일지
# ──────────────────────────────────────

def _write_buy_journal(strategy_key, strategy_name, ticker, name, shares,
                       price, amount, reason, score, weight, a):
    date = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"""## {strategy_name} 🟢 매수 | {date} | {name} ({ticker})

| 항목 | 내용 |
|------|------|
| 전략 | {strategy_name} |
| 수량 | {shares:,}주 |
| 단가 | {price:,}원 |
| 금액 | {amount:,}원 |
| 비중 | {weight}% |
| 퀀트점수 | {score}점 |

### 주요 지표
| 지표 | 값 |
|------|----|
| 1개월 수익률 | {a.get('m1', '-')}% |
| 3개월 수익률 | {a.get('m3', '-')}% |
| RSI | {a.get('rsi', '-')} |
| 변동성 | {a.get('vol', '-')}% |
| 샤프비율 | {a.get('sharpe', '-')} |

### 투자 이유
{reason}

---

"""
    with open(JOURNAL_FILE, "a") as f:
        f.write(entry)


def _write_sell_journal(strategy_key, strategy_name, ticker, name,
                        shares, price, amount, profit, pct, reason):
    date = datetime.now().strftime("%Y-%m-%d %H:%M")
    sign = "+" if profit >= 0 else ""
    entry = f"""## {strategy_name} 🔴 매도 | {date} | {name} ({ticker})

| 항목 | 내용 |
|------|------|
| 전략 | {strategy_name} |
| 수량 | {shares:,}주 |
| 매도가 | {price:,}원 |
| 금액 | {amount:,}원 |
| 실현손익 | {sign}{profit:,}원 ({sign}{pct:.2f}%) |

### 매도 이유
{reason}

---

"""
    with open(JOURNAL_FILE, "a") as f:
        f.write(entry)


def _write_daily_journal(date_str, summary_data, overall_profit, overall_ret):
    sign = "+" if overall_profit >= 0 else ""
    ranked = sorted(summary_data.items(), key=lambda x: x[1]["return_pct"], reverse=True)
    rows = "\n".join(
        f"| {v['name']} | {'+' if v['return_pct']>=0 else ''}{v['return_pct']:.2f}% | {'+' if v['total_profit']>=0 else ''}{v['total_profit']:,}원 |"
        for _, v in ranked
    )
    entry = f"""## 📊 멀티전략 일일결산 | {date_str}

| 항목 | 금액 |
|------|------|
| 전체 수익 | {sign}{overall_profit:,}원 |
| 전체 수익률 | {sign}{overall_ret:.2f}% |

### 전략별 성과
| 전략 | 수익률 | 손익 |
|------|--------|------|
{rows}

---

"""
    with open(JOURNAL_FILE, "a") as f:
        f.write(entry)


# ──────────────────────────────────────
# 메인 실행
# ──────────────────────────────────────

def run_all_strategies():
    client = KISClient(mock=True)
    client._ensure_token()

    print("=" * 55)
    print("  멀티 전략 자동매매 시작")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 55)

    send_telegram(f"""🤖 <b>멀티 전략 자동매매 시작</b>
{datetime.now().strftime('%Y-%m-%d %H:%M')}
🔵 Dual Momentum | 🟢 가치투자
🟡 뉴스감성 | 🔴 스캘핑
총 운용: 1억원 (전략별 2,500만원)""")

    # 1. 시장 데이터 갱신
    print("\n[1/4] 시장 데이터 갱신...")
    market_data = refresh_market_data(client)

    # 2. 전략별 분석 & 매수
    print("\n[2/4] 전략별 분석 & 매수 실행...")
    for key in STRATEGY_META:
        p = load_portfolio(key)
        has_holdings = any(h["shares"] > 0 for h in p["holdings"].values())
        if has_holdings:
            print(f"  [{STRATEGY_META[key]['name']}] 기존 포지션 보유 — 스킵")
            continue
        try:
            print(f"  [{STRATEGY_META[key]['name']}] 분석 중...")
            allocations = run_strategy_analysis(key, market_data)
            if allocations:
                execute_buy(client, key, allocations, market_data)
            else:
                print(f"  [{STRATEGY_META[key]['name']}] 매수 후보 없음")
        except Exception as e:
            print(f"  [{STRATEGY_META[key]['name']}] 오류: {e}")
            send_telegram(f"❌ {STRATEGY_META[key]['name']} 오류: {e}")

    # 3. 10분마다 손절 체크 → 메인루프에서 호출
    print("\n[3/4] 손절 체크...")
    check_stop_loss_all(client)

    # 4. 결산
    print("\n[4/4] 전략 비교 결산...")
    generate_comparison_report(client, market_data)


def load_cached_market_data() -> dict:
    """저장된 market_data.json을 로드"""
    path = os.path.join(DIR, "market_data.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        client = KISClient(mock=True)
        client._ensure_token()
        market_data = load_cached_market_data()
        generate_comparison_report(client, market_data)
    elif len(sys.argv) > 1 and sys.argv[1] == "stoploss":
        client = KISClient(mock=True)
        client._ensure_token()
        check_stop_loss_all(client)
    else:
        run_all_strategies()
