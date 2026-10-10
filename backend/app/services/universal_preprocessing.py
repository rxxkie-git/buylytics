"""Universal preprocessing pipeline for product/store tabular datasets."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.preprocessing import MinMaxScaler, OneHotEncoder, RobustScaler, StandardScaler

from .pipeline_config import EncodingStrategy, PipelineConfig, ScalingMethod
from .preprocessing_report import PreprocessingReport

LOGGER = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".csv", ".xls", ".xlsx"}
MISSING_TEXT_VALUES = {
    "",
    "na",
    "n/a",
    "null",
    "none",
    "nan",
    "nat",
    "-",
    "--",
    "missing",
    "unknown",
}

QUANTITY_COLUMN_HINTS = [
    "quantity",
    "qty",
    "units",
    "unit_sold",
    "units_sold",
    "sold_quantity",
]
PRICE_COLUMN_HINTS = [
    "selling_price",
    "sale_price",
    "unit_price",
    "price",
    "mrp",
]
COST_COLUMN_HINTS = [
    "cost_price",
    "unit_cost",
    "purchase_price",
    "cost",
]


@dataclass
class PreprocessingArtifacts:
    """Serializable fitted preprocessing components for reuse."""

    config: dict[str, Any]
    numeric_fill_values: dict[str, float]
    categorical_fill_values: dict[str, str]
    datetime_fill_values: dict[str, str]
    binary_mappings: dict[str, dict[str, int]]
    one_hot_columns: list[str]
    one_hot_encoder: OneHotEncoder | None
    scaled_columns: list[str]
    scaler: StandardScaler | MinMaxScaler | RobustScaler | None
    output_columns: list[str]
    datetime_columns: list[str]


@dataclass
class PreprocessingResult:
    """Container for preprocessing outputs and saved artifact paths."""

    cleaned_frame: pd.DataFrame
    processed_frame: pd.DataFrame
    report: PreprocessingReport
    cleaned_file_path: Path
    processed_file_path: Path
    pipeline_file_path: Path
    report_file_path: Path


class UniversalPreprocessor:
    """Generic and reusable preprocessing pipeline for tabular business data."""

    def __init__(self, config: PipelineConfig | None = None, verbose: bool = True):
        self.config = config or PipelineConfig()
        self.verbose = verbose
        self.artifacts: PreprocessingArtifacts | None = None

    @classmethod
    def load(cls, pipeline_file_path: Path | str) -> "UniversalPreprocessor":
        """Load a previously saved preprocessor pipeline."""
        bundle = joblib.load(pipeline_file_path)
        config = bundle.get("config", {})
        artifacts = bundle.get("artifacts")

        preprocessor = cls(config=PipelineConfig.from_dict(config))
        preprocessor.artifacts = artifacts
        return preprocessor

    def preprocess_uploaded_file(
        self,
        file_name: str,
        file_bytes: bytes,
        output_dir: Path | str,
        dataset_name: str | None = None,
    ) -> PreprocessingResult:
        """Read uploaded file bytes and run fit+transform preprocessing."""
        frame = self._read_tabular_file(file_name=file_name, file_bytes=file_bytes)
        dataset_label = dataset_name or Path(file_name).stem or "dataset"
        return self.fit_transform(
            frame=frame,
            dataset_name=dataset_label,
            output_dir=Path(output_dir),
            source_filename=file_name,
        )

    def fit_transform(
        self,
        frame: pd.DataFrame,
        dataset_name: str,
        output_dir: Path,
        source_filename: str = "",
    ) -> PreprocessingResult:
        """Fit preprocessing components and transform the input dataframe."""
        if frame.empty:
            raise ValueError("Input dataframe is empty. Unable to preprocess.")

        report = PreprocessingReport(
            filename=source_filename or f"{dataset_name}.csv",
            original_shape=(int(frame.shape[0]), int(frame.shape[1])),
        )

        working = self._normalize_schema(frame)
        working = self._drop_empty_rows(working)
        working = self._sanitize_missing_values(working)

        if working.empty:
            raise ValueError("No usable rows found in the uploaded file.")

        if self.config.handle_duplicates:
            before_rows = len(working)
            working = working.drop_duplicates().reset_index(drop=True)
            report.duplicate_rows_removed = int(before_rows - len(working))

        working, dtype_conversions = self._handle_inconsistent_data_types(working)
        report.columns_with_inconsistent_types = dtype_conversions

        numeric_columns, categorical_columns, datetime_columns = self._detect_column_types(working)
        report.numeric_columns = numeric_columns.copy()
        report.categorical_columns = categorical_columns.copy()
        report.datetime_columns = datetime_columns.copy()

        missing_counts = working.isna().sum()
        report.total_missing_values = int(missing_counts.sum())
        report.missing_values_by_column = {
            str(column): int(count)
            for column, count in missing_counts.items()
            if int(count) > 0
        }

        if self.config.handle_missing_values:
            (
                working,
                numeric_fill_values,
                categorical_fill_values,
                datetime_fill_values,
                fill_count,
                fill_strategy,
            ) = self._fill_missing_values(
                frame=working,
                numeric_columns=numeric_columns,
                categorical_columns=categorical_columns,
                datetime_columns=datetime_columns,
            )
        else:
            numeric_fill_values = {}
            categorical_fill_values = {}
            datetime_fill_values = {}
            fill_count = 0
            fill_strategy = {}

        report.missing_values_filled_count = int(fill_count)
        report.missing_fill_strategy = fill_strategy

        if self.config.engineer_features:
            engineered, engineered_features, datetime_feature_count = self._engineer_features(
                working,
                datetime_columns=datetime_columns,
            )
        else:
            engineered = working.copy()
            engineered_features = []
            datetime_feature_count = 0

        report.engineered_features = engineered_features
        report.datetime_features_count = int(datetime_feature_count)
        report.derived_features_count = max(
            int(len(engineered_features) - datetime_feature_count),
            0,
        )

        processed, binary_mappings, one_hot_columns, one_hot_encoder = self._encode_categorical_features(
            engineered,
            report=report,
        )

        one_hot_feature_columns = [
            feature_name
            for feature_list in report.one_hot_encoded_columns.values()
            for feature_name in feature_list
        ]
        scale_exclusions = report.label_encoded_columns + one_hot_feature_columns

        (
            processed,
            scaled_columns,
            scaler,
            scaling_statistics,
        ) = self._scale_numeric_features(
            processed,
            excluded_columns=scale_exclusions,
        )
        report.scaled_columns = scaled_columns
        report.scaling_method = self.config.scaling_method.value
        report.scaling_statistics = scaling_statistics
        report.final_shape = (int(processed.shape[0]), int(processed.shape[1]))

        self.artifacts = PreprocessingArtifacts(
            config=self.config.to_dict(),
            numeric_fill_values=numeric_fill_values,
            categorical_fill_values=categorical_fill_values,
            datetime_fill_values=datetime_fill_values,
            binary_mappings=binary_mappings,
            one_hot_columns=one_hot_columns,
            one_hot_encoder=one_hot_encoder,
            scaled_columns=scaled_columns,
            scaler=scaler,
            output_columns=processed.columns.tolist(),
            datetime_columns=datetime_columns,
        )

        (
            cleaned_file_path,
            processed_file_path,
            pipeline_file_path,
            report_file_path,
        ) = self._save_outputs(
            dataset_name=dataset_name,
            output_dir=output_dir,
            cleaned_frame=engineered,
            processed_frame=processed,
            report=report,
        )

        if self.verbose:
            report.log_summary()

        return PreprocessingResult(
            cleaned_frame=engineered,
            processed_frame=processed,
            report=report,
            cleaned_file_path=cleaned_file_path,
            processed_file_path=processed_file_path,
            pipeline_file_path=pipeline_file_path,
            report_file_path=report_file_path,
        )

    def transform(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Transform new data using fitted preprocessing artifacts."""
        if self.artifacts is None:
            raise ValueError("Preprocessing artifacts are not fitted. Run fit_transform first.")

        working = self._normalize_schema(frame)
        working = self._drop_empty_rows(working)
        working = self._sanitize_missing_values(working)
        working, _ = self._handle_inconsistent_data_types(working)

        for column, fill_value in self.artifacts.numeric_fill_values.items():
            if column not in working.columns:
                working[column] = np.nan
            working[column] = pd.to_numeric(working[column], errors="coerce").fillna(fill_value)

        for column, fill_value in self.artifacts.categorical_fill_values.items():
            if column not in working.columns:
                working[column] = pd.NA
            working[column] = (
                working[column].astype("string").str.strip().replace("", pd.NA).fillna(fill_value)
            )

        for column, fill_value in self.artifacts.datetime_fill_values.items():
            if column not in working.columns:
                working[column] = pd.NaT
            parsed = pd.to_datetime(working[column], errors="coerce")
            working[column] = parsed.fillna(pd.Timestamp(fill_value))

        engineered, _, _ = self._engineer_features(
            working,
            datetime_columns=self.artifacts.datetime_columns,
        )

        transformed = engineered.copy()

        for column, mapping in self.artifacts.binary_mappings.items():
            if column not in transformed.columns:
                transformed[column] = 0.0
                continue

            encoded = transformed[column].astype("string").map(mapping)
            transformed[column] = encoded.fillna(-1.0).astype(float)

        if self.artifacts.one_hot_encoder is not None and self.artifacts.one_hot_columns:
            for column in self.artifacts.one_hot_columns:
                if column not in transformed.columns:
                    transformed[column] = "Unknown"

            one_hot_source = transformed[self.artifacts.one_hot_columns].astype("string").fillna("Unknown")
            one_hot_matrix = self.artifacts.one_hot_encoder.transform(one_hot_source)
            one_hot_columns = self.artifacts.one_hot_encoder.get_feature_names_out(
                self.artifacts.one_hot_columns
            )

            transformed = transformed.drop(columns=self.artifacts.one_hot_columns)
            transformed = pd.concat(
                [
                    transformed,
                    pd.DataFrame(
                        one_hot_matrix,
                        index=transformed.index,
                        columns=list(one_hot_columns),
                    ),
                ],
                axis=1,
            )

        for column in transformed.columns:
            if not pd.api.types.is_numeric_dtype(transformed[column]):
                transformed[column] = pd.to_numeric(transformed[column], errors="coerce")

        transformed = transformed.fillna(0.0)

        if self.artifacts.scaler is not None and self.artifacts.scaled_columns:
            for column in self.artifacts.scaled_columns:
                if column not in transformed.columns:
                    transformed[column] = 0.0

            scaled_values = self.artifacts.scaler.transform(
                transformed[self.artifacts.scaled_columns].to_numpy(dtype=float)
            )
            for index, column in enumerate(self.artifacts.scaled_columns):
                transformed[column] = scaled_values[:, index]

        for column in self.artifacts.output_columns:
            if column not in transformed.columns:
                transformed[column] = 0.0

        return transformed[self.artifacts.output_columns].reset_index(drop=True)

    def save_pipeline(self, pipeline_file_path: Path | str) -> Path:
        """Persist fitted preprocessing artifacts."""
        if self.artifacts is None:
            raise ValueError("No fitted preprocessing artifacts available to save.")

        pipeline_path = Path(pipeline_file_path)
        pipeline_path.parent.mkdir(parents=True, exist_ok=True)

        bundle = {
            "config": self.config.to_dict(),
            "artifacts": self.artifacts,
            "saved_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        joblib.dump(bundle, pipeline_path)
        return pipeline_path

    def _read_tabular_file(self, file_name: str, file_bytes: bytes) -> pd.DataFrame:
        extension = Path(file_name or "").suffix.lower()

        if extension not in SUPPORTED_EXTENSIONS:
            raise ValueError("Only CSV, XLS, and XLSX files are supported.")

        if not file_bytes:
            raise ValueError("Uploaded file is empty.")

        buffer = BytesIO(file_bytes)
        if extension == ".csv":
            frame = pd.read_csv(buffer)
        else:
            frame = pd.read_excel(buffer)

        if frame.empty:
            raise ValueError("No usable rows found in the uploaded file.")

        return frame

    def _normalize_schema(self, frame: pd.DataFrame) -> pd.DataFrame:
        normalized = frame.copy()
        duplicate_count: dict[str, int] = {}
        normalized_columns: list[str] = []

        for raw_column in normalized.columns:
            cleaned = str(raw_column).strip() or "column"
            duplicate_count[cleaned] = duplicate_count.get(cleaned, 0) + 1
            if duplicate_count[cleaned] > 1:
                cleaned = f"{cleaned}_{duplicate_count[cleaned]}"
            normalized_columns.append(cleaned)

        normalized.columns = normalized_columns
        return normalized.reset_index(drop=True)

    def _drop_empty_rows(self, frame: pd.DataFrame) -> pd.DataFrame:
        return frame.dropna(how="all").reset_index(drop=True)

    def _sanitize_missing_values(self, frame: pd.DataFrame) -> pd.DataFrame:
        cleaned = frame.copy()
        cleaned = cleaned.replace(r"^\s*$", pd.NA, regex=True)
        cleaned = cleaned.replace([np.inf, -np.inf], pd.NA)

        object_like_columns = cleaned.select_dtypes(include=["object", "string", "category"]).columns
        for column in object_like_columns:
            text = cleaned[column].astype("string").str.strip()
            is_missing_token = text.str.lower().isin(MISSING_TEXT_VALUES)
            cleaned[column] = text.mask(is_missing_token, pd.NA)

        return cleaned

    def _handle_inconsistent_data_types(self, frame: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
        corrected = frame.copy()
        conversions: list[str] = []

        for column in corrected.columns:
            series = corrected[column]

            if pd.api.types.is_numeric_dtype(series) or pd.api.types.is_datetime64_any_dtype(series):
                continue

            if pd.api.types.is_bool_dtype(series):
                continue

            as_text = series.astype("string")
            non_null_count = int(as_text.notna().sum())
            if non_null_count == 0:
                corrected[column] = as_text
                continue

            stripped_numeric = (
                as_text.str.replace(r"[,\$]", "", regex=True)
                .str.replace("%", "", regex=False)
                .str.strip()
            )
            numeric_candidate = pd.to_numeric(stripped_numeric, errors="coerce")
            numeric_ratio = float(numeric_candidate.notna().sum() / non_null_count)

            if numeric_ratio >= 0.8:
                corrected[column] = numeric_candidate
                conversions.append(f"{column}: object->numeric")
                continue

            lower_column_name = column.lower()
            has_datetime_hint = any(
                token in lower_column_name for token in ("date", "time", "timestamp", "month")
            )

            should_try_datetime = has_datetime_hint or self._looks_like_datetime_values(as_text)
            if should_try_datetime:
                datetime_candidate = pd.to_datetime(as_text, errors="coerce")
                datetime_ratio = float(datetime_candidate.notna().sum() / non_null_count)
                if datetime_ratio >= 0.8 or (has_datetime_hint and datetime_ratio >= 0.5):
                    corrected[column] = datetime_candidate
                    conversions.append(f"{column}: object->datetime")
                    continue

            corrected[column] = as_text

        return corrected, conversions

    def _detect_column_types(self, frame: pd.DataFrame) -> tuple[list[str], list[str], list[str]]:
        numeric_columns: list[str] = []
        categorical_columns: list[str] = []
        datetime_columns: list[str] = []

        for column in frame.columns:
            series = frame[column]

            if pd.api.types.is_datetime64_any_dtype(series):
                datetime_columns.append(column)
            elif pd.api.types.is_bool_dtype(series):
                categorical_columns.append(column)
            elif pd.api.types.is_numeric_dtype(series):
                numeric_columns.append(column)
            else:
                categorical_columns.append(column)

        return numeric_columns, categorical_columns, datetime_columns

    def _fill_missing_values(
        self,
        frame: pd.DataFrame,
        numeric_columns: list[str],
        categorical_columns: list[str],
        datetime_columns: list[str],
    ) -> tuple[
        pd.DataFrame,
        dict[str, float],
        dict[str, str],
        dict[str, str],
        int,
        dict[str, str],
    ]:
        filled = frame.copy()
        numeric_fill_values: dict[str, float] = {}
        categorical_fill_values: dict[str, str] = {}
        datetime_fill_values: dict[str, str] = {}
        fill_strategy: dict[str, str] = {}
        filled_count = 0

        for column in numeric_columns:
            numeric_series = pd.to_numeric(filled[column], errors="coerce")
            missing_count = int(numeric_series.isna().sum())
            fill_value = float(numeric_series.mean()) if numeric_series.notna().any() else 0.0
            numeric_fill_values[column] = fill_value
            filled[column] = numeric_series.fillna(fill_value)
            if missing_count > 0:
                filled_count += missing_count
                fill_strategy[column] = "mean"

        for column in categorical_columns:
            categorical_series = filled[column].astype("string")
            missing_count = int(categorical_series.isna().sum())
            mode_values = categorical_series.mode(dropna=True)
            fill_value = str(mode_values.iloc[0]) if not mode_values.empty else "Unknown"
            categorical_fill_values[column] = fill_value
            filled[column] = categorical_series.fillna(fill_value)
            if missing_count > 0:
                filled_count += missing_count
                fill_strategy[column] = "mode"

        for column in datetime_columns:
            datetime_series = pd.to_datetime(filled[column], errors="coerce")
            missing_count = int(datetime_series.isna().sum())
            mode_values = datetime_series.mode(dropna=True)
            fill_value = mode_values.iloc[0] if not mode_values.empty else pd.Timestamp("1970-01-01")
            datetime_fill_values[column] = pd.Timestamp(fill_value).isoformat()
            filled[column] = datetime_series.fillna(fill_value)
            if missing_count > 0:
                filled_count += missing_count
                fill_strategy[column] = "mode(datetime)"

        return (
            filled,
            numeric_fill_values,
            categorical_fill_values,
            datetime_fill_values,
            filled_count,
            fill_strategy,
        )

    def _engineer_features(
        self,
        frame: pd.DataFrame,
        datetime_columns: list[str],
    ) -> tuple[pd.DataFrame, list[str], int]:
        engineered = frame.copy()
        engineered_features: list[str] = []
        datetime_feature_count = 0

        quantity_column = self._find_column(engineered.columns.tolist(), QUANTITY_COLUMN_HINTS)
        price_column = self._find_column(engineered.columns.tolist(), PRICE_COLUMN_HINTS)
        cost_column = self._find_column(engineered.columns.tolist(), COST_COLUMN_HINTS)
        selling_price_column = self._find_column(engineered.columns.tolist(), PRICE_COLUMN_HINTS)

        if quantity_column and price_column:
            quantity_values = pd.to_numeric(engineered[quantity_column], errors="coerce").fillna(0.0)
            price_values = pd.to_numeric(engineered[price_column], errors="coerce").fillna(0.0)
            engineered["Total_Sales"] = quantity_values * price_values
            engineered_features.append("Total_Sales")

        if cost_column and selling_price_column:
            cost_values = pd.to_numeric(engineered[cost_column], errors="coerce").fillna(0.0)
            selling_values = pd.to_numeric(engineered[selling_price_column], errors="coerce").fillna(0.0)
            safe_denominator = selling_values.replace(0, np.nan)
            profit_margin = ((selling_values - cost_values) / safe_denominator).replace([np.inf, -np.inf], np.nan)
            engineered["Profit_Margin"] = profit_margin.fillna(0.0)
            engineered_features.append("Profit_Margin")

        if self.config.datetime_feature_extraction:
            for column in datetime_columns:
                if column not in engineered.columns:
                    continue

                parsed = pd.to_datetime(engineered[column], errors="coerce")
                if parsed.notna().sum() == 0:
                    continue

                derived_columns = {
                    f"{column}_Year": parsed.dt.year.fillna(0).astype(float),
                    f"{column}_Month": parsed.dt.month.fillna(0).astype(float),
                    f"{column}_Day": parsed.dt.day.fillna(0).astype(float),
                    f"{column}_Weekday": parsed.dt.dayofweek.fillna(0).astype(float),
                }

                for derived_name, derived_series in derived_columns.items():
                    engineered[derived_name] = derived_series
                    engineered_features.append(derived_name)
                    datetime_feature_count += 1

                engineered = engineered.drop(columns=[column])

        return engineered.reset_index(drop=True), engineered_features, datetime_feature_count

    def _encode_categorical_features(
        self,
        frame: pd.DataFrame,
        report: PreprocessingReport,
    ) -> tuple[pd.DataFrame, dict[str, dict[str, int]], list[str], OneHotEncoder | None]:
        encoded = frame.copy()
        categorical_columns = encoded.select_dtypes(
            include=["object", "string", "category", "bool"]
        ).columns.tolist()

        binary_mappings: dict[str, dict[str, int]] = {}
        label_encoded_columns: list[str] = []
        one_hot_columns: list[str] = []

        for column in categorical_columns:
            values = encoded[column].astype("string").fillna("Unknown").str.strip()
            unique_count = int(values.nunique(dropna=True))

            if (
                unique_count == 2
                and self.config.binary_encoding_strategy == EncodingStrategy.LABEL
            ):
                unique_values = sorted(str(value) for value in values.dropna().unique().tolist())
                mapping = {value: index for index, value in enumerate(unique_values)}
                encoded[column] = values.map(mapping).astype(float)
                binary_mappings[column] = mapping
                label_encoded_columns.append(column)
                report.encoded_columns[column] = "label"
            elif unique_count <= 50 and not (len(values) > 50 and (unique_count / len(values)) > 0.3):
                one_hot_columns.append(column)
                report.encoded_columns[column] = "one_hot"
            else:
                report.encoded_columns[column] = "skipped_high_cardinality"

        one_hot_encoder: OneHotEncoder | None = None
        one_hot_encoded_columns: dict[str, list[str]] = {}

        if self.config.encoding_strategy == EncodingStrategy.ONE_HOT and one_hot_columns:
            one_hot_source = encoded[one_hot_columns].astype("string").fillna("Unknown")
            one_hot_encoder = OneHotEncoder(handle_unknown="ignore", sparse_output=False, dtype=np.float64)
            one_hot_matrix = one_hot_encoder.fit_transform(one_hot_source)
            one_hot_feature_names = one_hot_encoder.get_feature_names_out(one_hot_columns).tolist()

            one_hot_frame = pd.DataFrame(
                one_hot_matrix,
                columns=one_hot_feature_names,
                index=encoded.index,
            )
            encoded = encoded.drop(columns=one_hot_columns)
            encoded = pd.concat([encoded, one_hot_frame], axis=1)

            for column in one_hot_columns:
                generated = [name for name in one_hot_feature_names if name.startswith(f"{column}_")]
                one_hot_encoded_columns[column] = generated

        report.binary_categorical_columns = label_encoded_columns.copy()
        report.label_encoded_columns = label_encoded_columns
        report.one_hot_encoded_columns = one_hot_encoded_columns

        for column in encoded.columns:
            if not pd.api.types.is_numeric_dtype(encoded[column]):
                encoded[column] = pd.to_numeric(encoded[column], errors="coerce")

        encoded = encoded.fillna(0.0)
        return encoded, binary_mappings, one_hot_columns, one_hot_encoder

    def _scale_numeric_features(
        self,
        frame: pd.DataFrame,
        excluded_columns: list[str] | None = None,
    ) -> tuple[
        pd.DataFrame,
        list[str],
        StandardScaler | MinMaxScaler | RobustScaler | None,
        dict[str, dict[str, float]],
    ]:
        scaled_frame = frame.copy()
        excluded = set(excluded_columns or [])
        numeric_columns = [
            column
            for column in scaled_frame.select_dtypes(include=[np.number]).columns.tolist()
            if column not in excluded
        ]

        scaling_statistics: dict[str, dict[str, float]] = {}
        for column in numeric_columns:
            series = scaled_frame[column]
            scaling_statistics[column] = {
                "mean": float(series.mean()),
                "std": float(series.std(ddof=0)),
                "min": float(series.min()),
                "max": float(series.max()),
            }

        if self.config.scaling_method == ScalingMethod.NONE or not numeric_columns:
            return scaled_frame, [], None, scaling_statistics

        scaler: StandardScaler | MinMaxScaler | RobustScaler | None
        if self.config.scaling_method == ScalingMethod.MINMAX:
            scaler = MinMaxScaler()
        elif self.config.scaling_method == ScalingMethod.ROBUST:
            scaler = RobustScaler()
        else:
            scaler = StandardScaler()

        for column in numeric_columns:
            scaled_frame[column] = scaled_frame[column].astype(float)

        scaled_values = scaler.fit_transform(scaled_frame[numeric_columns].to_numpy(dtype=float))
        scaled_values_frame = pd.DataFrame(
            scaled_values,
            columns=numeric_columns,
            index=scaled_frame.index,
        )

        for column in numeric_columns:
            scaled_frame[column] = scaled_values_frame[column]

        return scaled_frame, numeric_columns, scaler, scaling_statistics

    def _save_outputs(
        self,
        dataset_name: str,
        output_dir: Path,
        cleaned_frame: pd.DataFrame,
        processed_frame: pd.DataFrame,
        report: PreprocessingReport,
    ) -> tuple[Path, Path, Path, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        cleaned_dir = output_dir / "cleaned"
        processed_dir = output_dir / "processed"
        pipeline_dir = output_dir / "pipelines"
        report_dir = output_dir / "reports"

        for directory in (cleaned_dir, processed_dir, pipeline_dir, report_dir):
            directory.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        safe_name = self._safe_file_name(dataset_name)

        cleaned_file_path = cleaned_dir / f"{safe_name}_{timestamp}_cleaned.csv"
        processed_file_path = processed_dir / f"{safe_name}_{timestamp}_processed.csv"
        pipeline_file_path = pipeline_dir / f"{safe_name}_{timestamp}_preprocessor.joblib"
        report_file_path = report_dir / f"{safe_name}_{timestamp}_report.json"

        cleaned_frame.to_csv(cleaned_file_path, index=False)
        processed_frame.to_csv(processed_file_path, index=False)
        self.save_pipeline(pipeline_file_path)
        report_file_path.write_text(
            json.dumps(report.to_dict(), indent=2, default=self._json_default),
            encoding="utf-8",
        )

        return cleaned_file_path, processed_file_path, pipeline_file_path, report_file_path

    def _find_column(self, columns: list[str], hints: list[str]) -> str | None:
        normalized_map = {self._normalize_key(column): column for column in columns}

        for hint in hints:
            normalized_hint = self._normalize_key(hint)
            if normalized_hint in normalized_map:
                return normalized_map[normalized_hint]

        for column in columns:
            normalized_column = self._normalize_key(column)
            if any(self._normalize_key(hint) in normalized_column for hint in hints):
                return column

        return None

    def _normalize_key(self, value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")

    def _looks_like_datetime_values(self, values: pd.Series) -> bool:
        sample = values.dropna().astype("string").str.strip().head(25)
        if sample.empty:
            return False

        pattern = (
            r"(^\d{4}[-/]\d{1,2}[-/]\d{1,2}$)"
            r"|(^\d{1,2}[-/]\d{1,2}[-/]\d{2,4}$)"
            r"|(^\d{4}-\d{2}-\d{2}T)"
        )
        match_ratio = float(sample.str.match(pattern).mean())
        return match_ratio >= 0.5

    def _safe_file_name(self, value: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip())
        return safe.strip("_") or "dataset"

    def _json_default(self, value: Any) -> Any:
        if isinstance(value, (pd.Timestamp, datetime)):
            return value.isoformat()
        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            return float(value)
        if isinstance(value, np.ndarray):
            return value.tolist()
        return str(value)

