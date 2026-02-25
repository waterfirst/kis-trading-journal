"""
자동 매매 봇 — 매일 09:05 KST 자동 실행
실행: python3 auto_trader.py        (스케줄 모드)
      python3 auto_trader.py now    (즉시 실행)

기능:
  - 08:55 최신 퀀트 분석 실행
  - 09:01 시장 오픈 후 포트폴리오 구성 + 텔레그램 알림 + 일지 기록
  - 10분마다 손절 모니터링 (-7%) + 텔레그램 알림
  - 15:20 일일 결산 + 텔레그램 + GitHub push
"""
import time, json, sys
from datetime import datetime, time as dtime
from api_client import KISClient
from simulator import load_portfolio, save_portfolio, get_current_price
from quant_engine import run_full_analysis
from config import ETF_WATCHLIST
from notifier import (
    send_telegram, notify_buy, notify_sell, notify_daily_report,
    notify_stop_loss, write_journal_buy, write_journal_sell,
    write_journal_daily, git_push
)

STOP_LOSS_PCT = -7.0
LOG_FILE = "trading_log.txt"


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def is_market_open():
    t = datetime.now().time()
    return dtime(9, 0) <= t <= dtime(15, 30)


def refresh_market_data(client):
    log("📡 시장 데이터 갱신 중...")
    all_data = {}
    for ticker, name in ETF_WATCHLIST.items():
        chart = client.get_daily_chart(ticker, days=30)
        if chart:
            all_data[ticker] = {"name": name, "prices": chart}
        time.sleep(0.3)
    with open("market_data.json", "w") as f:
        json.dump(all_data, f, ensure_ascii=False)
    log(f"✅ {len(all_data)}개 ETF 데이터 갱신")
    return all_data


# ─────────────────────────────────────────────────────
# 투자 이유 생성 (퀀트 지표 기반 자동 서술)
# ─────────────────────────────────────────────────────

def build_buy_reason(a: dict) -> str:
    reasons = []
    m1  = a.get("m1",  "-")
    m3  = a.get("m3",  "-")
    rsi = a.get("rsi", "-")
    vol = a.get("vol", "-")
    sharpe = a.get("sharpe", "-")

    if m1 != "-" and m1 > 20:
        reasons.append(f"1개월 수익률 {m1:+.1f}% — 강한 단기 모멘텀")
    if m3 != "-" and m3 > 20:
        reasons.append(f"3개월 수익률 {m3:+.1f}% — 지속적 상승 추세 확인")
    if a.get("trend_ok"):
        reasons.append(f"MA5({a.get('ma5','-'):,}) > MA20({a.get('ma20','-'):,}) — 골든크로스 유지 중")
    if rsi != "-" and rsi > 60:
        reasons.append(f"RSI {rsi:.0f} — 강세 구간 (과매수 아님)")
    if vol != "-" and vol < 30:
        reasons.append(f"연율 변동성 {vol:.1f}% — 낮은 변동성으로 역변동성 비중 증가")
    if sharpe != "-" and sharpe > 5:
        reasons.append(f"샤프비율 {sharpe:.2f} — 위험 대비 수익률 우수")

    if not reasons:
        reasons.append(f"퀀트 종합점수 {a.get('score', 0):.2f}점으로 매수 조건 충족")

    return "\n".join(f"• {r}" for r in reasons)


def build_sell_reason(pct: float, reason_type: str) -> str:
    if reason_type == "STOP_LOSS":
        return f"• 손절선 -7% 도달 ({pct:+.2f}%) — 리스크 관리 원칙에 따른 자동 청산"
    elif reason_type == "REBALANCE":
        return f"• 리밸런싱 — 모멘텀 순위 하락으로 포트폴리오 교체"
    return f"• 매도 조건 충족 ({pct:+.2f}%)"


# ─────────────────────────────────────────────────────
# 매수 실행
# ─────────────────────────────────────────────────────

