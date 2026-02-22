# database.py
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import urllib.parse
from sqlalchemy import create_engine

# 괄호 [ ] 빼고 비밀번호만 넣으세요!
SQLALCHEMY_DATABASE_URL = "postgresql+pg8000://postgres.iubmxvbggansukndtsep:rorkxspdlrjtodrkrqhek!!!@aws-1-ap-southeast-1.pooler.supabase.com:5432/postgres"

engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()