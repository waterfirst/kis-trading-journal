"""
한국투자증권 KIS Open API 클라이언트
모의투자 전용
"""
import requests
import json
import time
import os
from datetime import datetime, timedelta
from config import APP_KEY, APP_SECRET, MOCK_BASE_URL, REAL_BASE_URL, USE_MOCK, MOCK_ACCOUNT

TOKEN_FILE = os.path.join(os.path.dirname(__file__), ".token_cache.json")


class KISClient:
    def __init__(self, mock=True):
        self.mock = mock
        self.base_url = MOCK_BASE_URL if mock else REAL_BASE_URL
        self.access_token = None
        self.token_expires = None
        self.account_no = MOCK_ACCOUNT  # 예: "50123456-01"
        self._load_cached_token()

    def _load_cached_token(self):
        """파일에서 캐시된 토큰 로드"""
        if os.path.exists(TOKEN_FILE):
            try:
                with open(TOKEN_FILE) as f:
                    d = json.load(f)
                expires = datetime.fromisoformat(d["expires"])
                if datetime.now() < expires:
                    self.access_token = d["token"]
                    self.token_expires = expires
            except Exception:
                pass

    def _save_token(self):
        """토큰 파일에 저장"""
        with open(TOKEN_FILE, "w") as f:
            json.dump({"token": self.access_token, "expires": self.token_expires.isoformat()}, f)

    # ─────────────────────────────────────────
    # 1. 토큰 발급
    # ─────────────────────────────────────────
    def get_token(self):
        """OAuth 2.0 접근 토큰 발급"""
        url = f"{self.base_url}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": APP_KEY,
            "appsecret": APP_SECRET
        }
        res = requests.post(url, json=body)
        data = res.json()
        if "access_token" in data:
            self.access_token = data["access_token"]
            self.token_expires = datetime.now() + timedelta(hours=23)
            self._save_token()
            print(f"✅ 토큰 발급 성공 (만료: {self.token_expires.strftime('%H:%M')})")
            return True
        else:
            print(f"❌ 토큰 발급 실패: {data}")
            return False

    def _ensure_token(self):
        """토큰 유효성 확인 및 자동 갱신"""
        if not self.access_token or datetime.now() >= self.token_expires:
            self.get_token()

    def _headers(self, tr_id):
        """공통 헤더 생성"""
        self._ensure_token()
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.access_token}",
            "appkey": APP_KEY,
            "appsecret": APP_SECRET,
            "tr_id": tr_id,
            "custtype": "P",
        }

    # ─────────────────────────────────────────
    # 2. 시세 조회
    # ─────────────────────────────────────────
    def get_price(self, ticker):
        """주식/ETF 현재가 조회"""
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker}
        res = requests.get(url, headers=self._headers("FHKST01010100"), params=params)
        data = res.json()
        if data.get("rt_cd") == "0":
            o = data["output"]
            return {
                "ticker": ticker,
                "price": int(o["stck_prpr"]),         # 현재가
                "change": int(o["prdy_vrss"]),         # 전일대비
                "change_pct": float(o["prdy_ctrt"]),   # 등락률
                "volume": int(o["acml_vol"]),           # 누적거래량
                "open": int(o["stck_oprc"]),            # 시가
                "high": int(o["stck_hgpr"]),            # 고가
                "low": int(o["stck_lwpr"]),             # 저가
            }
        else:
            print(f"❌ 시세조회 실패 [{ticker}]: {data.get('msg1')}")
            return None

    def get_prices_bulk(self, tickers):
        """여러 종목 시세 한 번에 조회"""
        results = {}
        for t in tickers:
            price = self.get_price(t)
            if price:
                results[t] = price
            time.sleep(0.25)  # API 호출 간격 준수 (초당 4회 이하)
        return results

    def get_daily_chart(self, ticker, days=60):
        """일별 시세 조회 (전략 분석용)"""
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-price"
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker,
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "0",
        }
        res = requests.get(url, headers=self._headers("FHKST01010400"), params=params)
        data = res.json()
        if data.get("rt_cd") == "0":
            rows = []
            for row in data.get("output", [])[:days]:
                rows.append({
                    "date": row["stck_bsop_date"],
                    "close": int(row["stck_clpr"]),
                    "high":  int(row["stck_hgpr"]),
                    "low":   int(row["stck_lwpr"]),
                    "open":  int(row["stck_oprc"]),
                    "volume": int(row["acml_vol"]),
                })
            return rows
        return []

    # ─────────────────────────────────────────
    # 3. 모의투자 주문
    # ─────────────────────────────────────────
    def order_buy(self, ticker, qty, price=0):
        """매수 주문 (price=0 이면 시장가)"""
        return self._order(ticker, qty, price, "BUY")

    def order_sell(self, ticker, qty, price=0):
        """매도 주문 (price=0 이면 시장가)"""
        return self._order(ticker, qty, price, "SELL")

    def _order(self, ticker, qty, price, side):
        if self.account_no == "XXXXXXXX-XX":
            print("⚠️  config.py 에서 MOCK_ACCOUNT 를 먼저 설정해주세요!")
            return None

        # 모의투자 tr_id
        tr_id = "VTTC0802U" if side == "BUY" else "VTTC0801U"
        ord_dvsn = "01" if price == 0 else "00"  # 01=시장가, 00=지정가

        acc = self.account_no.replace("-", "")
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        body = {
            "CANO": acc[:8],
            "ACNT_PRDT_CD": acc[8:],
            "PDNO": ticker,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(qty),
            "ORD_UNPR": str(price),
        }
        res = requests.post(url, headers=self._headers(tr_id), json=body)
        data = res.json()
        if data.get("rt_cd") == "0":
            order_no = data["output"]["ODNO"]
            print(f"✅ {'매수' if side=='BUY' else '매도'} 주문 성공 | {ticker} {qty}주 | 주문번호: {order_no}")
            return order_no
        else:
            print(f"❌ 주문 실패: {data.get('msg1')}")
            return None

    # ─────────────────────────────────────────
    # 4. 잔고 조회
    # ─────────────────────────────────────────
    def get_balance(self):
        """모의투자 잔고 조회"""
        if self.account_no == "XXXXXXXX-XX":
            print("⚠️  config.py 에서 MOCK_ACCOUNT 를 먼저 설정해주세요!")
            return None

        acc = self.account_no.replace("-", "")
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
        params = {
            "CANO": acc[:8],
            "ACNT_PRDT_CD": acc[8:],
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        res = requests.get(url, headers=self._headers("VTTC8434R"), params=params)
        data = res.json()
        if data.get("rt_cd") == "0":
            holdings = []
            for item in data.get("output1", []):
                if int(item.get("hldg_qty", 0)) > 0:
                    holdings.append({
                        "ticker": item["pdno"],
                        "name": item["prdt_name"],
                        "qty": int(item["hldg_qty"]),
                        "avg_price": int(float(item["pchs_avg_pric"])),
                        "cur_price": int(item["prpr"]),
                        "eval_amt": int(item["evlu_amt"]),
                        "profit": int(item["evlu_pfls_amt"]),
                        "profit_pct": float(item["evlu_pfls_rt"]),
                    })
            summary = data.get("output2", [{}])[0]
            return {
                "holdings": holdings,
                "total_eval": int(summary.get("tot_evlu_amt", 0)),
                "total_profit": int(summary.get("evlu_pfls_smtl_amt", 0)),
                "cash": int(summary.get("dnca_tot_amt", 0)),
            }
        else:
            print(f"❌ 잔고조회 실패: {data.get('msg1')}")
            return None
