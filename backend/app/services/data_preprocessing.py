from __future__ import annotations

from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd

SUPPORTED_EXTENSIONS = {".csv", ".xls", ".xlsx"}


class InputPreprocessor:
    """Preprocessing template for uploaded tabular data.

    The frontend sends the file as multipart/form-data. This helper reads the
    uploaded file bytes, converts them into a pandas DataFrame, applies any
    cleanup rules, and can return the data as a NumPy array for downstream use.
    """

    def load_uploaded_file(
        self,
        file_name: str,
        file_bytes: bytes,
    ) -> tuple[pd.DataFrame, pd.DataFrame, np.ndarray]:
        extension = Path(file_name or "").suffix.lower()

        if extension not in SUPPORTED_EXTENSIONS:
            raise ValueError("Only CSV, XLS, and XLSX files are supported.")

        if not file_bytes:
            raise ValueError("Uploaded file is empty.")

        file_buffer = BytesIO(file_bytes)

        if extension == ".csv":
            frame = pd.read_csv(file_buffer)
        else:
            frame = pd.read_excel(file_buffer)

        frame = self.run(frame)
        feature_frame = self.engineer_features(frame)

        if frame.empty:
            raise ValueError("No usable rows found in the uploaded file.")

        if feature_frame.empty:
            feature_frame = pd.DataFrame({"row_index": np.arange(len(frame), dtype=float)})

        feature_frame = feature_frame.reset_index(drop=True)

        return frame, feature_frame, feature_frame.to_numpy(dtype=float)

    def normalize_schema(self, frame: pd.DataFrame) -> pd.DataFrame:
        normalized = frame.copy()
        normalized.columns = [str(column).strip() for column in normalized.columns]
        normalized = normalized.loc[:, ~normalized.columns.duplicated()]
        return normalized.reset_index(drop=True)

    def clean_values(self, frame: pd.DataFrame) -> pd.DataFrame:
        cleaned = frame.copy()
        cleaned = cleaned.dropna(how="all")
        for col in cleaned.select_dtypes(include=["object", "string"]).columns:
            cleaned[col] = cleaned[col].replace(r"^\s*$", pd.NA, regex=True)
        cleaned = cleaned.drop_duplicates().reset_index(drop=True)

        numeric_columns = cleaned.select_dtypes(include=[np.number]).columns
        for column in numeric_columns:
            if cleaned[column].isna().any():
                cleaned[column] = cleaned[column].fillna(cleaned[column].mean())

        return cleaned.reset_index(drop=True)

    def run(self, frame: pd.DataFrame) -> pd.DataFrame:
        normalized = self.normalize_schema(frame)
        cleaned = self.clean_values(normalized)
        return cleaned

    def engineer_features(self, frame: pd.DataFrame) -> pd.DataFrame:
        numeric_columns, datetime_columns, categorical_columns = self._classify_columns(frame)

        feature_parts: list[pd.DataFrame] = []

        if numeric_columns:
            numeric_frame = self._coerce_numeric_frame(frame[numeric_columns])
            feature_parts.append(self._standardize_numeric_frame(numeric_frame))

        if datetime_columns:
            datetime_frame = self._extract_datetime_features(frame[datetime_columns])
            if not datetime_frame.empty:
                feature_parts.append(self._standardize_numeric_frame(datetime_frame))

        if categorical_columns:
            categorical_frame = self._one_hot_encode_frame(frame[categorical_columns])
            if not categorical_frame.empty:
                feature_parts.append(categorical_frame.astype(float))

        if not feature_parts:
            return pd.DataFrame(index=frame.index)

        engineered = pd.concat(feature_parts, axis=1)
        engineered = engineered.loc[:, ~engineered.columns.duplicated()]
        engineered = engineered.fillna(0.0)

        return engineered.reset_index(drop=True)

    def _classify_columns(self, frame: pd.DataFrame) -> tuple[list[str], list[str], list[str]]:
        numeric_columns: list[str] = []
        datetime_columns: list[str] = []
        categorical_columns: list[str] = []

        for column in frame.columns:
            series = frame[column]

            if pd.api.types.is_bool_dtype(series) or pd.api.types.is_numeric_dtype(series):
                numeric_columns.append(column)
                continue

            if self._looks_like_datetime(series, column):
                datetime_columns.append(column)
                continue

            if self._numeric_parse_ratio(series) >= 0.8:
                numeric_columns.append(column)
                continue

            # Filter out high cardinality columns and identifiers from one-hot encoding
            non_null = series.dropna()
            if non_null.empty:
                continue
            unique_count = non_null.nunique()
            col_lower = str(column).lower()
            is_id_column = any(hint in col_lower for hint in ["id", "name", "email", "phone", "address", "desc", "url", "sku"])
            if is_id_column or unique_count > 50 or (len(non_null) > 50 and (unique_count / len(non_null)) > 0.2):
                continue

            categorical_columns.append(column)

        return numeric_columns, datetime_columns, categorical_columns

    def _coerce_numeric_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        numeric_frame = pd.DataFrame(index=frame.index)

        for column in frame.columns:
            numeric_frame[column] = pd.to_numeric(frame[column], errors="coerce")

        return numeric_frame

    def _standardize_numeric_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        standardized = pd.DataFrame(index=frame.index)

        for column in frame.columns:
            series = pd.to_numeric(frame[column], errors="coerce")

            if not series.notna().any():
                standardized[column] = 0.0
                continue

            fill_value = float(series.median()) if series.notna().any() else 0.0
            if np.isnan(fill_value):
                fill_value = 0.0

            series = series.fillna(fill_value).astype(float)
            mean_value = float(series.mean())
            std_value = float(series.std(ddof=0))

            if std_value == 0 or np.isnan(std_value):
                standardized[column] = 0.0
            else:
                standardized[column] = (series - mean_value) / std_value

        return standardized

    def _extract_datetime_features(self, frame: pd.DataFrame) -> pd.DataFrame:
        datetime_features: dict[str, pd.Series] = {}

        for column in frame.columns:
            try:
                parsed = pd.to_datetime(frame[column], errors="coerce", utc=True, format="mixed")
            except Exception:
                parsed = pd.to_datetime(frame[column], errors="coerce", utc=True)
            if parsed.notna().sum() == 0:
                continue

            parsed = parsed.dt.tz_convert(None)
            datetime_features[f"{column}_year"] = parsed.dt.year.astype(float)
            datetime_features[f"{column}_month"] = parsed.dt.month.astype(float)
            datetime_features[f"{column}_day"] = parsed.dt.day.astype(float)
            datetime_features[f"{column}_dayofweek"] = parsed.dt.dayofweek.astype(float)
            datetime_features[f"{column}_dayofyear"] = parsed.dt.dayofyear.astype(float)

        if not datetime_features:
            return pd.DataFrame(index=frame.index)

        return pd.DataFrame(datetime_features, index=frame.index)

    def _one_hot_encode_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        encoded_source = pd.DataFrame(index=frame.index)

        for column in frame.columns:
            series = frame[column].astype("string").str.strip()
            series = series.replace({"": pd.NA, "nan": pd.NA, "None": pd.NA, "NaT": pd.NA})
            series = series.fillna("Unknown")
            encoded_source[column] = series

        if encoded_source.empty:
            return pd.DataFrame(index=frame.index)

        return pd.get_dummies(encoded_source, columns=list(encoded_source.columns), dtype=float)

    def _looks_like_datetime(self, series: pd.Series, column_name: str) -> bool:
        lower_name = column_name.lower()
        if any(token in lower_name for token in ("date", "time", "timestamp", "month")):
            return True

        sample = series.dropna().head(25)
        if sample.empty:
            return False

        try:
            parsed = pd.to_datetime(sample, errors="coerce", utc=True, format="mixed")
        except Exception:
            parsed = pd.to_datetime(sample, errors="coerce", utc=True)
        return parsed.notna().mean() >= 0.7

    def _numeric_parse_ratio(self, series: pd.Series) -> float:
        sample = series.dropna().head(50)
        if sample.empty:
            return 0.0

        parsed = pd.to_numeric(sample, errors="coerce")
        return float(parsed.notna().mean())
