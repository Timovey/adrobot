from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CampaignLocal(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    keitaro_campaign_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    alias: Mapped[str | None] = mapped_column(String(255), nullable=True)
    geo: Mapped[str | None] = mapped_column(String(8), nullable=True)
    domain_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    group_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    traffic_source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    streams: Mapped[list["StreamLocal"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )


class StreamLocal(Base):
    __tablename__ = "streams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    keitaro_stream_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    stream_type: Mapped[str] = mapped_column(String(32), default="regular")
    stream_schema: Mapped[str] = mapped_column(String(32), default="landings")
    has_offers: Mapped[bool] = mapped_column(Boolean, default=False)
    # True only while the user has local edits (add/remove/bring-back/pin) not yet pushed.
    is_dirty: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    campaign: Mapped[CampaignLocal] = relationship(back_populates="streams")
    offers: Mapped[list["StreamOfferLocal"]] = relationship(
        back_populates="stream", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("campaign_id", "keitaro_stream_id", name="uq_campaign_stream"),)


class StreamOfferLocal(Base):
    """Local mirror of stream offers with draft / archive / pin support."""

    __tablename__ = "stream_offers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stream_id: Mapped[int] = mapped_column(ForeignKey("streams.id"), index=True)
    keitaro_offer_id: Mapped[int] = mapped_column(Integer, index=True)
    offer_name: Mapped[str] = mapped_column(String(512), default="")
    share: Mapped[float] = mapped_column(Float, default=0.0)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    # active | removed (local archive / removed in Keitaro)
    status: Mapped[str] = mapped_column(String(32), default="active")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    stream: Mapped[StreamLocal] = relationship(back_populates="offers")

    __table_args__ = (
        UniqueConstraint("stream_id", "keitaro_offer_id", name="uq_stream_offer"),
    )
