"""
퀀트 분석 엔진
전략: Dual Momentum + Trend Filtering + Inverse Volatility Weighting

수익률 극대화 3단계 접근:
  1. Absolute Momentum  - 상승 추세인 ETF만 선별
  2. Relative Momentum  - 상위 N개 ETF 선택
  3. Volatility Sizing  - 변동성 낮은 종목에 더 많은 비중
"""
import json
import math
import time
from datetime import datetime
from tabulate import tabulate
from api_client import KISClient
from config import ETF_WATCHLIST


# ─────────────────────────────────────────
# 지표 계산 함수들
# ─────────────────────────────────────────

def calc_returns(prices: list) -> list:
    """일별 수익률"""
    return [(prices[i] - prices[i+1]) / prices[i+1] for i in range(len(prices)-1)]

def calc_momentum(prices: list, period: int) -> float:
    """N일 수익률 (모멘텀)"""
    if len(prices) < period + 1:
        return None
    return (prices[0] - prices[period]) / prices[period] * 100

def calc_ma(prices: list, period: int) -> float:
    """단순이동평균"""
    if len(prices) < period:
        return None
    return sum(prices[:period]) / period

def calc_rsi(prices: list, period: int = 14) -> float:
    """RSI"""
    if len(prices) < period + 1:
        return None
    rets = [(prices[i] - prices[i+1]) / prices[i+1] for i in range(len(prices)-1)]
    gains = [r for r in rets[:period] if r > 0]
    losses = [-r for r in rets[:period] if r < 0]
    avg_gain = sum(gains) / period if gains else 0
    avg_loss = sum(losses) / period if losses else 1e-10
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calc_volatility(prices: list, period: int = 20) -> float:
    """연율화 변동성 (%)"""
    if len(prices) < period + 1:
        period = len(prices) - 1
    rets = calc_returns(prices[:period+1])
    if not rets:
        return None
    mean = sum(rets) / len(rets)
    variance = sum((r - mean) ** 2 for r in rets) / len(rets)
    return math.sqrt(variance) * math.sqrt(252) * 100

def calc_sharpe(prices: list, risk_free=0.035) -> float:
    """샤프 비율 (연율화, 무위험수익률 3.5%)"""
    if len(prices) < 5:
        return None
    rets = calc_returns(prices)
    if not rets:
        return None
    mean_daily = sum(rets) / len(rets)
    std_daily  = math.sqrt(sum((r - mean_daily)**2 for r in rets) / len(rets))
    if std_daily == 0:
        return None
    annual_ret = mean_daily * 252
    annual_std = std_daily * math.sqrt(252)
    return (annual_ret - risk_free) / annual_std

def calc_max_drawdown(prices: list) -> float:
    """최대낙폭 (MDD %)"""
    peak = prices[0]
    max_dd = 0
    for p in prices:
        if p > peak:
            peak = p
        dd = (peak - p) / peak * 100
        if dd > max_dd:
            max_dd = dd
    return max_dd


# ─────────────────────────────────────────
# 핵심 전략: Dual Momentum + Volatility
# ─────────────────────────────────────────

