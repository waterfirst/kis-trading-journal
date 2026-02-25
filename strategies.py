"""
투자 전략 모듈
- ETF 모멘텀 전략 (듀얼 모멘텀)
- 이동평균 크로스 전략
- 포트폴리오 리밸런싱
"""
import pandas as pd
from api_client import KISClient
from config import ETF_WATCHLIST
from tabulate import tabulate


class MomentumStrategy:
    """
    ETF 듀얼 모멘텀 전략
    - 1개월/3개월/6개월 수익률 합산으로 순위 산정
    - 상위 N개 ETF 매수, 하락장이면 현금 보유
    """
    def __init__(self, client: KISClient, top_n=5):
        self.client = client
        self.top_n = top_n

    def calc_momentum(self, ticker, period_days=20):
        """모멘텀 점수 계산 (최근 N일 수익률)"""
        chart = self.client.get_daily_chart(ticker, days=period_days + 5)
        if len(chart) < 2:
            return None
        oldest = chart[-1]["close"]
        latest = chart[0]["close"]
        if oldest == 0:
            return None
        return (latest - oldest) / oldest * 100

    def rank_etfs(self):
        """전체 ETF 모멘텀 순위 산정"""
        print("\n📊 ETF 모멘텀 순위 분석 중...")
        scores = []
        for ticker, name in ETF_WATCHLIST.items():
            m1 = self.calc_momentum(ticker, 20)   # 1개월
            m3 = self.calc_momentum(ticker, 60)   # 3개월
            m6 = self.calc_momentum(ticker, 120)  # 6개월
            price_data = self.client.get_price(ticker)
            cur_price = price_data["price"] if price_data else 0

            # 세 기간 평균 모멘텀 점수
            valid = [x for x in [m1, m3, m6] if x is not None]
            score = sum(valid) / len(valid) if valid else 0

            scores.append({
                "ticker": ticker,
                "name": name,
                "price": cur_price,
                "1M%": round(m1, 2) if m1 else "-",
                "3M%": round(m3, 2) if m3 else "-",
                "6M%": round(m6, 2) if m6 else "-",
                "score": round(score, 2),
            })

        scores.sort(key=lambda x: x["score"], reverse=True)

        # 테이블 출력
        rows = [[i+1, s["ticker"], s["name"], f"{s['price']:,}", s["1M%"], s["3M%"], s["6M%"], s["score"]]
                for i, s in enumerate(scores)]
        print(tabulate(rows,
            headers=["순위", "코드", "종목명", "현재가", "1M%", "3M%", "6M%", "점수"],
            tablefmt="rounded_outline"))

        buy_list = [s for s in scores if s["score"] > 0][:self.top_n]
        print(f"\n✅ 매수 추천 TOP {self.top_n}:")
        for i, s in enumerate(buy_list):
            print(f"  {i+1}. {s['name']} ({s['ticker']}) — 점수 {s['score']}%")

        return scores, buy_list


class MAStrategy:
    """
    이동평균 크로스 전략
    - 5일 MA가 20일 MA 위 → 매수 신호
    - 5일 MA가 20일 MA 아래 → 매도 신호
    """
    def __init__(self, client: KISClient):
        self.client = client

    def analyze(self, ticker, name=""):
        """MA 크로스 분석"""
        chart = self.client.get_daily_chart(ticker, days=30)
        if len(chart) < 20:
            return None

        closes = [c["close"] for c in chart]
        ma5  = sum(closes[:5])  / 5
        ma20 = sum(closes[:20]) / 20
        cur  = closes[0]

        signal = "🟢 매수" if ma5 > ma20 else "🔴 매도"
        gap_pct = (ma5 - ma20) / ma20 * 100

        return {
            "ticker": ticker,
            "name": name,
            "price": cur,
            "MA5": round(ma5),
            "MA20": round(ma20),
            "gap%": round(gap_pct, 2),
            "signal": signal,
        }

    def scan_all(self):
        """전체 관심 ETF 스캔"""
        print("\n📈 이동평균 전략 스캔 중...")
        results = []
        for ticker, name in ETF_WATCHLIST.items():
            r = self.analyze(ticker, name)
            if r:
                results.append(r)

        buy  = [r for r in results if "매수" in r["signal"]]
        sell = [r for r in results if "매도" in r["signal"]]

        rows = [[r["ticker"], r["name"], f"{r['price']:,}", f"{r['MA5']:,}", f"{r['MA20']:,}", r["gap%"], r["signal"]]
                for r in results]
        print(tabulate(rows,
            headers=["코드", "종목명", "현재가", "MA5", "MA20", "괴리%", "신호"],
            tablefmt="rounded_outline"))

        print(f"\n✅ 매수 신호: {len(buy)}개  🔴 매도 신호: {len(sell)}개")
        return results


class Rebalancer:
    """
    포트폴리오 리밸런싱
    - 목표 비율 설정 후 현재 비율과 비교
    - 편차가 5% 이상이면 리밸런싱 주문 제안
    """
    def __init__(self, client: KISClient):
        self.client = client

    # 목표 비율 (직접 수정 가능)
    TARGET = {
        "266410": 0.20,  # KODEX 증권 20%
        "069500": 0.15,  # KODEX 200 15%
        "379800": 0.15,  # KODEX 미국S&P500 15%
        "411060": 0.10,  # ACE KRX금현물 10%
        "381170": 0.10,  # TIGER 반도체TOP10 10%
        "395160": 0.10,  # PLUS K방산 10%
        "CASH":   0.20,  # 현금 20%
    }

    def analyze(self):
        """리밸런싱 필요 여부 분석"""
        balance = self.client.get_balance()
        if not balance:
            return

        total = balance["total_eval"] + balance["cash"]
        print(f"\n💼 총 평가금액: {total:,}원  현금: {balance['cash']:,}원")

        rows = []
        for h in balance["holdings"]:
            cur_pct = h["eval_amt"] / total * 100
            target_pct = self.TARGET.get(h["ticker"], 0) * 100
            diff = cur_pct - target_pct
            action = "보유" if abs(diff) < 5 else ("🔴 매도" if diff > 0 else "🟢 매수")
            rows.append([h["ticker"], h["name"], f"{h['qty']:,}주",
                         f"{cur_pct:.1f}%", f"{target_pct:.1f}%", f"{diff:+.1f}%", action])

        print(tabulate(rows,
            headers=["코드", "종목명", "수량", "현재비율", "목표비율", "편차", "조치"],
            tablefmt="rounded_outline"))
