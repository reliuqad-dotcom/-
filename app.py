import uvicorn
import uuid
import requests  # [필수] 이 부분이 누락되면 500 에러가 발생합니다.
import yfinance as yf
import plotly.graph_objects as go
import pandas as pd
from datetime import datetime, timedelta, timezone, date
from fastapi import FastAPI, Request, Depends, Form, Query, Response
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from urllib.parse import unquote

# 사용자 정의 모듈 (파일이 동일 디렉토리에 있어야 함)
from database import SessionLocal, engine, Base
from models import Stock, Transaction

app = FastAPI(title="라고 할 때 살 걸")
templates = Jinja2Templates(directory="templates")
Base.metadata.create_all(bind=engine)

KST = timezone(timedelta(hours=9))

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_user_id(request: Request):
    """쿠키에서 ID를 읽어오고, 없으면 새로 생성만 합니다."""
    user_id = request.cookies.get("user_id")
    if not user_id:
        user_id = str(uuid.uuid4())
    return user_id

# 차트 및 마켓 정보 참조용 고정 데이터
ticker_map = {
    "삼성전자": {"ticker": "005930.KS", "currency": "KRW"},
    "애플": {"ticker": "AAPL", "currency": "USD"},
    "테슬라": {"ticker": "TSLA", "currency": "USD"},
    "알파벳A": {"ticker": "GOOGL", "currency": "USD"},
}

@app.get("/")
def dashboard(request: Request, response: Response, db: Session = Depends(get_db), target_date: str = Query(None)):
    # [수정] 인자에서 response 제거하고 내부에서 생성하도록 변경
    uid = get_user_id(request)
    if not uid:
        uid = str(uuid.uuid4())
    now_kst = datetime.now(KST)
    display_date = target_date if target_date else now_kst.strftime("%Y-%m-%d")

    # 1. 초기 종목 세팅 (DB 비어있을 시) - 기존 로직 유지
    stocks_in_db = db.query(Stock).filter(Stock.user_id == uid).all()
    if not stocks_in_db:
        for name, info in ticker_map.items():
            db.add(Stock(
                name=name, 
                user_id=uid, 
                ticker=info["ticker"], 
                currency=info["currency"]
            ))
        db.commit()
        stocks_in_db = db.query(Stock).filter(Stock.user_id == uid).all()

    current_user_ticker_map = {
        s.name: {"ticker": s.ticker, "currency": s.currency} 
        for s in stocks_in_db
    }

    user_stock_names = [s.name for s in stocks_in_db]
    market_info = {}
    usd_rate = 1380.0

    try:
        target_dt = datetime.strptime(display_date, "%Y-%m-%d")
        start_dt = target_dt - timedelta(days=15)
        end_dt = target_dt + timedelta(days=1)

        # 환율 및 마켓 데이터 수집 - 기존 로직 유지
        usd_data = yf.download("USDKRW=X", start=start_dt, end=end_dt, progress=False, threads=False)
        if not usd_data.empty:
            if isinstance(usd_data.columns, pd.MultiIndex): usd_data.columns = usd_data.columns.get_level_values(0)
            usd_rate = float(usd_data['Close'].dropna().iloc[-1])

        for name in user_stock_names:
            info = ticker_map.get(name)
            if not info: continue
            h = yf.download(info["ticker"], start=start_dt, end=end_dt, progress=False, threads=False)
            if not h.empty:
                if isinstance(h.columns, pd.MultiIndex): h.columns = h.columns.get_level_values(0)
                p = float(h['Close'].dropna().iloc[-1])
                market_info[name] = {
                    "price": p, "currency": info["currency"],
                    "currency_symbol": "$" if info["currency"] == "USD" else "￦"
                }
    except Exception as e:
        print(f"Market Data Fetch Error: {e}")

    # 자산 및 포트폴리오 로직 - 기존 로직 유지
    initial_cash = 10_000_000
    all_tx = db.query(Transaction).filter(Transaction.user_id == uid).all()
    cash_flow = initial_cash
    holdings = {s.name: 0 for s in stocks_in_db}

    for tx in all_tx:
        if tx.type == "DEPOSIT": cash_flow += tx.price
        elif tx.type == "WITHDRAW": cash_flow -= tx.price
        elif tx.type in ("BUY", "SELL"):
            stock_obj = db.query(Stock).filter(Stock.id == tx.stock_id).first()
            if not stock_obj: continue
            
            amt = tx.price * tx.quantity
            if stock_obj.name in ticker_map and ticker_map[stock_obj.name]["currency"] == "USD":
                amt *= usd_rate
            
            if tx.type == "BUY": cash_flow -= amt
            else: cash_flow += amt

    portfolio = []
    total_stock_value_krw = 0.0
    for s in stocks_in_db:
        txs = [t for t in all_tx if t.stock_id == s.id]
        b_qty = sum(t.quantity for t in txs if t.type == "BUY")
        s_qty = sum(t.quantity for t in txs if t.type == "SELL")
        qty = b_qty - s_qty
        holdings[s.name] = qty
        
        if qty > 0 and s.name in market_info:
            m = market_info[s.name]
            rate = usd_rate if m["currency"] == "USD" else 1.0
            buy_val = sum(t.price * t.quantity for t in txs if t.type == "BUY")
            avg_p = buy_val / b_qty if b_qty > 0 else 0
            cur_val_krw = m["price"] * qty * rate
            profit_krw = cur_val_krw - (avg_p * qty * rate)
            total_stock_value_krw += cur_val_krw
            profit_pct = (profit_krw / (avg_p * qty * rate) * 100) if (avg_p * qty * rate) > 0 else 0
            
            portfolio.append({
                "name": s.name, "quantity": qty,
                "current_price_str": f"{m['price']:,.2f}" if m["currency"] == "USD" else f"{m['price']:,.0f}",
                "currency_symbol": m["currency_symbol"],
                "profit_str": f"{profit_krw:+,.0f}원",
                "profit_pct_str": f"{profit_pct:+.2f}%"
            })

    total_asset = cash_flow + total_stock_value_krw

    response = templates.TemplateResponse("dashboard.html", {
        "request": request,
        "available_stocks": user_stock_names,
        "market_info": market_info,
        "portfolio": portfolio,
        "holdings": holdings,
        "total_asset": f"{total_asset:,.0f}",
        "cash": f"{cash_flow:,.0f}",
        "cash_raw": cash_flow,
        "usd_rate": round(usd_rate, 2),
        "target_date": display_date
    })

    # 쿠키 설정 (하나로 통합)
    response.set_cookie(
        key="user_id", 
        value=uid, 
        max_age=31536000, # 1년 유지
        httponly=True, 
        samesite="lax", 
        secure=True
    )
    return response

