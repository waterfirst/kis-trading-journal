"""
KIS 모의투자 메인 실행 파일
사용법: python main.py [명령]

명령:
  token     - 토큰 발급 테스트
  price     - ETF 시세 조회
  balance   - 잔고 조회
  momentum  - 모멘텀 전략 분석
  ma        - 이동평균 전략 분석
  rebalance - 리밸런싱 분석
  buy TICKER QTY  - 모의투자 매수
  sell TICKER QTY - 모의투자 매도
"""
import sys
import time
from api_client import KISClient
from strategies import MomentumStrategy, MAStrategy, Rebalancer
from config import ETF_WATCHLIST
from tabulate import tabulate


def cmd_token(client):
    """토큰 발급 테스트"""
    print("🔑 토큰 발급 시도...")
    ok = client.get_token()
    if ok:
        print(f"  Token: {client.access_token[:30]}...")


def cmd_price(client):
    """전체 관심 ETF 현재가 출력"""
    print("\n💹 ETF 현재가 조회 중...")
    rows = []
    for ticker, name in ETF_WATCHLIST.items():
        p = client.get_price(ticker)
        time.sleep(0.25)
        if p:
            sign = "▲" if p["change"] >= 0 else "▼"
            rows.append([
                ticker, name, f"{p['price']:,}",
                f"{sign}{abs(p['change']):,}", f"{p['change_pct']:+.2f}%",
                f"{p['volume']:,}"
            ])
    print(tabulate(rows,
        headers=["코드", "종목명", "현재가", "전일대비", "등락률", "거래량"],
        tablefmt="rounded_outline"))


def cmd_balance(client):
    """잔고 조회"""
    b = client.get_balance()
    if not b:
        return
    print(f"\n💼 잔고 현황")
    print(f"  총 평가금액: {b['total_eval']:,}원")
    print(f"  평가손익:    {b['total_profit']:+,}원")
    print(f"  예수금:      {b['cash']:,}원")
    if b["holdings"]:
        rows = [[h["ticker"], h["name"], f"{h['qty']:,}주",
                 f"{h['avg_price']:,}", f"{h['cur_price']:,}",
                 f"{h['eval_amt']:,}", f"{h['profit']:+,}", f"{h['profit_pct']:+.2f}%"]
                for h in b["holdings"]]
        print(tabulate(rows,
            headers=["코드", "종목명", "수량", "평균단가", "현재가", "평가금액", "손익", "수익률"],
            tablefmt="rounded_outline"))
    else:
        print("  보유 종목 없음")


def main():
    client = KISClient(mock=True)

    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1].lower()

    # 캐시 토큰이 없을 때만 새로 발급
    client._ensure_token()

    if cmd == "token":
        cmd_token(client)

    elif cmd == "price":
        cmd_price(client)

    elif cmd == "balance":
        cmd_balance(client)

    elif cmd == "momentum":
        strat = MomentumStrategy(client, top_n=5)
        strat.rank_etfs()

    elif cmd == "ma":
        strat = MAStrategy(client)
        strat.scan_all()

    elif cmd == "rebalance":
        reb = Rebalancer(client)
        reb.analyze()

    elif cmd == "buy" and len(sys.argv) >= 4:
        ticker, qty = sys.argv[2], int(sys.argv[3])
        price = int(sys.argv[4]) if len(sys.argv) >= 5 else 0
        client.order_buy(ticker, qty, price)

    elif cmd == "sell" and len(sys.argv) >= 4:
        ticker, qty = sys.argv[2], int(sys.argv[3])
        price = int(sys.argv[4]) if len(sys.argv) >= 5 else 0
        client.order_sell(ticker, qty, price)

    else:
        print(__doc__)


if __name__ == "__main__":
    main()
