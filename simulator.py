"""
로컬 모의투자 시뮬레이터
- 실시간 시세: KIS API (실제 데이터)
- 매매 기록: 로컬 JSON 파일
- 포트폴리오 수익률 실시간 계산

사용법:
  python3 simulator.py status          # 포트폴리오 현황
  python3 simulator.py buy 069500 10   # KODEX 200 10주 매수 (시장가)
  python3 simulator.py sell 069500 5   # 5주 매도
  python3 simulator.py history         # 거래 내역
  python3 simulator.py momentum        # 모멘텀 전략 신호
  python3 simulator.py reset           # 초기화 (1억 현금으로 리셋)
"""
import sys
import json
import os
import time
from datetime import datetime
from tabulate import tabulate
from api_client import KISClient
from config import ETF_WATCHLIST

PORTFOLIO_FILE = os.path.join(os.path.dirname(__file__), "portfolio.json")
INITIAL_CASH = 100_000_000  # 1억원


def load_portfolio():
    if os.path.exists(PORTFOLIO_FILE):
        with open(PORTFOLIO_FILE) as f:
            return json.load(f)
    return {
        "cash": INITIAL_CASH,
        "holdings": {},   # {ticker: {shares, avg_price, name}}
        "trades": [],
        "created": datetime.now().isoformat(),
    }


def save_portfolio(p):
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(p, f, ensure_ascii=False, indent=2)


def get_current_price(client, ticker):
    data = client.get_price(ticker)
    return data["price"] if data else None