def execute_buy_plan(client, allocations):
    p = load_portfolio()
    bought = []

    for a in allocations:
        ticker = a["ticker"]
        shares = a.get("shares", 0)
        if shares <= 0:
            continue

        price = get_current_price(client, ticker)
        time.sleep(0.4)
        if not price:
            log(f"⚠️  {a['name']} 시세 조회 실패, 스킵")
            continue

        cost = price * shares
        if cost > p["cash"]:
            max_s = p["cash"] // price
            if max_s <= 0:
                log(f"⚠️  {a['name']} 현금 부족, 스킵")
                continue
            shares, cost = max_s, max_s * price

        existing = p["holdings"].get(ticker, {"shares": 0, "avg_price": price})
        if existing["shares"] > 0:
            total_shares = existing["shares"] + shares
            avg_price    = (existing["avg_price"] * existing["shares"] + cost) // total_shares
        else:
            total_shares, avg_price = shares, price

        p["holdings"][ticker] = {"shares": total_shares, "avg_price": avg_price, "name": a["name"],
                                  "buy_date": datetime.now().isoformat()}
        p["cash"] -= cost
        p["trades"].append({
            "type": "BUY", "ticker": ticker, "name": a["name"],
            "shares": shares, "price": price, "amount": cost,
            "date": datetime.now().isoformat(),
        })

        reason = build_buy_reason(a)
        log(f"✅ 매수: {a['name']} {shares:,}주 × {price:,}원 = {cost:,}원")

        # 텔레그램 알림
        notify_buy(ticker, a["name"], shares, price, cost, reason,
                   a.get("score", 0), p["cash"])

        # 투자일지 기록
        indicators = {k: a.get(k) for k in ["m1","m3","rsi","vol","sharpe","ma5","ma20"]}
        write_journal_buy(ticker, a["name"], shares, price, cost, reason,
                          a.get("score", 0), a.get("weight_pct", 0), indicators)

        bought.append(a)

    save_portfolio(p)

    # GitHub push
    commit_msg = f"[매수] {datetime.now().strftime('%Y-%m-%d')} — {', '.join(a['name'] for a in bought)}"
    git_push(commit_msg)

    log(f"📊 매수 완료 {len(bought)}종목 | 잔여예수금: {p['cash']:,}원")
    return bought


# ─────────────────────────────────────────────────────
# 손절 체크
# ─────────────────────────────────────────────────────

