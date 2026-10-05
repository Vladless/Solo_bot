from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from ._base import Base, DictLikeMixin


class Server(DictLikeMixin, Base):
    __tablename__ = "servers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cluster_name = Column(String)
    server_name = Column(String, unique=True)
    api_url = Column(String)
    subscription_url = Column(String)
    inbound_id = Column(String)
    panel_type = Column(String)
    max_keys = Column(Integer)
    tariff_group = Column(String)
    enabled = Column(Boolean, default=True)

    subgroups = relationship("ServerSubgroup", back_populates="server", cascade="all, delete-orphan")
    groups = relationship("ServerSpecialgroup", back_populates="server", cascade="all, delete-orphan")


class ServerSubgroup(DictLikeMixin, Base):
    __tablename__ = "server_subgroups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    server_id = Column(Integer, ForeignKey("servers.id", ondelete="CASCADE"), index=True, nullable=False)
    group_code = Column(String, nullable=False)
    subgroup_title = Column(String, nullable=False)

    server = relationship("Server", back_populates="subgroups")

    __table_args__ = (UniqueConstraint("server_id", "subgroup_title", name="uq_server_subgroup"),)


class ServerSpecialgroup(DictLikeMixin, Base):
    __tablename__ = "server_specialgroups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    server_id = Column(Integer, ForeignKey("servers.id", ondelete="CASCADE"), index=True, nullable=False)
    group_code = Column(String, nullable=False)

    server = relationship("Server")

    __table_args__ = (UniqueConstraint("server_id", "group_code", name="uq_server_group"),)