class DualMomentumStrategy:
    """
    핵심 전략:
    - Step1: MA5 > MA20 인 ETF만 후보 (상승추세 필터)
    - Step2: 모멘텀 점수 = 1M수익률*0.5 + 3M수익률*0.3 + RSI정규화*0.2
    - Step3: 상위 TOP_N 선택
    - Step4: 역변동성 비중 배분 (변동성 낮을수록 비중 ↑)
    - Stop Loss: 매입가 대비 -7% 시 자동 청산
    """
    TOP_N      = 4      # 보유 종목 수
    STOP_LOSS  = -7.0   # 손절 기준 (%)
    TOTAL_CASH = 100_000_000

    def __init__(self, client: KISClient):
        self.client = client

    def analyze(self, data: dict) -> list:
        """전 종목 퀀트 분석 → 랭킹"""
        results = []

        for ticker, d in data.items():
            prices = [p["close"] for p in d["prices"]]
            if len(prices) < 5:
                continue

            cur   = prices[0]
            ma5   = calc_ma(prices, 5)
            ma20  = calc_ma(prices, 20)
            m1    = calc_momentum(prices, min(20, len(prices)-1))
            m3    = calc_momentum(prices, min(60, len(prices)-1))
            rsi   = calc_rsi(prices, 14)
            vol   = calc_volatility(prices, min(20, len(prices)-1))
            sharpe= calc_sharpe(prices)
            mdd   = calc_max_drawdown(prices)

            # 추세 필터
            trend_ok = (ma5 is not None and ma20 is not None and ma5 > ma20)

            # 모멘텀 점수 (없으면 0)
            w1 = m1 if m1 else 0
            w3 = m3 if m3 else 0
            rsi_norm = ((rsi - 50) / 50 * 100) if rsi else 0
            score = w1 * 0.5 + w3 * 0.3 + rsi_norm * 0.2

            results.append({
                "ticker":   ticker,
                "name":     d["name"],
                "price":    cur,
                "ma5":      round(ma5)  if ma5  else "-",
                "ma20":     round(ma20) if ma20 else "-",
                "trend":    "▲ 상승" if trend_ok else "▼ 하락",
                "m1":       round(m1, 2) if m1 else "-",
                "m3":       round(m3, 2) if m3 else "-",
                "rsi":      round(rsi, 1) if rsi else "-",
                "vol":      round(vol, 1) if vol else "-",
                "sharpe":   round(sharpe, 2) if sharpe else "-",
                "mdd":      round(mdd, 1),
                "score":    round(score, 2),
                "trend_ok": trend_ok,
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    def get_allocation(self, results: list, cash: int) -> list:
        """역변동성 기반 비중 계산"""
        # 추세 필터 + 양수 모멘텀인 종목만
        candidates = [r for r in results if r["trend_ok"] and r["score"] > 0][:self.TOP_N]

        if not candidates:
            print("⚠️  매수 조건을 충족한 종목이 없습니다. 현금 보유.")
            return []

        # 역변동성 가중치
        vols = [r["vol"] if r["vol"] != "-" else 20.0 for r in candidates]
        inv_vols = [1 / v for v in vols]
        total_inv = sum(inv_vols)
        weights = [iv / total_inv for iv in inv_vols]

        allocations = []
        for r, w in zip(candidates, weights):
            alloc_cash = int(cash * w)
            shares = alloc_cash // r["price"]
            actual_cash = shares * r["price"]
            allocations.append({
                **r,
                "weight_pct": round(w * 100, 1),
                "alloc_cash": alloc_cash,
                "shares":     shares,
                "actual_cash": actual_cash,
            })

        return allocations

    def print_analysis(self, results: list):
        """분석 결과 출력"""
        rows = []
        for i, r in enumerate(results):
            trend_icon = "✅" if r["trend_ok"] else "❌"
            rows.append([
                i+1, r["ticker"], r["name"], f"{r['price']:,}",
                trend_icon, r["m1"], r["m3"], r["rsi"],
                r["vol"], r["sharpe"], f"-{r['mdd']}%", r["score"]
            ])

        print("\n" + "═"*90)
        print("  📊 퀀트 분석 결과  —  Dual Momentum + Trend Filter")
        print("═"*90)
        print(tabulate(rows,
            headers=["#","코드","종목명","현재가","추세","1M%","3M%","RSI","변동성","샤프","MDD","점수"],
            tablefmt="rounded_outline"))

    def print_allocation(self, allocations: list):
        """포트폴리오 배분 출력"""
        if not allocations:
            return
        rows = []
        total = sum(a["actual_cash"] for a in allocations)
        for a in allocations:
            rows.append([
                a["ticker"], a["name"], f"{a['weight_pct']}%",
                f"{a['shares']:,}주", f"{a['price']:,}", f"{a['actual_cash']:,}"
            ])
        rows.append(["", "합계", "", "", "", f"{total:,}"])

        print("\n" + "═"*70)
        print("  💼 내일 매수 포트폴리오  (역변동성 가중 배분)")
        print("═"*70)
        print(tabulate(rows,
            headers=["코드","종목명","비중","수량","단가","투자금액"],
            tablefmt="rounded_outline"))
        remaining = self.TOTAL_CASH - total
        print(f"\n  투자금액 합계: {total:,}원  |  현금 유보: {remaining:,}원")
        print(f"  손절 기준: 매입가 대비 -{abs(self.STOP_LOSS)}% 자동 청산\n")


def run_full_analysis():
    """전체 분석 실행"""
    # 저장된 데이터 로드
    try:
        with open("market_data.json") as f:
            data = json.load(f)
    except FileNotFoundError:
        print("❌ market_data.json 없음. 먼저 데이터를 수집하세요.")
        return None, None

    client = KISClient(mock=True)
    strat  = DualMomentumStrategy(client)

    print(f"\n🔬 분석 기준일: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"   데이터: {len(data)}개 ETF × 최대 30일")

    results     = strat.analyze(data)
    allocations = strat.get_allocation(results, strat.TOTAL_CASH)

    strat.print_analysis(results)
    strat.print_allocation(allocations)

    # 결과 저장
    strategy_plan = {
        "generated": datetime.now().isoformat(),
        "analysis":  results,
        "allocations": allocations,
        "stop_loss_pct": strat.STOP_LOSS,
    }
    with open("strategy_plan.json", "w") as f:
        json.dump(strategy_plan, f, ensure_ascii=False, indent=2)

    print("✅ strategy_plan.json 저장 완료")
    return results, allocations


if __name__ == "__main__":
    run_full_analysis()
