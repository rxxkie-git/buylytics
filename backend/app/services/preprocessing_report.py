"""Logging and reporting utilities for the preprocessing pipeline."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PreprocessingReport:
    """Structured report of preprocessing operations."""

    filename: str = ""
    original_shape: tuple[int, int] = (0, 0)
    final_shape: tuple[int, int] = (0, 0)
    processing_timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )

    duplicate_rows_removed: int = 0
    total_missing_values: int = 0
    missing_values_by_column: dict[str, int] = field(default_factory=dict)
    missing_values_filled_count: int = 0
    missing_fill_strategy: dict[str, str] = field(default_factory=dict)

    numeric_columns: list[str] = field(default_factory=list)
    categorical_columns: list[str] = field(default_factory=list)
    datetime_columns: list[str] = field(default_factory=list)
    columns_with_inconsistent_types: list[str] = field(default_factory=list)

    encoded_columns: dict[str, str] = field(default_factory=dict)
    binary_categorical_columns: list[str] = field(default_factory=list)
    one_hot_encoded_columns: dict[str, list[str]] = field(default_factory=dict)
    label_encoded_columns: list[str] = field(default_factory=list)

    scaled_columns: list[str] = field(default_factory=list)
    scaling_method: str = ""
    scaling_statistics: dict[str, dict[str, float]] = field(default_factory=dict)

    engineered_features: list[str] = field(default_factory=list)
    derived_features_count: int = 0
    datetime_features_count: int = 0

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dictionary representation."""
        return asdict(self)

    def get_summary(self) -> str:
        """Generate a compact human-readable summary."""
        lines: list[str] = []
        lines.append("=" * 72)
        lines.append("DATA PREPROCESSING REPORT")
        lines.append("=" * 72)
        lines.append(f"File: {self.filename}")
        lines.append(f"Processing Time: {self.processing_timestamp}")
        lines.append(f"Original Shape: {self.original_shape[0]} rows x {self.original_shape[1]} columns")
        lines.append(f"Final Shape: {self.final_shape[0]} rows x {self.final_shape[1]} columns")
        lines.append("-" * 72)
        lines.append("CLEANING")
        lines.append(f"Duplicates Removed: {self.duplicate_rows_removed}")
        lines.append(f"Missing Values Detected: {self.total_missing_values}")
        lines.append(f"Missing Values Filled: {self.missing_values_filled_count}")

        if self.missing_values_by_column:
            lines.append("Missing by Column:")
            for column, count in sorted(
                self.missing_values_by_column.items(),
                key=lambda item: item[1],
                reverse=True,
            ):
                lines.append(f"- {column}: {count}")

        if self.missing_fill_strategy:
            lines.append("Fill Strategies:")
            for column, strategy in self.missing_fill_strategy.items():
                lines.append(f"- {column}: {strategy}")

        lines.append("-" * 72)
        lines.append("ENCODING + SCALING")
        lines.append(f"Encoded Columns: {len(self.encoded_columns)}")
        lines.append(f"Label Encoded: {len(self.label_encoded_columns)}")
        lines.append(f"One-Hot Encoded: {len(self.one_hot_encoded_columns)}")
        lines.append(f"Scaling Method: {self.scaling_method or 'none'}")
        lines.append(f"Scaled Columns: {len(self.scaled_columns)}")

        lines.append("-" * 72)
        lines.append("FEATURE ENGINEERING")
        lines.append(f"Engineered Features: {len(self.engineered_features)}")
        lines.append(f"Derived Features: {self.derived_features_count}")
        lines.append(f"Datetime Features: {self.datetime_features_count}")
        if self.engineered_features:
            lines.append(f"Feature Names: {', '.join(self.engineered_features)}")

        if self.warnings:
            lines.append("-" * 72)
            lines.append("WARNINGS")
            for warning in self.warnings:
                lines.append(f"- {warning}")

        if self.errors:
            lines.append("-" * 72)
            lines.append("ERRORS")
            for error in self.errors:
                lines.append(f"- {error}")

        lines.append("=" * 72)
        return "\n".join(lines)

    def log_summary(self, level: int = logging.INFO) -> None:
        """Log report summary."""
        logger.log(level, self.get_summary())
