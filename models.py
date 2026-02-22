from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from database import Base
from datetime import datetime

class Stock(Base):
    __tablename__ = "stocks"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    ticker = Column(String)    # 추가
    currency = Column(String)  # 추가
    user_id = Column(String, index=True)

    transactions = relationship("Transaction", back_populates="stock", cascade="all, delete-orphan")

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=True)
    type = Column(String(20))   # BUY, SELL, DEPOSIT, WITHDRAW
    price = Column(Float)
    quantity = Column(Integer, default=1)
    date = Column(DateTime, default=datetime.now)
    
    stock = relationship("Stock", back_populates="transactions")