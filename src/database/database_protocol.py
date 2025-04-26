from abc import ABC, abstractmethod
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv, find_dotenv


Base = declarative_base()

class DatabaseProtocol(ABC):
    
    @abstractmethod
    def get_engine(self):
        pass

    @abstractmethod
    def get_session(self):
        pass

    @abstractmethod
    def create_tables(self, Base):
        pass


class SQLiteDatabase(DatabaseProtocol):
    
    def __init__(self, db_url="sqlite:///./database.db"):
        self.db_url = db_url
        self.engine = create_engine(self.db_url, connect_args={"check_same_thread": False})
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def get_engine(self):
        return self.engine

    def get_session(self):
        return self.SessionLocal()

    def create_tables(self, Base):
        Base.metadata.create_all(bind=self.engine)


def get_database():
    return SQLiteDatabase()