# --- 차트 로직 (사용자 기존 코드 100% 보존) ---
@app.get("/chart/{stock_name}/{period}")
def get_chart(stock_name: str, period: str, end_date: str = Query(None)):
    stock_name = unquote(stock_name)
    info = ticker_map.get(stock_name)
    if not info: return HTMLResponse(f"<div style='padding:20px;'>'{stock_name}' 정보 없음</div>")
    
    try:
        period_config = {
            "5d":  {"yf_period": "15d",  "interval": "15m", "days": 5}, 
            "1mo": {"yf_period": "max",  "interval": "1d",  "days": 30},
            "3mo": {"yf_period": "max",  "interval": "1d",  "days": 90},
            "1y":  {"yf_period": "max",  "interval": "1d",  "days": 365},
            "max": {"yf_period": "max",  "interval": "1d",  "days": 9999}
        }
        cfg = period_config.get(period, period_config["1mo"])
        end_dt_obj = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1) if end_date and end_date != "None" else None
        
        current_interval = cfg["interval"]
        if period == "5d":
            df = pd.DataFrame()
            for trial in ["15m", "30m", "1h", "1d"]:
                current_interval = trial
                df = yf.download(info["ticker"], start=(end_dt_obj - timedelta(days=10)) if end_dt_obj else None, end=end_dt_obj, interval=current_interval, progress=False, auto_adjust=True)
                if not df.empty: break
        else:
            df = yf.download(info["ticker"], period=None if end_dt_obj else cfg["yf_period"], start=(end_dt_obj - timedelta(days=cfg["days"]+40)) if end_dt_obj else None, end=end_dt_obj, interval=current_interval, progress=False, auto_adjust=True)

        if df.empty: return HTMLResponse("<div style='color:#64748b; padding:20px;'>데이터 없음</div>")
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        df.index = df.index.tz_convert('Asia/Seoul') if df.index.tz else df.index.tz_localize('UTC').tz_convert('Asia/Seoul')

        df['MA5'] = df['Close'].rolling(5).mean()
        df['MA20'] = df['Close'].rolling(20).mean()
        
        available_dates = sorted(list(set(df.index.date)))
        target_date = [d for d in available_dates if d <= (end_dt_obj.date() if end_dt_obj else available_dates[-1])][-1]
        idx = available_dates.index(target_date)
        df = df[pd.Series(df.index.date).isin(available_dates[max(0, idx - cfg["days"] + 1) : idx + 1]).values]

        fig = go.Figure()
        tick_vals, tick_text = None, None
        if period in ["5d", "1mo", "3mo", "1y"]:
            x_type = 'category'
            x_data = df.index.strftime('%m-%d %H:%M' if current_interval != "1d" else '%Y-%m-%d')
            step = {"5d": 12 if current_interval=="15m" else 6, "1mo": 5, "3mo": 10, "1y": 21}.get(period, 5)
            tick_vals = list(range(0, len(df), step))
            tick_text = [x_data[i] for i in tick_vals if i < len(x_data)]
        else:
            x_data = df.index; x_type = 'date'

        fig.add_trace(go.Candlestick(x=x_data, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], increasing_line_color='#ef4444', decreasing_line_color='#3b82f6', name="가격"))
        fig.add_trace(go.Scatter(x=x_data, y=df['MA5'], line=dict(width=1.2, color='#f59e0b'), name="5선"))
        fig.add_trace(go.Scatter(x=x_data, y=df['MA20'], line=dict(width=1.2, color='#10b981'), name="20선"))

        fig.update_layout(
            template='plotly_white',
            margin=dict(l=5, r=5, t=10, b=10), # 여백 극최소화 (모바일 공간 확보)
            hovermode='x unified',
            showlegend=True,
            
            # --- 범례: 차트 내부 좌측 상단에 콤팩트하게 배치 ---
            legend=dict(
                orientation="h",
                yanchor="top",
                y=0.98,
                xanchor="left",
                x=0.02,
                font=dict(size=10), # 글자 크기 줄임
                bgcolor="rgba(255, 255, 255, 0.6)"
            ),
            
            # --- X축: 카테고리 모드에서도 겹침 방지 ---
            xaxis=dict(
                type=x_type,
                showgrid=True,
                gridcolor='#f1f5f9',
                # 카테고리 모드일 때 눈금 강제 지정 (tick_vals가 정의된 경우)
                tickvals=tick_vals if x_type == 'category' else None,
                ticktext=tick_text if x_type == 'category' else None,
                tickfont=dict(size=10),
                automargin=True,
                rangeslider=dict(visible=False) # 캔들스틱 기본 하단 슬라이더 제거 (공간 확보)
            ),
            
            # --- Y축: 우측 배치로 차트 가독성 향상 ---
            yaxis=dict(
                showgrid=True, 
                gridcolor='#f1f5f9',
                side="right", 
                tickfont=dict(size=10),
                fixedrange=False # Y축 줌 가능하게 설정
            ),
            height=400, # 고정 높이 설정 (iframe 내부 최적화)
            autosize=True
        )
        return HTMLResponse(fig.to_html(full_html=False, include_plotlyjs='cdn'))
    except Exception as e:
        return HTMLResponse(f"차트 오류: {str(e)}")

