# PDCA Plan: Gemini Alpha Hybrid Strategy

## 1. 개요 (Plan)
- **목표**: Claude의 가치 투자 전략 대비 월간 수익률 +5% 상회.
- **핵심 로직**:
    1. **Sentiment Filtering**: Gemini 1.5 Flash를 사용하여 주요 경제 뉴스 및 종목 토론방 데이터 실시간 분석 (긍정/부정 스코어링).
    2. **Quant Signal**: RSI, MACD, 볼린저 밴드의 변동성 돌파 전략 결합.
    3. **Dynamic Risk Control**: 시장 변동성(VIX)에 따라 개별 종목 손절 라인을 2%~5% 사이로 가변 적용.

## 2. 구현 계획 (Do)
- `gemini_quant_engine.py`: 뉴스 데이터 처리 및 감성 분석 모듈 개발.
- `gemini_auto_trader.py`: KIS API 연동 및 매매 로직 구현.
- `strategy_config.json`: Gemini 전용 매매 파라미터 저장.

## 3. 검증 지표 (Check)
- 일일 수익률, 최대 낙폭(MDD), 승률(Win Rate), Claude 대비 상대 수익률.

## 4. 향후 개선 (Act)
- 매주 금요일 폐장 후 `code-analyzer` 스킬로 로직 복기 및 파라미터 튜닝.
