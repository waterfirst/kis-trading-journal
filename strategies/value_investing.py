# ============================================================
# Value Investing Strategy (가치투자 + 재무제표 분석)
# 대상: 한국 주식 ETF (config.ETF_WATCHLIST)
# 시드머니: 25,000,000원
# ============================================================

SEED_MONEY = 25_000_000       # 시드머니 (원)
STOP_LOSS = -5.0              # 손절선 (%)
MAX_POSITIONS = 3             # 최대 보유 종목 수
MAX_WEIGHT_PER_TICKER = 0.40  # 종목당 최대 비중 (40%)

# 필터 기준
MIN_RETURN_3M = 10.0          # 3개월 수익률 최소 기준 (%)
RSI_MIN = 30.0                # RSI 하한 (과매도 경계)
RSI_MAX = 85.0                # RSI 상한 (과매수 경계)
MAX_VOLATILITY = 65.0         # 최대 허용 변동성 (%)
MIN_SHARPE = 1.5              # 최소 샤프비율

# 배당/안정형 ETF 목록 (가중치 보너스 부여)
STABLE_ETF_TICKERS = {"069500", "102110"}  # KODEX 200, TIGER 200
HIGH_DIVIDEND_KEYWORDS = ("고배당", "배당", "dividend")

STABLE_SCORE_BONUS = 1.0      # 안정형 ETF 점수 보너스


# ──────────────────────────────────────────────
# 순수 파이썬 수학 유틸리티
# ──────────────────────────────────────────────

def _mean(values: list) -> float:
    """리스트 평균."""
    if not values:
        return 0.0
    return sum(values) / len(values)


def _std(values: list) -> float:
    """모표준편차 (population std)."""
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    variance = sum((v - m) ** 2 for v in values) / len(values)
    return variance ** 0.5


def _pct_change(prices: list) -> list:
    """인접 가격 간 등락률 리스트 (소수 단위, e.g. 0.01 = 1%)."""
    result = []
    for i in range(1, len(prices)):
        prev = prices[i - 1]
        if prev == 0:
            result.append(0.0)
        else:
            result.append((prices[i] - prev) / prev)
    return result


def _calc_return(prices: list, n_days: int) -> float:
    """
    최근 n_days 기간의 수익률 (%).
    prices는 오래된 것부터 최신 순서로 정렬된 종가 리스트.
    """
    if len(prices) < 2:
        return 0.0
    start_idx = max(0, len(prices) - n_days - 1)
    start_price = prices[start_idx]
    end_price = prices[-1]
    if start_price == 0:
        return 0.0
    return (end_price - start_price) / start_price * 100.0


def _calc_volatility_annualized(prices: list, n_days: int = 60) -> float:
    """
    일간 수익률 기반 연환산 변동성 (%).
    n_days: 사용할 최근 일수.
    """
    window = prices[-n_days:] if len(prices) >= n_days else prices
    daily_returns = _pct_change(window)
    if len(daily_returns) < 2:
        return 0.0
    daily_std = _std(daily_returns)
    annualized = daily_std * (252 ** 0.5) * 100.0
    return annualized


def _calc_rsi(prices: list, period: int = 14) -> float:
    """
    RSI (Relative Strength Index) 계산.
    period: RSI 계산 기간 (기본 14일).
    """
    if len(prices) < period + 1:
        return 50.0  # 데이터 부족 시 중립값 반환

    recent = prices[-(period + 1):]
    gains = []
    losses = []
    for i in range(1, len(recent)):
        delta = recent[i] - recent[i - 1]
        if delta > 0:
            gains.append(delta)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(delta))

    avg_gain = _mean(gains)
    avg_loss = _mean(losses)

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _calc_sharpe(prices: list, n_days: int = 60, risk_free_rate_annual: float = 0.035) -> float:
    """
    샤프비율 계산 (연환산).
    risk_free_rate_annual: 무위험 수익률 (기본 3.5% = 한국 국채 기준).
    """
    window = prices[-n_days:] if len(prices) >= n_days else prices
    daily_returns = _pct_change(window)
    if len(daily_returns) < 2:
        return 0.0

    daily_rf = risk_free_rate_annual / 252.0
    excess_returns = [r - daily_rf for r in daily_returns]
    mean_excess = _mean(excess_returns)
    std_excess = _std(excess_returns)

    if std_excess == 0:
        return 0.0
    return (mean_excess / std_excess) * (252 ** 0.5)


def _parse_prices(price_records: list) -> list:
    """
    market_data의 prices 레코드에서 종가(float) 리스트 추출.
    market_data는 최신 날짜가 records[0] (내림차순)이므로 역순 정렬하여 반환.
    계산 함수들은 오름차순(오래된 것 먼저, 최신 것 마지막)을 가정.
    stck_clpr 필드가 없으면 close 필드도 시도.
    """
    result = []
    for rec in price_records:
        raw = rec.get("stck_clpr") or rec.get("close") or rec.get("price")
        if raw is None:
            continue
        try:
            result.append(float(str(raw).replace(",", "")))
        except (ValueError, TypeError):
            continue
    return list(reversed(result))  # 오름차순(오래된→최신) 정렬


