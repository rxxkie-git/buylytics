"""Legacy single-dataset upload endpoints (Phase 1 ML surface).

These two handlers are the heaviest synchronous work in the application: pandas
parsing, feature engineering, and KMeans inference. They were ``async def``,
which means every one of those CPU-bound seconds ran *on the event loop* and
froze the whole API — ``/health`` included. That is the same defect as the import
path, in the place where it costs the most, so the same remedy applies: the
handler stays ``async`` only because it must ``await file.read()``, and
everything after that is handed to a worker thread through
:func:`app.core.concurrency.run_import_blocking`.

The bare ``print()`` calls this module used are gone. They bypassed the logging
configuration entirely, so they carried no request id, no tenant, and no
timestamp, and in a container they interleave unpredictably with real log output.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, Depends

from ..config import settings
from ..core.concurrency import (
    BlockingWorkBusy,
    BlockingWorkTimeout,
    run_import_blocking,
)
from ..core.logging import get_logger
from ..services.data_preprocessing import InputPreprocessor
from ..services.inference import run_segmentation
from ..services.insights import (
    build_analysis_payload,
    build_dashboard_payload,
    build_insights_payload,
    build_preview_payload,
    build_results_payload,
)
from ..services.pipeline_config import EncodingStrategy, PipelineConfig, ScalingMethod
from ..services.preprocess import (
    SUPPORTED_EXTENSIONS,
    build_customer_frame,
    to_serializable_records,
)
from ..services.universal_preprocessing import UniversalPreprocessor
from ..auth import get_current_user, ClerkUser

logger = get_logger(__name__)

router = APIRouter(tags=["upload"])


def _parse_scaling_method(raw_value: str) -> ScalingMethod:
    normalized = (raw_value or "").strip().lower()
    for method in ScalingMethod:
        if method.value == normalized:
            return method

    supported_methods = ", ".join(method.value for method in ScalingMethod)
    raise HTTPException(
        status_code=400,
        detail=f"Invalid scaling_method. Supported values: {supported_methods}.",
    )


def _busy_error(operation: str) -> HTTPException:
    """503 with Retry-After: this request did no work, so retrying is safe."""
    logger.warning("Upload rejected at capacity", extra={"operation": operation})
    return HTTPException(
        status_code=503,
        detail=(
            "The server is processing other uploads and could not start this one. "
            "Retry in a few seconds."
        ),
        headers={"Retry-After": "10"},
    )


def _slow_error(operation: str, budget: float) -> HTTPException:
    """504 phrased honestly — the thread cannot be killed, so it may still finish."""
    logger.error(
        "Upload exceeded its time budget",
        extra={"operation": operation, "timeout_seconds": budget},
    )
    return HTTPException(
        status_code=504,
        detail=(
            f"Processing this dataset took longer than {budget:.0f}s and the "
            "request was released. The work may still be completing in the "
            "background. Try a smaller file."
        ),
    )


@router.post("/upload")
async def upload_dataset(
    request: Request,
    file: UploadFile = File(...),
    current_user: ClerkUser = Depends(get_current_user)
) -> dict:
    file_name = file.filename or ""
    extension = Path(file_name).suffix.lower()

    if extension not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Only CSV, XLS, and XLSX files are supported.")

    file_bytes = await file.read()

    logger.info(
        "ml_upload_started",
        extra={"dataset_type": "customer", "file_bytes": len(file_bytes)},
    )

    upload_meta = {
        "fileName": file_name,
        "size": file.size if file.size is not None else len(file_bytes),
        "type": file.content_type or "application/octet-stream",
        "uploadedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    inference_artifacts = request.app.state.inference_artifacts
    repository = request.app.state.dataset_repository
    dataset_id = str(uuid4())

    def _process() -> dict[str, Any]:
        """Everything synchronous, executed off the loop in one thread.

        Kept as a single closure rather than several offloads: the intermediate
        DataFrames are large, and hopping back to the loop between stages would
        gain nothing while multiplying the handoffs.
        """
        input_preprocessor = InputPreprocessor()
        raw_frame, engineered_frame, engineered_input_array = input_preprocessor.load_uploaded_file(
            file_name,
            file_bytes,
        )
        customers, quality = build_customer_frame(raw_frame)

        inference_result = run_segmentation(
            customers,
            inference_artifacts,
            feature_frame=engineered_frame,
        )

        dataset_document = {
            "datasetId": dataset_id,
            "datasetType": "customer",
            "createdAt": datetime.now(timezone.utc),
            "uploadMeta": upload_meta,
            "quality": quality,
            "preview": build_preview_payload(inference_result.customers, quality, upload_meta),
            "dashboard": build_dashboard_payload(
                inference_result.customers,
                upload_meta,
                quality,
                inference_result.segment_mix,
                inference_result.model_meta,
            ),
            "analysis": build_analysis_payload(
                inference_result.customers,
                inference_result.cluster_distribution,
                inference_result.cluster_profiles,
                inference_result.silhouette_score,
                inference_result.elbow_method,
                inference_result.model_meta,
            ),
            "results": build_results_payload(
                inference_result.customers,
                inference_result.segment_mix,
                inference_result.model_meta,
            ),
            "insights": build_insights_payload(
                inference_result.customers,
                quality,
                inference_result.model_meta,
            ),
            "modelMeta": inference_result.model_meta,
        }

        # Persisted inside the thread too: save_dataset is a synchronous driver
        # call, and leaving it on the loop would undo the point of the offload.
        if repository is not None:
            repository.save_dataset(dataset_document)

        return {
            "quality": quality,
            "model_meta": inference_result.model_meta,
            "input_array": engineered_input_array,
            "feature_frame": engineered_frame,
        }

    try:
        outcome = await run_import_blocking(
            _process,
            operation="ml_upload.customer",
            timeout_seconds=settings.import_timeout_seconds,
        )
    except BlockingWorkBusy:
        raise _busy_error("ml_upload.customer") from None
    except BlockingWorkTimeout as exc:
        raise _slow_error("ml_upload.customer", exc.timeout_seconds) from None
    except ValueError as exc:
        # Raised by load_uploaded_file / build_customer_frame for unusable input.
        logger.warning(
            "ml_upload_failed",
            extra={"dataset_type": "customer", "reason": "invalid_input"},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Loop-side state assignment only: cheap, and it must happen on the loop
    # because app.state is shared mutable process state.
    request.app.state.last_uploaded_input_array = outcome["input_array"]
    request.app.state.last_uploaded_feature_frame = outcome["feature_frame"]

    quality = outcome["quality"]

    logger.info(
        "ml_upload_completed",
        extra={
            "dataset_type": "customer",
            "dataset_id": dataset_id,
            "rows": quality["totalRows"],
            "columns": quality["totalColumns"],
        },
    )

    return {
        "success": True,
        "message": "Dataset uploaded and queued for analysis.",
        "datasetId": dataset_id,
        "uploadMeta": upload_meta,
        "preview": {
            "rows": quality["totalRows"],
            "columns": quality["totalColumns"],
        },
        "modelMeta": outcome["model_meta"],
    }


@router.post("/upload-product")
async def upload_product_dataset(
    request: Request,
    file: UploadFile = File(...),
    scaling_method: str = Form("standard"),
    use_binary_label_encoding: bool = Form(True),
    current_user: ClerkUser = Depends(get_current_user)
) -> dict:
    file_name = file.filename or ""
    extension = Path(file_name).suffix.lower()

    if extension not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Only CSV, XLS, and XLSX files are supported.")

    file_bytes = await file.read()
    dataset_id = str(uuid4())

    # Validated before the offload so a bad form value returns 400 immediately
    # rather than occupying an import slot to do it.
    parsed_scaling = _parse_scaling_method(scaling_method)

    logger.info(
        "ml_upload_started",
        extra={"dataset_type": "product", "file_bytes": len(file_bytes)},
    )

    upload_meta = {
        "fileName": file_name,
        "size": file.size if file.size is not None else len(file_bytes),
        "type": file.content_type or "application/octet-stream",
        "uploadedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    repository = request.app.state.dataset_repository

    def _process() -> dict[str, Any]:
        pipeline_config = PipelineConfig(
            scaling_method=parsed_scaling,
            encoding_strategy=EncodingStrategy.ONE_HOT,
            binary_encoding_strategy=(
                EncodingStrategy.LABEL if use_binary_label_encoding else None
            ),
            handle_duplicates=True,
            handle_missing_values=True,
            engineer_features=True,
            datetime_feature_extraction=True,
        )
        preprocessor = UniversalPreprocessor(config=pipeline_config)
        preprocessing_result = preprocessor.preprocess_uploaded_file(
            file_name=file_name,
            file_bytes=file_bytes,
            output_dir=settings.preprocessing_output_dir,
            dataset_name=f"{Path(file_name).stem}_{dataset_id[:8]}",
        )

        artifact_paths = {
            "cleanedFilePath": str(preprocessing_result.cleaned_file_path),
            "processedFilePath": str(preprocessing_result.processed_file_path),
            "pipelineFilePath": str(preprocessing_result.pipeline_file_path),
            "reportFilePath": str(preprocessing_result.report_file_path),
        }

        dataset_document = {
            "datasetId": dataset_id,
            "datasetType": "product",
            "createdAt": datetime.now(timezone.utc),
            "uploadMeta": upload_meta,
            "productData": {
                "rows": int(preprocessing_result.report.original_shape[0]),
                "columns": int(preprocessing_result.report.original_shape[1]),
                "columnNames": [str(column) for column in preprocessing_result.cleaned_frame.columns],
                "processedColumnNames": [
                    str(column) for column in preprocessing_result.processed_frame.columns
                ],
                "cleanedPreviewRecords": to_serializable_records(
                    preprocessing_result.cleaned_frame.head(50)
                ),
                "processedPreviewRecords": to_serializable_records(
                    preprocessing_result.processed_frame.head(50)
                ),
                "preprocessingReport": preprocessing_result.report.to_dict(),
                "artifactPaths": artifact_paths,
            },
        }

        if repository is not None:
            repository.save_dataset(dataset_document)

        report = preprocessing_result.report

        return {
            "rows": int(preprocessing_result.processed_frame.shape[0]),
            "columns": int(preprocessing_result.processed_frame.shape[1]),
            "artifact_paths": artifact_paths,
            "summary": {
                "duplicatesRemoved": report.duplicate_rows_removed,
                "missingValuesHandled": report.missing_values_filled_count,
                "encodedColumns": sorted(report.encoded_columns.keys()),
                "scaledColumns": report.scaled_columns,
                "engineeredFeatures": report.engineered_features,
                "scalingMethod": report.scaling_method,
            },
        }

    try:
        outcome = await run_import_blocking(
            _process,
            operation="ml_upload.product",
            timeout_seconds=settings.import_timeout_seconds,
        )
    except BlockingWorkBusy:
        raise _busy_error("ml_upload.product") from None
    except BlockingWorkTimeout as exc:
        raise _slow_error("ml_upload.product", exc.timeout_seconds) from None
    except ValueError as exc:
        logger.warning(
            "ml_upload_failed",
            extra={"dataset_type": "product", "reason": "invalid_input"},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - defensive API error handling
        logger.exception(
            "ml_upload_failed",
            extra={"dataset_type": "product", "reason": "preprocessing_error"},
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to preprocess uploaded dataset. Please verify file contents and retry.",
        ) from exc

    logger.info(
        "ml_upload_completed",
        extra={
            "dataset_type": "product",
            "dataset_id": dataset_id,
            "rows": outcome["rows"],
            "columns": outcome["columns"],
        },
    )

    return {
        "success": True,
        "message": "Product dataset uploaded, preprocessed, and stored successfully.",
        "datasetId": dataset_id,
        "uploadMeta": upload_meta,
        "preview": {
            "rows": outcome["rows"],
            "columns": outcome["columns"],
        },
        "preprocessingSummary": outcome["summary"],
        "artifactPaths": outcome["artifact_paths"],
    }
