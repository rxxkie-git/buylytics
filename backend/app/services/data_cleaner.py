"""Data cleaning module for handling duplicates and missing values."""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class DataCleaner:
    """Handles data cleaning operations: duplicate removal and missing value imputation."""

    def __init__(self, verbose: bool = True):
        """
        Initialize DataCleaner.

        Args:
            verbose: Whether to log detailed cleaning information
        """
        self.verbose = verbose
        self.cleaning_report = {}

    def remove_duplicates(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Detect and remove duplicate rows.

        Args:
            df: Input dataframe

        Returns:
            DataFrame with duplicates removed
        """
        initial_rows = len(df)
        df_cleaned = df.drop_duplicates().reset_index(drop=True)
        duplicates_removed = initial_rows - len(df_cleaned)

        self.cleaning_report["duplicates_removed"] = duplicates_removed

        if self.verbose and duplicates_removed > 0:
            logger.info(f"Removed {duplicates_removed} duplicate rows")

        return df_cleaned

    def detect_missing_values(self, df: pd.DataFrame) -> dict[str, int]:
        """
        Detect missing values in each column.

        Args:
            df: Input dataframe

        Returns:
            Dictionary with column names as keys and missing value counts as values
        """
        missing_values = df.isnull().sum()
        missing_dict = missing_values[missing_values > 0].to_dict()

        self.cleaning_report["missing_values_per_column"] = missing_dict
        self.cleaning_report["total_missing_values"] = missing_values.sum()

        if self.verbose and missing_dict:
            logger.info(f"Detected missing values: {missing_dict}")

        return missing_dict

    def fill_missing_values(
        self,
        df: pd.DataFrame,
        numeric_strategy: str = "mean",
        categorical_strategy: str = "mode",
    ) -> pd.DataFrame:
        """
        Fill missing values using specified strategies.

        Args:
            df: Input dataframe
            numeric_strategy: Strategy for numeric columns ('mean', 'median', 'forward_fill')
            categorical_strategy: Strategy for categorical columns ('mode', 'forward_fill', 'drop')

        Returns:
            DataFrame with missing values filled
        """
        df_filled = df.copy()

        # Identify column types
        numeric_cols = df_filled.select_dtypes(include=[np.number]).columns
        categorical_cols = df_filled.select_dtypes(include=["object", "category"]).columns
        datetime_cols = df_filled.select_dtypes(include=["datetime64"]).columns

        filled_columns = []

        # Fill numeric columns
        for col in numeric_cols:
            if df_filled[col].isnull().any():
                if numeric_strategy == "mean":
                    fill_value = df_filled[col].mean()
                    df_filled[col].fillna(fill_value, inplace=True)
                elif numeric_strategy == "median":
                    fill_value = df_filled[col].median()
                    df_filled[col].fillna(fill_value, inplace=True)
                elif numeric_strategy == "forward_fill":
                    df_filled[col].fillna(method="ffill", inplace=True)
                    df_filled[col].fillna(method="bfill", inplace=True)

                filled_columns.append(f"{col} ({numeric_strategy})")

        # Fill categorical columns
        for col in categorical_cols:
            if df_filled[col].isnull().any():
                if categorical_strategy == "mode":
                    mode_value = df_filled[col].mode()
                    if len(mode_value) > 0:
                        df_filled[col].fillna(mode_value[0], inplace=True)
                    else:
                        df_filled[col].fillna("Unknown", inplace=True)
                elif categorical_strategy == "forward_fill":
                    df_filled[col].fillna(method="ffill", inplace=True)
                    df_filled[col].fillna(method="bfill", inplace=True)
                elif categorical_strategy == "drop":
                    df_filled = df_filled.dropna(subset=[col])

                filled_columns.append(f"{col} ({categorical_strategy})")

        # Fill datetime columns by forward/backward fill
        for col in datetime_cols:
            if df_filled[col].isnull().any():
                df_filled[col].fillna(method="ffill", inplace=True)
                df_filled[col].fillna(method="bfill", inplace=True)
                filled_columns.append(f"{col} (ffill/bfill)")

        self.cleaning_report["columns_filled"] = filled_columns

        if self.verbose and filled_columns:
            logger.info(f"Filled missing values in columns: {filled_columns}")

        return df_filled.reset_index(drop=True)

    def handle_inconsistent_dtypes(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Handle inconsistent data types by attempting conversion.

        Args:
            df: Input dataframe

        Returns:
            DataFrame with corrected data types
        """
        df_corrected = df.copy()
        corrections = []

        for col in df_corrected.columns:
            original_dtype = df_corrected[col].dtype

            # Try to detect and convert to numeric if possible
            if original_dtype == "object":
                try:
                    # Try numeric conversion
                    converted = pd.to_numeric(df_corrected[col], errors="coerce")
                    if converted.notna().sum() / len(df_corrected) > 0.9:  # 90% success rate
                        df_corrected[col] = converted
                        corrections.append(f"{col}: object -> numeric")
                        continue
                except Exception:
                    pass

                # Try datetime conversion
                try:
                    converted = pd.to_datetime(df_corrected[col], errors="coerce")
                    if converted.notna().sum() / len(df_corrected) > 0.9:  # 90% success rate
                        df_corrected[col] = converted
                        corrections.append(f"{col}: object -> datetime")
                        continue
                except Exception:
                    pass

        self.cleaning_report["dtype_corrections"] = corrections

        if self.verbose and corrections:
            logger.info(f"Corrected data types: {corrections}")

        return df_corrected

    def clean(
        self,
        df: pd.DataFrame,
        remove_duplicates: bool = True,
        fill_missing: bool = True,
        numeric_strategy: str = "mean",
        categorical_strategy: str = "mode",
        fix_dtypes: bool = True,
    ) -> pd.DataFrame:
        """
        Apply all cleaning operations in sequence.

        Args:
            df: Input dataframe
            remove_duplicates: Whether to remove duplicate rows
            fill_missing: Whether to fill missing values
            numeric_strategy: Strategy for numeric columns
            categorical_strategy: Strategy for categorical columns
            fix_dtypes: Whether to correct inconsistent data types

        Returns:
            Cleaned dataframe
        """
        df_clean = df.copy()

        if remove_duplicates:
            df_clean = self.remove_duplicates(df_clean)

        if fill_missing:
            df_clean = self.fill_missing_values(
                df_clean,
                numeric_strategy=numeric_strategy,
                categorical_strategy=categorical_strategy,
            )

        if fix_dtypes:
            df_clean = self.handle_inconsistent_dtypes(df_clean)

        return df_clean.reset_index(drop=True)

    def get_report(self) -> dict:
        """
        Get cleaning report.

        Returns:
            Dictionary with cleaning statistics
        """
        return self.cleaning_report.copy()