def _is_stable_etf(ticker: str, name: str) -> bool:
    """배당/안정형 ETF 여부 판단."""
    if ticker in STABLE_ETF_TICKERS:
        return True
    lower_name = name.lower()
    for keyword in HIGH_DIVIDEND_KEYWORDS:
        if keyword in lower_name:
            return True
    return False


def _score_candidate(m1: float, m3: float, rsi: float, vol: float, sharpe: float,
                     is_stable: bool) -> float:
    """
    후보 종목 점수 산출 (0 ~ 10+).

    점수 구성:
      - 3개월 수익률 점수 (최대 3점): m3 / 10 (상한 3점)
      - RSI 저평가 구간 점수 (최대 2점): RSI가 40~45 구간 중심일수록 높음
      - 저변동성 점수 (최대 2점): 변동성이 낮을수록 높음
      - 샤프비율 점수 (최대 2점): 샤프 1.5 초과분 반영
      - 1개월 수익률 점수 (최대 1점)
      - 안정형 ETF 보너스 (1점)
    """
    # 3개월 수익률 점수
    m3_score = min(m3 / 10.0, 3.0)

    # RSI 저평가 점수: 40~45 구간 중심, 멀어질수록 감점
    rsi_center = 42.5
    rsi_distance = abs(rsi - rsi_center)
    rsi_score = max(0.0, 2.0 - (rsi_distance / 12.5))

    # 저변동성 점수: 변동성 0% → 2점, 30% → 0점
    vol_score = max(0.0, 2.0 - (vol / 15.0))

    # 샤프비율 점수
    sharpe_score = min(max(0.0, (sharpe - MIN_SHARPE) / 1.0), 2.0)

    # 1개월 수익률 점수
    m1_score = min(max(0.0, m1 / 5.0), 1.0)

    # 안정형 ETF 보너스
    stable_bonus = STABLE_SCORE_BONUS if is_stable else 0.0

    total = m3_score + rsi_score + vol_score + sharpe_score + m1_score + stable_bonus
    return round(total, 2)


def _build_reason(ticker: str, name: str, m1: float, m3: float,
                  rsi: float, vol: float, sharpe: float, is_stable: bool) -> str:
    """매수 근거 문자열 생성."""
    parts = []
    if m3 >= MIN_RETURN_3M:
        parts.append(f"3개월 수익률 {m3:.1f}% 달성")
    if RSI_MIN <= rsi <= RSI_MAX:
        parts.append(f"RSI {rsi:.1f} 저평가 구간")
    if vol < MAX_VOLATILITY:
        parts.append(f"변동성 {vol:.1f}% 안정적")
    if sharpe >= MIN_SHARPE:
        parts.append(f"샤프비율 {sharpe:.2f} 우수")
    if is_stable:
        parts.append("배당/안정형 ETF 우대")
    if not parts:
        parts.append("종합 지표 통과")
    return "; ".join(parts)


# ──────────────────────────────────────────────
# 메인 전략 클래스
# ──────────────────────────────────────────────

