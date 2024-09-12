from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import datetime

DATABASE_URL = 'sqlite:///vpn_users.db'

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()
Base = declarative_base()

class VPNUser(Base):
    __tablename__ = 'vpn_users'
    
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True)
    username = Column(String)
    subscription_end = Column(DateTime)
    access_key = Column(String)

Base.metadata.create_all(engine)

def add_user(telegram_id, username, access_key):
    user = VPNUser(telegram_id=telegram_id, username=username, 
                   subscription_end=datetime.datetime.now() + datetime.timedelta(days=30),
                   access_key=access_key)
    session.add(user)
    session.commit()

def get_user(telegram_id):
    return session.query(VPNUser).filter(VPNUser.telegram_id == telegram_id).first()

def update_subscription(telegram_id, days):
    user = get_user(telegram_id)
    if user:
        user.subscription_end += datetime.timedelta(days=days)
        session.commit()
