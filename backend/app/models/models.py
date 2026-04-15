"""SQLAlchemy ORM models for Rubin Scout."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Object(Base):
    """An astronomical source tracked across multiple detections."""
    __tablename__ = "objects"

    oid = Column(String, primary_key=True)
    ra = Column(Float, nullable=False)
    dec = Column(Float, nullable=False)
    first_detection = Column(DateTime(timezone=True))
    last_detection = Column(DateTime(timezone=True))
    n_detections = Column(Integer, default=0)
    classification = Column(String, index=True)
    classification_probability = Column(Float)
    sub_classification = Column(String)
    classifier_name = Column(String)
    classifier_version = Column(String)
    cross_match_catalog = Column(String)
    cross_match_name = Column(String)
    cross_match_type = Column(String)
    cross_match_distance_arcsec = Column(Float)
    host_galaxy_name = Column(String)
    host_galaxy_redshift = Column(Float)
    broker_source = Column(String, default="tns")
    alert_url = Column(String)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    detections = relationship("Detection", back_populates="object", cascade="all, delete-orphan")
    probabilities = relationship("ClassificationProbability", back_populates="object", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "oid": self.oid,
            "ra": self.ra,
            "dec": self.dec,
            "first_detection": self.first_detection.isoformat() if self.first_detection else None,
            "last_detection": self.last_detection.isoformat() if self.last_detection else None,
            "n_detections": self.n_detections,
            "classification": self.classification,
            "classification_probability": self.classification_probability,
            "sub_classification": self.sub_classification,
            "cross_match_name": self.cross_match_name,
            "cross_match_type": self.cross_match_type,
            "host_galaxy_name": self.host_galaxy_name,
            "host_galaxy_redshift": self.host_galaxy_redshift,
            "broker_source": self.broker_source,
            "alert_url": self.alert_url,
        }


class Detection(Base):
    """A single brightness measurement of an object at a point in time."""
    __tablename__ = "detections"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    oid = Column(String, ForeignKey("objects.oid", ondelete="CASCADE"), nullable=False)
    candid = Column(BigInteger)
    mjd = Column(Float, nullable=False)
    detection_time = Column(DateTime(timezone=True), nullable=False)
    fid = Column(Integer)
    band = Column(String)
    magpsf = Column(Float)
    sigmapsf = Column(Float)
    ra = Column(Float)
    dec = Column(Float)
    isdiffpos = Column(String)
    rb = Column(Float)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    object = relationship("Object", back_populates="detections")

    def to_dict(self):
        return {
            "mjd": self.mjd,
            "detection_time": self.detection_time.isoformat() if self.detection_time else None,
            "band": self.band,
            "fid": self.fid,
            "magpsf": self.magpsf,
            "sigmapsf": self.sigmapsf,
            "ra": self.ra,
            "dec": self.dec,
        }


class ClassificationProbability(Base):
    """Classification probability for a given object from a specific classifier."""
    __tablename__ = "classification_probabilities"

    id = Column(Integer, primary_key=True, autoincrement=True)
    oid = Column(String, ForeignKey("objects.oid", ondelete="CASCADE"), nullable=False)
    classifier_name = Column(String, nullable=False)
    classifier_version = Column(String)
    class_name = Column(String, nullable=False)
    probability = Column(Float, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    object = relationship("Object", back_populates="probabilities")

    __table_args__ = (
        UniqueConstraint("oid", "classifier_name", "class_name"),
    )


class Subscription(Base):
    """User notification subscription with filter criteria."""
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    user_email = Column(String, nullable=False)
    filter_config = Column(JSONB, default={})
    notification_method = Column(String, default="email")
    webhook_url = Column(String)
    slack_channel = Column(String)
    active = Column(Boolean, default=True)
    last_notified_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


class GWEvent(Base):
    """A gravitational wave event from LIGO/Virgo/KAGRA."""
    __tablename__ = "gw_events"

    superevent_id = Column(String, primary_key=True)
    event_time = Column(DateTime(timezone=True))
    far = Column(Float)
    skymap_url = Column(String)
    classification = Column(JSONB)
    properties = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    candidates = relationship("GWCandidate", back_populates="event", cascade="all, delete-orphan")


class GWCandidate(Base):
    """An optical counterpart candidate for a GW event."""
    __tablename__ = "gw_candidates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    superevent_id = Column(String, ForeignKey("gw_events.superevent_id"), nullable=False)
    oid = Column(String, ForeignKey("objects.oid"), nullable=False)
    probability_in_skymap = Column(Float)
    distance_to_peak_arcsec = Column(Float)
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    event = relationship("GWEvent", back_populates="candidates")

    __table_args__ = (
        UniqueConstraint("superevent_id", "oid"),
    )


class IngestionLog(Base):
    """Track ingestion runs for monitoring and deduplication."""
    __tablename__ = "ingestion_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String, nullable=False)
    query_params = Column(JSONB)
    objects_ingested = Column(Integer, default=0)
    started_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    completed_at = Column(DateTime(timezone=True))
    status = Column(String, default="running")
    error_message = Column(Text)