def cmd_status(client):
    """포트폴리오 현황 출력"""
    p = load_portfolio()
    total_invested = 0
    total_eval = 0

    rows = []
    for ticker, h in p["holdings"].items():
        if h["shares"] == 0:
            continue
        cur_price = get_current_price(client, ticker)
        time.sleep(0.25)
        if not cur_price:
            continue

        buy_amt  = h["avg_price"] * h["shares"]
        eval_amt = cur_price * h["shares"]
        profit   = eval_amt - buy_amt
        profit_r = profit / buy_amt * 100

        total_invested += buy_amt
        total_eval     += eval_amt

        rows.append([
            ticker, h["name"],
            f"{h['shares']:,}주",
            f"{h['avg_price']:,}",
            f"{cur_price:,}",
            f"{eval_amt:,}",
            f"{profit:+,}",
            f"{profit_r:+.2f}%",
        ])

    total_assets = total_eval + p["cash"]
    total_profit = total_eval - total_invested
    total_r = total_profit / total_invested * 100 if total_invested > 0 else 0.0

    print("\n" + "═"*60)
    print(f"  💼 모의투자 포트폴리오  ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
    print("═"*60)
    print(f"  총 자산:   {total_assets:>15,}원")
    print(f"  예수금:    {p['cash']:>15,}원")
    print(f"  평가금액:  {total_eval:>15,}원")
    pcolor = "+" if total_profit >= 0 else ""
    print(f"  평가손익:  {pcolor}{total_profit:>14,}원  ({pcolor}{total_r:.2f}%)")
    print("═"*60)

    if rows:
        print(tabulate(rows,
            headers=["코드","종목명","수량","평균단가","현재가","평가금액","손익","수익률"],
            tablefmt="rounded_outline"))
    else:
        print("  보유 종목 없음 — buy 명령어로 매수해보세요!")
    print()


def cmd_buy(client, ticker, shares):
    """매수"""
    p = load_portfolio()

    price = get_current_price(client, ticker)
    if not price:
        print(f"❌ {ticker} 시세 조회 실패")
        return

    name = ETF_WATCHLIST.get(ticker, ticker)
    cost = price * shares

    if cost > p["cash"]:
        max_shares = p["cash"] // price
        print(f"❌ 현금 부족! 최대 {max_shares:,}주 매수 가능 (필요: {cost:,}원 / 보유: {p['cash']:,}원)")
        return

    # 평균단가 계산
    if ticker in p["holdings"] and p["holdings"][ticker]["shares"] > 0:
        existing = p["holdings"][ticker]
        total_shares = existing["shares"] + shares
        total_cost   = existing["avg_price"] * existing["shares"] + cost
        avg_price    = total_cost // total_shares
    else:
        total_shares = shares
        avg_price    = price

    p["holdings"][ticker] = {"shares": total_shares, "avg_price": avg_price, "name": name}
    p["cash"] -= cost

    trade = {
        "type": "BUY",
        "ticker": ticker,
        "name": name,
        "shares": shares,
        "price": price,
        "amount": cost,
        "date": datetime.now().isoformat(),
    }
    p["trades"].append(trade)
    save_portfolio(p)

    print(f"\n✅ 매수 완료!")
    print(f"   {name} ({ticker})  {shares:,}주 × {price:,}원 = {cost:,}원")
    print(f"   잔여 예수금: {p['cash']:,}원\n")


def cmd_sell(client, ticker, shares):
    """매도"""
    p = load_portfolio()

    if ticker not in p["holdings"] or p["holdings"][ticker]["shares"] < shares:
        held = p["holdings"].get(ticker, {}).get("shares", 0)
        print(f"❌ 보유 수량 부족 ({held}주 보유, {shares}주 매도 요청)")
        return

    price = get_current_price(client, ticker)
    if not price:
        print(f"❌ {ticker} 시세 조회 실패")
        return

    h = p["holdings"][ticker]
    revenue = price * shares
    profit  = (price - h["avg_price"]) * shares
    profit_r= profit / (h["avg_price"] * shares) * 100

    p["holdings"][ticker]["shares"] -= shares
    p["cash"] += revenue

    trade = {
        "type": "SELL",
        "ticker": ticker,
        "name": h["name"],
        "shares": shares,
        "price": price,
        "amount": revenue,
        "profit": profit,
        "profit_rate": round(profit_r, 2),
        "date": datetime.now().isoformat(),
    }
    p["trades"].append(trade)
    save_portfolio(p)

    pmark = "+" if profit >= 0 else ""
    print(f"\n✅ 매도 완료!")
    print(f"   {h['name']} ({ticker})  {shares:,}주 × {price:,}원 = {revenue:,}원")
    print(f"   실현손익: {pmark}{profit:,}원 ({pmark}{profit_r:.2f}%)")
    print(f"   잔여 예수금: {p['cash']:,}원\n")


def cmd_history():
    """거래 내역"""
    p = load_portfolio()
    if not p["trades"]:
        print("\n거래 내역이 없습니다.\n")
        return

    rows = []
    for t in reversed(p["trades"][-30:]):
        sign = "🟢" if t["type"] == "BUY" else "🔴"
        profit_str = ""
        if t["type"] == "SELL":
            pr = t.get("profit_rate", 0)
            profit_str = f"{pr:+.2f}%"
        rows.append([
            t["date"][:16], sign + t["type"],
            t["ticker"], t["name"],
            f"{t['shares']:,}주", f"{t['price']:,}", f"{t['amount']:,}", profit_str
        ])

    print(tabulate(rows,
        headers=["일시","구분","코드","종목명","수량","가격","금액","수익률"],
        tablefmt="rounded_outline"))


def cmd_momentum(client):
    """간단한 모멘텀 신호 (현재가 기반)"""
    from strategies import MomentumStrategy
    strat = MomentumStrategy(client, top_n=5)
    scores, buy_list = strat.rank_etfs()

    print("\n💡 추천 종목으로 매수하려면:")
    for s in buy_list:
        print(f"   python3 simulator.py buy {s['ticker']} 10")


def cmd_reset():
    """포트폴리오 초기화"""
    confirm = input("⚠️  포트폴리오를 초기화하시겠습니까? (yes 입력): ")
    if confirm.strip().lower() == "yes":
        p = {
            "cash": INITIAL_CASH,
            "holdings": {},
            "trades": [],
            "created": datetime.now().isoformat(),
        }
        save_portfolio(p)
        print(f"✅ 초기화 완료. 예수금 {INITIAL_CASH:,}원으로 시작합니다.")
    else:
        print("취소했습니다.")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1].lower()
    client = KISClient(mock=True)
    client._ensure_token()

    if cmd == "status":
        cmd_status(client)
    elif cmd == "buy" and len(sys.argv) >= 4:
        cmd_buy(client, sys.argv[2], int(sys.argv[3]))
    elif cmd == "sell" and len(sys.argv) >= 4:
        cmd_sell(client, sys.argv[2], int(sys.argv[3]))
    elif cmd == "history":
        cmd_history()
    elif cmd == "momentum":
        cmd_momentum(client)
    elif cmd == "reset":
        cmd_reset()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
