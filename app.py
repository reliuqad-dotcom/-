from fastapi import FastAPI, Request, Depends, Form, Query
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta, timezone
import yfinance as yf
import plotly.graph_objects as go
import pandas as pd
import requests # 맨 위에 import requests 추가하세요!

# ===== DB와 모델 import =====
from database import SessionLocal, engine, Base
from models import Stock, Transaction

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# 한국시간(KST)
KST = timezone(timedelta(hours=9))
Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# 1. 전역 변수 설정 (최상단 배치)
ticker_map = {
    "삼성전자": {"ticker": "005930.KS", "currency": "KRW"},
    "애플":      {"ticker": "AAPL",      "currency": "USD"},
    "테슬라":    {"ticker": "TSLA",      "currency": "USD"},
    "알파벳A":    {"ticker": "GOOGL",     "currency": "USD"},
    "SK하이닉스":{"ticker": "000660.KS", "currency": "KRW"},
}
available_stocks = list(ticker_map.keys())

# -------------------------
# 종목 추가 API
# -------------------------
@app.post("/add_stock")
def add_new_stock(
    name: str = Form(...), 
    ticker: str = Form(...), 
    currency: str = Form(...),
    db: Session = Depends(get_db)
):
    # 전역 변수 업데이트
    ticker_map[name] = {"ticker": ticker, "currency": currency}
    if name not in available_stocks:
        available_stocks.append(name)
    
    # DB 종목 확인 및 생성
    stock = db.query(Stock).filter(Stock.name == name).first()
    if not stock:
        new_stock = Stock(name=name)
        db.add(new_stock)
        db.commit()
        
    return RedirectResponse(url="/", status_code=303)

# -------------------------
# 자동완성
# -------------------------
@app.get("/search_ticker")
def search_ticker(q: str):
    if not q:
        return []
    
    # 1. 한국 종목 대응: 사용자가 한글로 검색할 경우를 대비해 
    # Yahoo Finance의 특정 지역(KR) 코드를 포함하여 검색 퀄리티를 높임
    # 또는 한글 이름을 영문으로 매핑하는 간단한 딕셔너리를 둘 수도 있습니다.
    
    # 한국 주식 전용 검색 최적화 (검색어 뒤에 자동 키워드 조합)
    search_query = q
    if any(ord('가') <= ord(char) <= ord('힣') for char in q):
        # 한글이 포함된 경우 검색 정확도를 높이기 위해 쿼리 조정 가능
        pass

    url = f"https://query1.finance.yahoo.com/v1/finance/search?q={search_query}&quotesCount=10&newsCount=0"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    try:
        response = requests.get(url, headers=headers)
        data = response.json()
        
        results = []
        for quote in data.get("quotes", []):
            # 우리가 필요한 정보 위주로 필터링 (주식 및 ETF만)
            if quote.get("quoteType") in ["EQUITY", "ETF"]:
                results.append({
                    "symbol": quote.get("symbol"),
                    "shortname": quote.get("shortname") or quote.get("longname") or quote.get("symbol"),
                    "exchange": quote.get("exchange"),
                    "type": quote.get("quoteType")
                })
        return results
    except Exception as e:
        print(f"Search Error: {e}")
        return []

# -------------------------
# 종목삭제
# -------------------------

@app.post("/delete_stock/{name}")
def delete_stock(name: str, db: Session = Depends(get_db)):
    # 1. 전역 변수에서 제거
    if name in ticker_map:
        del ticker_map[name]
    if name in available_stocks:
        available_stocks.remove(name)
    
    # 2. DB에서 해당 종목 삭제 (거래 내역이 있으면 주의 필요)
    # 여기서는 종목 정보만 삭제하고 거래 내역은 유지하거나, 깔끔하게 같이 지울 수 있습니다.
    stock = db.query(Stock).filter(Stock.name == name).first()
    if stock:
        # 연관된 거래 내역까지 삭제하려면 아래 주석 해제
        # db.query(Transaction).filter(Transaction.stock_id == stock.id).delete()
        db.delete(stock)
        db.commit()
        
    return RedirectResponse(url="/", status_code=303)

# -------------------------
# 거래 추가
# -------------------------
@app.post("/add_transaction")
def add_transaction(
    name: str = Form(...), type: str = Form(...),
    price: float = Form(...), quantity: int = Form(...),
    db: Session = Depends(get_db)
):
    stock = db.query(Stock).filter(Stock.name == name).first()
    if not stock:
        stock = Stock(name=name); db.add(stock); db.commit(); db.refresh(stock)

    transaction = Transaction(
        stock_id=stock.id, type=type, price=price,
        quantity=quantity, date=datetime.now(KST)
    )
    db.add(transaction); db.commit()
    return RedirectResponse(url="/", status_code=303)