class ValueInvestingStrategy:
    """
    가치투자 전략 (Value Investing).

    저변동성 + 장기 수익률 + 저RSI + 높은 샤프비율 조건을 조합하여
    저평가된 ETF를 선별하고, 분산 포지션으로 보수적 운용한다.
    """

    STRATEGY_NAME = "Value Investing"
    SEED_MONEY = SEED_MONEY
    STOP_LOSS = STOP_LOSS

    # ── 공개 메서드 ──────────────────────────────

    def get_strategy_name(self) -> str:
        """전략명 반환."""
        return self.STRATEGY_NAME

    def get_description(self) -> str:
        """전략 설명 반환."""
        return (
            "가치투자 전략 (Value Investing)\n"
            "==============================\n"
            f"시드머니    : {SEED_MONEY:,}원\n"
            f"손절선      : {STOP_LOSS:.1f}%\n"
            f"최대 종목수 : {MAX_POSITIONS}종목\n"
            f"종목당 비중 : 최대 {int(MAX_WEIGHT_PER_TICKER * 100)}%\n"
            "\n"
            "[선정 기준]\n"
            f"  1. 저변동성 우선 : 연환산 변동성 < {MAX_VOLATILITY:.0f}%\n"
            f"  2. 장기 수익률   : 3개월 수익률 > {MIN_RETURN_3M:.0f}%\n"
            f"  3. RSI 저평가    : {RSI_MIN:.0f} ~ {RSI_MAX:.0f} 구간 (저평가 매수)\n"
            f"  4. 샤프비율      : > {MIN_SHARPE:.1f} (위험 대비 수익 우수)\n"
            "  5. 배당/안정형 ETF 가중치 부여 (KODEX200, TIGER200, 고배당)\n"
            "\n"
            "[운용 원칙]\n"
            "  - 분산 투자로 개별 종목 리스크 최소화\n"
            "  - 보수적 손절 (-5%) 로 원금 보전 우선\n"
            "  - 장기 보유 관점의 저평가 구간 매수\n"
        )

    def analyze(self, market_data: dict) -> list:
        """
        매수 후보 종목 분석 및 반환.

        Parameters
        ----------
        market_data : dict
            {
              "069500": {
                "name": "KODEX 200",
                "prices": [{"stck_clpr": "90000", "stck_bsop_date": "20260225"}, ...]
              },
              ...
            }
            prices 리스트는 날짜 오름차순(오래된 것 먼저) 을 가정.

        Returns
        -------
        list[dict]
            필터 통과 후보 종목 리스트, 점수 내림차순 정렬.
            각 항목:
              ticker, name, score, reason,
              m1 (1개월 수익률%), m3 (3개월 수익률%),
              rsi, vol (연환산 변동성%), sharpe
        """
        candidates = []

        for ticker, info in market_data.items():
            name = info.get("name", ticker)
            raw_prices = info.get("prices", [])

            prices = _parse_prices(raw_prices)
            if len(prices) < 15:
                # 최소 15일치 데이터 필요
                continue

            # ── 지표 계산 ──
            m1 = _calc_return(prices, n_days=21)    # ~1개월 (영업일 기준)
            m3 = _calc_return(prices, n_days=63)    # ~3개월 (영업일 기준)
            vol = _calc_volatility_annualized(prices)
            rsi = _calc_rsi(prices, period=14)
            sharpe = _calc_sharpe(prices)
            is_stable = _is_stable_etf(ticker, name)

            # ── 필터 적용 ──
            if vol >= MAX_VOLATILITY:
                continue
            if m3 < MIN_RETURN_3M:
                continue
            if not (RSI_MIN <= rsi <= RSI_MAX):
                continue
            if sharpe < MIN_SHARPE:
                continue

            score = _score_candidate(m1, m3, rsi, vol, sharpe, is_stable)
            reason = _build_reason(ticker, name, m1, m3, rsi, vol, sharpe, is_stable)

            current_price = int(prices[-1]) if prices else 0  # 최신 종가
            candidates.append({
                "ticker": ticker,
                "name": name,
                "price": current_price,
                "score": score,
                "reason": reason,
                "m1": round(m1, 2),
                "m3": round(m3, 2),
                "rsi": round(rsi, 2),
                "vol": round(vol, 2),
                "sharpe": round(sharpe, 2),
            })

        # 점수 내림차순 정렬 후 최대 MAX_POSITIONS 종목만 유지
        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates[:MAX_POSITIONS]

    def get_allocations(self, candidates: list, cash: float) -> list:
        """
        후보 종목별 매수 수량 및 비중 배분.

        비중 배분 방식:
          - 점수 기반 비례 배분 (상한: MAX_WEIGHT_PER_TICKER)
          - 가용 현금 내에서 정수 주 단위 매수

        Parameters
        ----------
        candidates : list[dict]
            analyze() 반환값.  각 항목에 "price" 키(현재가)가 있으면 사용,
            없으면 market_data 없이도 동작하도록 0주 처리.
        cash : float
            배분 가능 현금 (원).

        Returns
        -------
        list[dict]
            ticker, name, shares, weight_pct, score, reason,
            m1, m3, rsi, vol, sharpe 포함.
        """
        if not candidates or cash <= 0:
            return []

        n = len(candidates)
        total_score = sum(c["score"] for c in candidates)

        # 점수 비례 목표 비중 계산 (상한 적용)
        if total_score == 0:
            raw_weights = [1.0 / n] * n
        else:
            raw_weights = [c["score"] / total_score for c in candidates]

        # 상한 클리핑: 종목당 MAX_WEIGHT_PER_TICKER 초과 불가
        # 재정규화 없이 클리핑된 비중 그대로 사용 (상한 엄수)
        capped_weights = [min(w, MAX_WEIGHT_PER_TICKER) for w in raw_weights]

        result = []
        remaining_cash = cash

        for i, candidate in enumerate(candidates):
            weight = capped_weights[i]
            alloc_cash = cash * weight

            # 현재가: candidates 항목에 "price" 키가 있으면 사용
            current_price = float(candidate.get("price", 0))

            if current_price > 0 and alloc_cash >= current_price:
                shares = int(alloc_cash // current_price)
                actual_cost = shares * current_price
            else:
                shares = 0
                actual_cost = 0.0

            actual_weight_pct = (actual_cost / cash * 100.0) if cash > 0 else 0.0
            remaining_cash -= actual_cost

            entry = {
                "ticker": candidate["ticker"],
                "name": candidate["name"],
                "shares": shares,
                "weight_pct": round(actual_weight_pct, 2),
                "score": candidate["score"],
                "reason": candidate["reason"],
                "m1": candidate["m1"],
                "m3": candidate["m3"],
                "rsi": candidate["rsi"],
                "vol": candidate["vol"],
                "sharpe": candidate["sharpe"],
            }
            result.append(entry)

        return result