def check_stop_loss(client):
    p = load_portfolio()
    sold_any = False

    for ticker, h in list(p["holdings"].items()):
        if h["shares"] <= 0:
            continue
        cur = get_current_price(client, ticker)
        time.sleep(0.25)
        if not cur:
            continue

        pct = (cur - h["avg_price"]) / h["avg_price"] * 100
        if pct <= STOP_LOSS_PCT:
            shares  = h["shares"]
            revenue = cur * shares
            profit  = (cur - h["avg_price"]) * shares

            p["cash"] += revenue
            p["holdings"][ticker]["shares"] = 0
            p["trades"].append({
                "type": "SELL", "ticker": ticker, "name": h["name"],
                "shares": shares, "price": cur, "amount": revenue,
                "profit": profit, "profit_rate": round(pct, 2),
                "date": datetime.now().isoformat(), "reason": "STOP_LOSS",
            })

            reason = build_sell_reason(pct, "STOP_LOSS")
            log(f"🔴 손절: {h['name']} {shares:,}주 | {pct:+.2f}% | 손실: {profit:,}원")

            # 텔레그램 알림
            notify_stop_loss(ticker, h["name"], shares, cur, profit, pct)

            # 투자일지
            buy_date = h.get("buy_date")
            hold_days = None
            if buy_date:
                from datetime import datetime as dt
                hold_days = (dt.now() - dt.fromisoformat(buy_date)).days
            write_journal_sell(ticker, h["name"], shares, cur, revenue,
                               profit, pct, reason, hold_days)
            sold_any = True

    if sold_any:
        save_portfolio(p)
        git_push(f"[손절] {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    return sold_any


# ─────────────────────────────────────────────────────
# 일일 결산
# ─────────────────────────────────────────────────────

def daily_report(client):
    p = load_portfolio()
    total_eval, total_cost, lines, tg_lines = 0, 0, [], []

    for ticker, h in p["holdings"].items():
        if h["shares"] <= 0:
            continue
        cur = get_current_price(client, ticker)
        time.sleep(0.25)
        if not cur:
            continue
        eval_amt = cur * h["shares"]
        cost_amt = h["avg_price"] * h["shares"]
        pct = (cur - h["avg_price"]) / h["avg_price"] * 100
        total_eval += eval_amt
        total_cost += cost_amt
        sign = "+" if pct >= 0 else ""
        lines.append(f"| {h['name']} | {h['shares']:,}주 | {sign}{pct:.2f}% | {eval_amt:,}원 |")
        tg_lines.append(f"  {h['name'][:10]:10s} {sign}{pct:.2f}%  {eval_amt:,}원")

    total_profit = total_eval - total_cost
    total_assets = total_eval + p["cash"]
    total_ret    = total_profit / total_cost * 100 if total_cost > 0 else 0.0

    report_text = f"""
╔══════════════════════════════════════════════╗
  📈 일일 결산  {datetime.now().strftime('%Y-%m-%d')}
╠══════════════════════════════════════════════╣
  총 자산:  {total_assets:>12,}원
  평가손익: {total_profit:>+12,}원  ({total_ret:+.2f}%)
  예수금:   {p['cash']:>12,}원
{"".join(chr(10)+'  '+l for l in tg_lines)}
╚══════════════════════════════════════════════╝"""
    log(report_text)

    # 텔레그램 알림
    holdings_str = "\n".join(tg_lines) if tg_lines else "  (보유 종목 없음)"
    notify_daily_report(total_assets, total_profit, total_ret, holdings_str)

    # 투자일지 (테이블 형식)
    holdings_md = "| 종목 | 수량 | 수익률 | 평가금액 |\n|------|------|--------|----------|\n"
    holdings_md += "\n".join(lines) if lines else "| - | - | - | - |"
    write_journal_daily(datetime.now().strftime("%Y-%m-%d"), total_assets,
                        total_profit, total_ret, holdings_md)

    # GitHub push
    git_push(f"[결산] {datetime.now().strftime('%Y-%m-%d')} 수익률 {total_ret:+.2f}%")

    return total_ret


# ─────────────────────────────────────────────────────
# 메인 루프
# ─────────────────────────────────────────────────────

def run_trading_day():
    client = KISClient(mock=True)
    client._ensure_token()
    send_telegram(f"🤖 <b>자동매매 봇 시작</b>\n{datetime.now().strftime('%Y-%m-%d %H:%M')}\n전략: Dual Momentum + Trend Filter")
    log("🤖 자동매매 봇 시작")

    morning_done = False
    stop_loss_last = 0

    while True:
        now = datetime.now()
        t   = now.time()

        # 08:55 — 분석
        if dtime(8, 55) <= t <= dtime(8, 58) and not morning_done:
            log("☀️  장 전 분석 시작")
            refresh_market_data(client)
            results, allocations = run_full_analysis()
            if allocations:
                names = ", ".join(a["name"] for a in allocations)
                log(f"📋 매수 계획: {names}")
                send_telegram(f"☀️ <b>장 전 분석 완료</b>\n매수 예정: {names}")
            morning_done = True

        # 09:01 — 매수
        elif dtime(9, 1) <= t <= dtime(9, 5) and morning_done:
            p = load_portfolio()
            has_holdings = any(h["shares"] > 0 for h in p["holdings"].values())
            if not has_holdings:
                log("🟢 시장 오픈! 매수 실행")
                try:
                    with open("strategy_plan.json") as f:
                        plan = json.load(f)
                    execute_buy_plan(client, plan["allocations"])
                except Exception as e:
                    log(f"❌ 매수 실패: {e}")
                    send_telegram(f"❌ 매수 실패: {e}")

        # 10분마다 손절 체크
        elif is_market_open() and (now.minute % 10 == 0) and now.second < 15:
            if time.time() - stop_loss_last > 550:
                check_stop_loss(client)
                stop_loss_last = time.time()

        # 15:20 — 결산
        elif dtime(15, 20) <= t <= dtime(15, 25):
            log("📊 장 마감 결산")
            daily_report(client)
            morning_done = False
            break

        time.sleep(10)

    log("✅ 오늘 자동매매 완료")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "now":
        client = KISClient(mock=True)
        client._ensure_token()
        refresh_market_data(client)
        results, allocations = run_full_analysis()
        if allocations:
            execute_buy_plan(client, allocations)
        daily_report(client)
    else:
        run_trading_day()
