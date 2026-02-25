"""
텔레그램 알림 + GitHub 투자일지 모듈
"""
import requests
import subprocess
import os
from datetime import datetime

BOT_TOKEN = "7927906835:AAFrilD2u3_maMK8uI5OMWVBJ_yA-Cj4U3Y"
CHAT_ID   = "5767743818"

JOURNAL_FILE = os.path.join(os.path.dirname(__file__), "TRADING_JOURNAL.md")
DIR = os.path.dirname(os.path.abspath(__file__))


# ─────────────────────────────────────────
# 텔레그램 전송
# ─────────────────────────────────────────

def send_telegram(message: str):
    """텔레그램 메시지 전송"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        res = requests.post(url, data={
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }, timeout=10)
        return res.json().get("ok", False)
    except Exception as e:
        print(f"[텔레그램 전송 실패] {e}")
        return False


def notify_buy(ticker, name, shares, price, amount, reason, score, portfolio_cash):
    """매수 알림"""
    msg = f"""🟢 <b>모의투자 매수 체결</b>
━━━━━━━━━━━━━━━━━━
📌 종목: {name} ({ticker})
📊 수량: {shares:,}주 × {price:,}원
💰 금액: {amount:,}원
💵 잔여현금: {portfolio_cash:,}원

📋 <b>투자 이유</b>
{reason}

🤖 퀀트점수: {score}점
📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}"""
    return send_telegram(msg)


def notify_sell(ticker, name, shares, price, amount, profit, profit_pct, reason, portfolio_cash):
    """매도 알림"""
    emoji = "📈" if profit >= 0 else "📉"
    sign  = "+" if profit >= 0 else ""
    msg = f"""🔴 <b>모의투자 매도 체결</b>
━━━━━━━━━━━━━━━━━━
📌 종목: {name} ({ticker})
📊 수량: {shares:,}주 × {price:,}원
💰 금액: {amount:,}원
{emoji} 실현손익: {sign}{profit:,}원 ({sign}{profit_pct:.2f}%)
💵 잔여현금: {portfolio_cash:,}원

📋 <b>매도 이유</b>
{reason}

📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}"""
    return send_telegram(msg)


def notify_daily_report(total_assets, total_profit, total_ret, holdings_summary):
    """일일 결산 알림"""
    sign = "+" if total_profit >= 0 else ""
    emoji = "📈" if total_profit >= 0 else "📉"
    msg = f"""📊 <b>모의투자 일일 결산</b> {datetime.now().strftime('%Y-%m-%d')}
━━━━━━━━━━━━━━━━━━
{emoji} 총 자산: {total_assets:,}원
💹 평가손익: {sign}{total_profit:,}원 ({sign}{total_ret:.2f}%)

<b>보유 종목</b>
{holdings_summary}

🤖 Claude 자동매매 시스템"""
    return send_telegram(msg)


def notify_stop_loss(ticker, name, shares, price, loss, loss_pct):
    """손절 알림"""
    msg = f"""⚠️ <b>손절 자동 실행</b>
━━━━━━━━━━━━━━━━━━
📌 종목: {name} ({ticker})
📊 수량: {shares:,}주 × {price:,}원
📉 손실: {loss:,}원 ({loss_pct:.2f}%)

🛡️ 손절선 -7% 도달로 자동 청산
📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}"""
    return send_telegram(msg)


# ─────────────────────────────────────────
# 투자 일지 (Markdown)
# ─────────────────────────────────────────

def _init_journal():
    """일지 파일 초기화"""
    if not os.path.exists(JOURNAL_FILE):
        with open(JOURNAL_FILE, "w") as f:
            f.write("""# 📒 모의투자 일지
> Claude AI 자동매매 시스템 · 전략: Dual Momentum + Trend Filter + 역변동성 배분

## 전략 요약
- **매수 조건**: MA5 > MA20 (상승추세) + 모멘텀 점수 상위 4개
- **비중 배분**: 역변동성 가중 (변동성 낮을수록 비중 ↑)
- **손절 기준**: 매입가 대비 -7% 자동 청산
- **시드머니**: 1억원

---

""")


def write_journal_buy(ticker, name, shares, price, amount, reason, score, weight_pct, indicators):
    """매수 일지 기록"""
    _init_journal()
    date = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"""## 🟢 매수 | {date} | {name} ({ticker})

| 항목 | 내용 |
|------|------|
| 수량 | {shares:,}주 |
| 단가 | {price:,}원 |
| 금액 | {amount:,}원 |
| 비중 | {weight_pct}% |
| 퀀트점수 | {score}점 |

### 주요 지표
| 지표 | 값 |
|------|----|
| 1개월 수익률 | {indicators.get('m1', '-')}% |
| 3개월 수익률 | {indicators.get('m3', '-')}% |
| RSI | {indicators.get('rsi', '-')} |
| 변동성 | {indicators.get('vol', '-')}% |
| 샤프비율 | {indicators.get('sharpe', '-')} |
| MA5 | {indicators.get('ma5', '-'):,} |
| MA20 | {indicators.get('ma20', '-'):,} |

### 투자 이유
{reason}

---

"""
    with open(JOURNAL_FILE, "a") as f:
        f.write(entry)


def write_journal_sell(ticker, name, shares, price, amount, profit, profit_pct, reason, hold_days=None):
    """매도 일지 기록"""
    _init_journal()
    date = datetime.now().strftime("%Y-%m-%d %H:%M")
    sign = "+" if profit >= 0 else ""
    hold_str = f"{hold_days}일 보유" if hold_days else "-"
    entry = f"""## 🔴 매도 | {date} | {name} ({ticker})

| 항목 | 내용 |
|------|------|
| 수량 | {shares:,}주 |
| 매도가 | {price:,}원 |
| 금액 | {amount:,}원 |
| 실현손익 | {sign}{profit:,}원 ({sign}{profit_pct:.2f}%) |
| 보유기간 | {hold_str} |

### 매도 이유
{reason}

---

"""
    with open(JOURNAL_FILE, "a") as f:
        f.write(entry)


def write_journal_daily(date_str, total_assets, total_profit, total_ret, holdings):
    """일일 결산 일지"""
    _init_journal()
    sign = "+" if total_profit >= 0 else ""
    entry = f"""## 📊 일일결산 | {date_str}

| 항목 | 금액 |
|------|------|
| 총 자산 | {total_assets:,}원 |
| 평가손익 | {sign}{total_profit:,}원 ({sign}{total_ret:.2f}%) |

### 보유 현황
{holdings}

---

"""
    with open(JOURNAL_FILE, "a") as f:
        f.write(entry)


# ─────────────────────────────────────────
# GitHub Push
# ─────────────────────────────────────────

def git_push(commit_message: str):
    """변경사항 커밋 + GitHub 푸시"""
    try:
        subprocess.run(["git", "-C", DIR, "add", "TRADING_JOURNAL.md", "portfolio.json", "trading_log.txt"], check=True)
        result = subprocess.run(
            ["git", "-C", DIR, "diff", "--cached", "--quiet"],
            capture_output=True
        )
        if result.returncode == 0:
            return True  # 변경사항 없음

        subprocess.run(["git", "-C", DIR, "commit", "-m", commit_message], check=True)
        subprocess.run(["git", "-C", DIR, "push", "origin", "main"], check=True)
        print(f"✅ GitHub push 완료: {commit_message}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ GitHub push 실패: {e}")
        return False
