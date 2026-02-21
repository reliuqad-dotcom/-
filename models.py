from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from database import Base
from datetime import datetime


class Stock(Base):
    __tablename__ = "stocks"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True)

    transactions = relationship("Transaction", back_populates="stock")


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"))
    type = Column(String)  # BUY or SELL
    price = Column(Float)
    quantity = Column(Integer)
    date = Column(DateTime, default=datetime.utcnow)

    stock = relationship("Stock", back_populates="transactions")