# -------------------------
# 차트 API
# -------------------------
@app.get("/chart/{stock_name}/{period}")
def get_chart(stock_name: str, period: str, end_date: str = Query(None)):
    info = ticker_map.get(stock_name)
    if not info: return HTMLResponse("<h3>종목 정보 없음</h3>")

    end_dt = None
    if end_date and end_date not in ["None", "", "undefined"]:
        try:
            end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
        except: pass

    yf_period = "7d" if period == "1wk" else period
    interval = "5m" if period == "1d" else ("30m" if period == "1wk" else "1d")

    try:
        data = yf.download(info["ticker"], period=yf_period, interval=interval, end=end_dt, progress=False)
        if isinstance(data.columns, pd.MultiIndex): data.columns = data.columns.get_level_values(0)
    except: return HTMLResponse("<h3>데이터 로드 실패</h3>")

    if data.empty: return HTMLResponse("<h3>데이터가 없습니다.</h3>")

    fig = go.Figure(data=[go.Candlestick(
        x=data.index, open=data['Open'], high=data['High'], low=data['Low'], close=data['Close'],
        increasing_line_color='#22c55e', decreasing_line_color='#ef4444'
    )])
    fig.update_layout(height=580, template="plotly_white", xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=10, b=10))
    return HTMLResponse(content=fig.to_html(full_html=True, include_plotlyjs='cdn'))

# -------------------------
# 대시보드
# -------------------------
@app.get("/")
def dashboard(request: Request, db: Session = Depends(get_db), target_date: str = Query(None)):
    now_kst = datetime.now(KST)
    is_backtest = False
    display_date = now_kst.strftime("%Y-%m-%d")
    
    if target_date and target_date != now_kst.strftime("%Y-%m-%d"):
        is_backtest = True
        display_date = target_date

    market_info = {}
    usd_rate = 1380.0
    try:
        target_dt = datetime.strptime(display_date, "%Y-%m-%d")
        start_dt = target_dt - timedelta(days=10)
        end_dt = target_dt + timedelta(days=1)
        
        usd_data = yf.download("USDKRW=X", start=start_dt, end=end_dt, progress=False)
        if not usd_data.empty:
            if isinstance(usd_data.columns, pd.MultiIndex): usd_data.columns = usd_data.columns.get_level_values(0)
            usd_rate = float(usd_data['Close'].dropna().iloc[-1])

        for name, info in ticker_map.items():
            h = yf.download(info["ticker"], start=start_dt, end=end_dt, progress=False)
            if isinstance(h.columns, pd.MultiIndex): h.columns = h.columns.get_level_values(0)
            
            if not h.empty:
                p = float(h['Close'].dropna().iloc[-1])
                market_info[name] = {
                    "price": round(p, 2 if info["currency"] == "USD" else 0),
                    "currency": info["currency"],
                    "krw_price": p * usd_rate if info["currency"] == "USD" else p
                }
            else:
                market_info[name] = {"price": 0, "currency": info["currency"], "krw_price": 0}
    except Exception as e:
        print(f"Data Load Error: {e}")

    holdings = {name: 0 for name in available_stocks}
    portfolio = []
    total_stock_value_krw = 0.0
    
    stocks = db.query(Stock).all()
    for stock in stocks:
        b_qty = db.query(func.sum(Transaction.quantity)).filter(Transaction.stock_id == stock.id, Transaction.type == "BUY").scalar() or 0
        s_qty = db.query(func.sum(Transaction.quantity)).filter(Transaction.stock_id == stock.id, Transaction.type == "SELL").scalar() or 0
        qty = b_qty - s_qty
        
        if stock.name in holdings:
            holdings[stock.name] = qty
        
        if qty > 0:
            b_sum = db.query(func.sum(Transaction.price * Transaction.quantity)).filter(Transaction.stock_id == stock.id, Transaction.type == "BUY").scalar() or 0
            avg_p = b_sum / b_qty
            m_info = market_info.get(stock.name, {"krw_price": 0, "currency": "KRW", "price": 0})
            current_eval_krw = qty * m_info["krw_price"]
            cost_basis_krw = (avg_p * qty) * (usd_rate if m_info["currency"] == "USD" else 1)
            profit_krw = current_eval_krw - cost_basis_krw
            total_stock_value_krw += current_eval_krw
            
            portfolio.append({
                "name": stock.name,
                "quantity": qty,
                "profit_krw": profit_krw,
                "p_str": "{:,.0f}".format(profit_krw)
            })

    t_buy = db.query(func.sum(Transaction.price * Transaction.quantity)).filter(Transaction.type == "BUY").scalar() or 0
    t_sell = db.query(func.sum(Transaction.price * Transaction.quantity)).filter(Transaction.type == "SELL").scalar() or 0
    cash = 10_000_000 - t_buy + t_sell
    total_asset_krw = cash + total_stock_value_krw

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "available_stocks": available_stocks,
        "portfolio": portfolio,
        "total_asset": "{:,.0f}".format(total_asset_krw),
        "total_asset_raw": total_asset_krw,
        "cash_raw": cash,
        "cash": "{:,.0f}".format(cash),
        "total_value": "{:,.0f}".format(total_stock_value_krw),
        "is_backtest": is_backtest,
        "target_date": display_date,
        "market_info": market_info,
        "usd_rate": round(usd_rate, 2),
        "holdings": holdings,
        "last_updated": display_date
    })

@app.post("/reset")
def reset_data(db: Session = Depends(get_db)):
    db.query(Transaction).delete()
    db.query(Stock).delete()
    db.commit()
    return RedirectResponse(url="/", status_code=303)

import os

if __name__ == "__main__":
    # 환경 변수에서 포트를 가져오고, 없으면 5000번을 사용합니다.
    port = int(os.environ.get("PORT", 5000))
    # host를 0.0.0.0으로 설정해야 외부에서 접속이 가능합니다.
    app.run(host="0.0.0.0", port=port)