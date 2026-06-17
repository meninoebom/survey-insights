"""SQLAlchemy 2.0 models for the two tables.

`responses` is the cleaned detail grain (the rebuildable source of truth).
`distributions` is the precomputed long-format speed layer: one row per
(measure, dimension, group_value, response_value).
"""

from sqlalchemy import Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class Response(Base):
    """One cleaned respondent row. The source of truth `distributions` rebuilds from."""

    __tablename__ = "responses"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    age: Mapped[int]
    age_bucket: Mapped[str]
    gender: Mapped[str]
    state: Mapped[str]
    city: Mapped[str]
    zip_code: Mapped[str]  # text: preserves leading zeros (e.g. 04225)
    income: Mapped[str]
    education_level: Mapped[str]
    q1_rating: Mapped[int]
    q2_rating: Mapped[int]
    q4_rating: Mapped[int]
    sentiment_label: Mapped[str]


class Distribution(Base):
    """One precomputed count for a (measure, dimension, group_value, response_value).

    `response_value` is text so one table holds both rating values (1-5) and
    sentiment labels; it is cast to int in the read query for ratings.
    """

    __tablename__ = "distributions"

    id: Mapped[int] = mapped_column(primary_key=True)
    measure: Mapped[str]
    dimension: Mapped[str]  # a dimension name, or the OVERALL_DIMENSION sentinel
    group_value: Mapped[str]  # a value within the dimension, or OVERALL_GROUP_VALUE
    response_value: Mapped[str]
    count: Mapped[int]

    __table_args__ = (
        Index(
            "ix_distributions_measure_dimension_group",
            "measure",
            "dimension",
            "group_value",
        ),
    )