# --- POST 액션 라우터 ---
@app.post("/add_transaction")
def add_tx(request: Request, name: str = Form(...), type: str = Form(...), price: float = Form(...), quantity: int = Form(...), db: Session = Depends(get_db)):
    uid = get_user_id(request)
    stock = db.query(Stock).filter(Stock.name == name, Stock.user_id == uid).first()
    if stock:
        db.add(Transaction(user_id=uid, stock_id=stock.id, type=type, price=price, quantity=quantity, date=datetime.now(KST)))
        db.commit()
    return RedirectResponse(url= "/", status_code=303)

@app.post("/add_stock")
def add_stock(request: Request, name: str = Form(...), ticker: str = Form(...), currency: str = Form(...), db: Session = Depends(get_db)):
    uid = get_user_id(request)
    if not uid: return RedirectResponse(url="/", status_code=303)

    # 사용자별로 중복 확인 후 상세 정보까지 저장
    existing = db.query(Stock).filter(Stock.name == name, Stock.user_id == uid).first()
    if not existing:
        new_stock = Stock(
            name=name, 
            ticker=ticker, 
            currency=currency, 
            user_id=uid
        )
        db.add(new_stock)
        db.commit()
    return RedirectResponse(url="/", status_code=303)

@app.post("/delete_stock/{name}")
def delete_stock(name: str, request: Request, db: Session = Depends(get_db)):
    uid = request.cookies.get("user_id")
    stock = db.query(Stock).filter(Stock.name == name, Stock.user_id == uid).first()
    if stock:
        db.query(Transaction).filter(Transaction.stock_id == stock.id).delete()
        db.delete(stock)
        db.commit()
    return RedirectResponse(url="/", status_code=303)

@app.post("/reset")
def reset_data(request: Request, db: Session = Depends(get_db)):
    uid = request.cookies.get("user_id")
    if uid:
        db.query(Transaction).filter(Transaction.user_id == uid).delete()
        db.query(Stock).filter(Stock.user_id == uid).delete()
        db.commit()
    return RedirectResponse(url="/", status_code=303)

@app.post("/cash_transaction")
def cash_tx(request: Request, amount: float = Form(...), type: str = Form(...), db: Session = Depends(get_db)):
    uid = request.cookies.get("user_id")
    db.add(Transaction(user_id=uid, type=type, price=amount, quantity=1, date=datetime.now(KST)))
    db.commit()
    return RedirectResponse(url="/", status_code=303)

@app.get("/search_ticker")
def search_ticker(q: str):
    try:
        res = requests.get(f"https://query2.finance.yahoo.com/v1/finance/search?q={q}", headers={'User-Agent': 'Mozilla/5.0'})
        return res.json().get("quotes", [])[:5]
    except Exception as e:
        print(f"Search Error: {e}")
        return []

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)