"""Configuration and enums for the preprocessing pipeline."""

from enum import Enum
from typing import Optional


class ScalingMethod(Enum):
    """Available scaling methods for feature normalization."""
    STANDARD = "standard"  # StandardScaler: (x - mean) / std
    MINMAX = "minmax"      # MinMaxScaler: (x - min) / (max - min)
    ROBUST = "robust"      # RobustScaler: (x - median) / IQR
    NONE = "none"          # No scaling


class EncodingStrategy(Enum):
    """Categorical encoding strategies."""
    ONE_HOT = "one_hot"     # One-Hot Encoding (default)
    LABEL = "label"         # Label Encoding
    ORDINAL = "ordinal"     # Ordinal Encoding


class PipelineConfig:
    """Configuration for the preprocessing pipeline."""

    def __init__(
        self,
        scaling_method: ScalingMethod = ScalingMethod.STANDARD,
        encoding_strategy: EncodingStrategy = EncodingStrategy.ONE_HOT,
        binary_encoding_strategy: Optional[EncodingStrategy] = EncodingStrategy.LABEL,
        handle_duplicates: bool = True,
        handle_missing_values: bool = True,
        engineer_features: bool = True,
        drop_original_columns: bool = False,
        min_category_threshold: int = 2,
        datetime_feature_extraction: bool = True,
    ):
        """
        Initialize pipeline configuration.

        Args:
            scaling_method: Method to use for feature scaling
            encoding_strategy: Strategy for encoding categorical features
            binary_encoding_strategy: Strategy for binary categorical features
            handle_duplicates: Whether to detect and remove duplicate rows
            handle_missing_values: Whether to fill missing values
            engineer_features: Whether to create derived features
            drop_original_columns: Whether to drop original columns after encoding
            min_category_threshold: Minimum unique values to treat as categorical
            datetime_feature_extraction: Whether to extract features from datetime columns
        """
        self.scaling_method = scaling_method
        self.encoding_strategy = encoding_strategy
        self.binary_encoding_strategy = binary_encoding_strategy
        self.handle_duplicates = handle_duplicates
        self.handle_missing_values = handle_missing_values
        self.engineer_features = engineer_features
        self.drop_original_columns = drop_original_columns
        self.min_category_threshold = min_category_threshold
        self.datetime_feature_extraction = datetime_feature_extraction

    def to_dict(self) -> dict:
        """Convert configuration to dictionary."""
        return {
            "scaling_method": self.scaling_method.value,
            "encoding_strategy": self.encoding_strategy.value,
            "binary_encoding_strategy": self.binary_encoding_strategy.value if self.binary_encoding_strategy else None,
            "handle_duplicates": self.handle_duplicates,
            "handle_missing_values": self.handle_missing_values,
            "engineer_features": self.engineer_features,
            "drop_original_columns": self.drop_original_columns,
            "min_category_threshold": self.min_category_threshold,
            "datetime_feature_extraction": self.datetime_feature_extraction,
        }

    @classmethod
    def from_dict(cls, config_dict: dict) -> "PipelineConfig":
        """Create configuration from dictionary."""
        return cls(
            scaling_method=ScalingMethod(config_dict.get("scaling_method", "standard")),
            encoding_strategy=EncodingStrategy(config_dict.get("encoding_strategy", "one_hot")),
            binary_encoding_strategy=(
                EncodingStrategy(config_dict["binary_encoding_strategy"])
                if config_dict.get("binary_encoding_strategy")
                else None
            ),
            handle_duplicates=config_dict.get("handle_duplicates", True),
            handle_missing_values=config_dict.get("handle_missing_values", True),
            engineer_features=config_dict.get("engineer_features", True),
            drop_original_columns=config_dict.get("drop_original_columns", False),
            min_category_threshold=config_dict.get("min_category_threshold", 2),
            datetime_feature_extraction=config_dict.get("datetime_feature_extraction", True),
        )
