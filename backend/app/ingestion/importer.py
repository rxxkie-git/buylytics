"""Import orchestration service.

Phase 2 adds two things to the Phase 1 orchestrator:

``import_orders``
    Orders are what make RFM real. Without them ``recency``/``frequency``/
    ``monetary`` have nothing to measure, so this is the import that turns the
    analytics engine on.
``_store_import_rfm``
    When an uploaded customers file already carries genuine recency, frequency and
    monetary columns, those values are kept rather than discarded. They are stored
    with ``source='import'`` so the origin of every number stays visible, and they
    are only ever used for customers who have no measured orders.

Nothing here invents a value. A customer with neither orders nor real columns gets
no RFM row at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.analytics.scoring import has_real_rfm_columns, rfm_from_import_record
from app.core.logging import get_logger
from app.ingestion.column_mapping import (
    ColumnMappingError,
    ResolvedColumns,
    apply_mapping,
    mapping_summary,
)
from app.ingestion.file_parser import DataValidator, FileParser
from app.repositories.analytics_repository import AnalyticsRepository
from app.services.customer_service import CustomerService
from app.services.import_service import ImportService
from app.services.inventory_service import InventoryService
from app.services.order_service import OrderService
from app.services.product_service import ProductService

logger = get_logger(__name__)


class ImportOrchestrator:
    """Orchestrate file imports and data persistence.

    Response shape, and why it is counts rather than rows
    ----------------------------------------------------
    Every ``import_*`` method returns *counts* — ``rows_processed``,
    ``rows_persisted``, ``rows_failed`` — and never the persisted ORM objects.

    Three of these four methods used to return ``"records": <list of ORM rows>``.
    ``upload_orders`` stripped it (``result.pop("records", None)``); the other
    three did not, which is precisely why customers, products and inventory failed
    in the UI while orders succeeded. The import itself was fine: it committed, and
    *then* FastAPI had to encode the returned rows. On this project's own
    ``dataset/df_Customers.csv`` (89,316 rows) that is a measured 0.93 s in
    ``jsonable_encoder`` plus 0.13 s in ``json.dumps``, on the event loop, after
    the commit, producing a 24.81 MiB response body — for data the frontend never
    reads. The same response without it is 394 bytes.

    So the fix is structural, not cosmetic: nothing here returns ORM instances.
    ``expire_on_commit=False`` (see ``app/db/session.py``) means those instances
    keep every loaded column after the commit, so returning them is never cheap and
    the cost scales with the file.

    ``rows_persisted`` exists because ``rows_processed`` is not the same number.
    Upserts collapse duplicate keys, so a 3,000-row product file with repeated
    ``product_id`` values persists fewer rows than it processes. Reporting only the
    larger number is how "3,000 rows processed" ends up above a 2,420-row table.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.import_service = ImportService(session)
        self.customer_service = CustomerService(session)
        self.product_service = ProductService(session)
        self.inventory_service = InventoryService(session)
        self.order_service = OrderService(session)

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_and_map(file_path: Path, dataset: str) -> tuple[Any, ResolvedColumns]:
        """Read the file and rename its columns to the canonical names.

        Raises :class:`ColumnMappingError` when a required field has no column.
        Callers turn that into a report naming the field, so a header problem no
        longer arrives as "0 rows processed, N skipped".
        """
        df = FileParser.parse_file(file_path)
        return apply_mapping(df, dataset)

    def _mapping_failure(self, import_id: str, error: ColumnMappingError) -> dict[str, Any]:
        """Record the failure and return a payload the UI can act on."""
        self.session.rollback()
        self.import_service.mark_failed(import_id, str(error))
        self.session.commit()

        logger.warning(
            "Import rejected: columns could not be mapped",
            extra={
                "import_id": import_id,
                "dataset": error.dataset,
                "missing_fields": error.missing_required,
                "ambiguous_fields": sorted(error.ambiguous),
            },
        )

        return {
            "import_id": import_id,
            "status": "failed",
            "rows_processed": 0,
            "rows_failed": 0,
            **error.to_payload(),
        }

    # ------------------------------------------------------------------
    # RFM passthrough
    # ------------------------------------------------------------------

    def _store_import_rfm(self, business_id: str, df: Any, customers: list) -> dict[str, int]:
        """Keep real RFM columns from an uploaded customers file.

        The validator strips unknown columns, so the values are read back off the
        parsed DataFrame and matched to persisted customers by external id.

        Returns counts rather than raising: a file without RFM columns is the
        normal case, not an error.
        """
        by_external_id = {
            str(customer.external_customer_id): customer
            for customer in customers
            if getattr(customer, "external_customer_id", None)
        }

        if not by_external_id or df is None or "external_customer_id" not in getattr(df, "columns", []):
            return {"rfm_rows_from_import": 0, "rows_with_partial_rfm": 0}

        rows: list[dict[str, object]] = []
        partial = 0

        for record in df.to_dict(orient="records"):
            external_id = record.get("external_customer_id")

            if external_id is None:
                continue

            customer = by_external_id.get(str(external_id))

            if customer is None:
                continue

            if not has_real_rfm_columns(record):
                # Some RFM-ish column present but not all three usable values.
                if any(
                    key.strip().lower().replace(" ", "_").replace("-", "_")
                    in {"recency", "frequency", "monetary"}
                    for key in record
                ):
                    partial += 1
                continue

            rfm_row = rfm_from_import_record(str(customer.id), record)

            if rfm_row is None:
                partial += 1
                continue

            rows.append(rfm_row.to_model_fields())

        written = 0

        if rows:
            written = AnalyticsRepository(self.session).upsert_import_rfm(
                business_id=business_id, rows=rows
            )
            logger.info(
                "Stored RFM values passed through from an upload",
                extra={"rows": written, "partial_rows_skipped": partial},
            )

        return {"rfm_rows_from_import": written, "rows_with_partial_rfm": partial}

    def import_customers(
        self,
        business_id: str,
        file_path: str | Path,
        source_type: str = "csv",
    ) -> dict[str, Any]:
        """Import customer data from a file.

        Args:
            business_id: Business ID
            file_path: Path to file
            source_type: Type of source (csv, excel, api)

        Returns:
            dict with import results
        """
        file_path = Path(file_path)
        file_name = file_path.name

        # Create import record
        import_record = self.import_service.create_import(
            business_id=business_id,
            dataset_type="customer",
            source_type=source_type,
            file_name=file_name,
        )

        try:
            # Mark as processing
            self.import_service.mark_processing(import_record.id)

            # Parse file and rename real-world headers to the canonical fields
            df, resolved = self._parse_and_map(file_path, "customer")

            logger.info(
                "import_validation_started",
                extra={"dataset": "customer", "row_count": len(df)},
            )
            # Validate data
            valid_records, errors = DataValidator.validate_customer_data(df)
            logger.info(
                "import_validation_completed",
                extra={
                    "dataset": "customer",
                    "rows_valid": len(valid_records),
                    # From the frame, not len(errors) — the error list is capped at
                    # ~100 entries so the response stays a sane size, which means
                    # counting it would under-report a badly broken file as "1 bad row".
                    "rows_invalid": len(df) - len(valid_records),
                },
            )

            logger.info(
                "import_database_started",
                extra={"dataset": "customer", "rows": len(valid_records)},
            )

            # Persist valid records
            if valid_records:
                customers = self.customer_service.bulk_upsert_customers(business_id, valid_records)
                self.session.commit()
            else:
                customers = []

            logger.info(
                "import_database_completed",
                extra={"dataset": "customer", "rows_written": len(customers)},
            )

            # Keep any genuine RFM columns the file carried.
            rfm_counts = self._store_import_rfm(business_id, df, customers)

            # Mark as completed
            self.import_service.mark_completed(import_record.id, row_count=len(valid_records))
            self.session.commit()

            return {
                "import_id": import_record.id,
                "status": "completed",
                "rows_processed": len(valid_records),
                # Counted, not returned. See the class docstring: putting the ORM
                # rows in the response body is what broke this endpoint.
                "rows_persisted": len(customers),
                "rows_failed": len(df) - len(valid_records),
                "errors": errors,
                **rfm_counts,
                **mapping_summary(resolved),
            }
        except ColumnMappingError as mapping_error:
            return self._mapping_failure(import_record.id, mapping_error)
        except Exception as e:
            error_msg = str(e)
            self.session.rollback()
            self.import_service.mark_failed(import_record.id, error_msg)
            self.session.commit()
            return {
                "import_id": import_record.id,
                "status": "failed",
                "error": error_msg,
            }

    def import_products(
        self,
        business_id: str,
        file_path: str | Path,
        source_type: str = "csv",
    ) -> dict[str, Any]:
        """Import product data from a file."""
        file_path = Path(file_path)
        file_name = file_path.name

        import_record = self.import_service.create_import(
            business_id=business_id,
            dataset_type="product",
            source_type=source_type,
            file_name=file_name,
        )

        try:
            self.import_service.mark_processing(import_record.id)

            df, resolved = self._parse_and_map(file_path, "product")

            logger.info(
                "import_validation_started",
                extra={"dataset": "product", "row_count": len(df)},
            )
            valid_records, errors = DataValidator.validate_product_data(df)
            logger.info(
                "import_validation_completed",
                extra={
                    "dataset": "product",
                    "rows_valid": len(valid_records),
                    "rows_invalid": len(df) - len(valid_records),
                },
            )

            logger.info(
                "import_database_started",
                extra={"dataset": "product", "rows": len(valid_records)},
            )

            if valid_records:
                products = self.product_service.bulk_upsert_products(business_id, valid_records)
                self.session.commit()
            else:
                products = []

            logger.info(
                "import_database_completed",
                extra={"dataset": "product", "rows_written": len(products)},
            )

            self.import_service.mark_completed(import_record.id, row_count=len(valid_records))
            self.session.commit()

            return {
                "import_id": import_record.id,
                "status": "completed",
                "rows_processed": len(valid_records),
                "rows_persisted": len(products),
                "rows_failed": len(df) - len(valid_records),
                "errors": errors,
                **mapping_summary(resolved),
            }
        except ColumnMappingError as mapping_error:
            return self._mapping_failure(import_record.id, mapping_error)
        except Exception as e:
            error_msg = str(e)
            self.session.rollback()
            self.import_service.mark_failed(import_record.id, error_msg)
            self.session.commit()
            return {
                "import_id": import_record.id,
                "status": "failed",
                "error": error_msg,
            }

    def import_inventory(
        self,
        business_id: str,
        file_path: str | Path,
        source_type: str = "csv",
    ) -> dict[str, Any]:
        """Import inventory data from a file."""
        file_path = Path(file_path)
        file_name = file_path.name

        import_record = self.import_service.create_import(
            business_id=business_id,
            dataset_type="inventory",
            source_type=source_type,
            file_name=file_name,
        )

        try:
            self.import_service.mark_processing(import_record.id)

            df, resolved = self._parse_and_map(file_path, "inventory")

            logger.info(
                "import_validation_started",
                extra={"dataset": "inventory", "row_count": len(df)},
            )
            valid_records, errors = DataValidator.validate_inventory_data(df)
            logger.info(
                "import_validation_completed",
                extra={
                    "dataset": "inventory",
                    "rows_valid": len(valid_records),
                    "rows_invalid": len(df) - len(valid_records),
                },
            )

            inventory_rows: list[dict[str, Any]] = []

            if valid_records:
                # Inventory is stored against the internal product id, so every row
                # has to be resolved from its external id first. That resolution was
                # a SELECT per row; now it is a handful of chunked IN queries.
                external_product_ids = [
                    str(record["external_product_id"]) for record in valid_records
                ]
                product_ids = self.product_service.product_ids_by_external_ids(
                    business_id, external_product_ids
                )

                # Unknown products are reported rather than invented. Unlike orders
                # (where a shell customer preserves real purchase history), an
                # inventory row for a product that does not exist carries no
                # information worth keeping — and guessing a product would corrupt
                # the catalogue. Import products first.
                missing: set[str] = set()

                for record in valid_records:
                    payload = dict(record)
                    external_product_id = str(payload.pop("external_product_id"))
                    product_id = product_ids.get(external_product_id)

                    if product_id is None:
                        missing.add(external_product_id)
                        continue

                    payload["product_id"] = product_id
                    inventory_rows.append(payload)

                if missing:
                    # One aggregated message instead of one per row: a file whose
                    # products were never imported would otherwise generate an error
                    # string per row and balloon the response.
                    sample = sorted(missing)[:10]
                    errors.append(
                        f"{len(missing)} product(s) referenced by this file are not in "
                        f"the catalogue and were skipped (e.g. {', '.join(sample)}). "
                        "Import the products file first, then retry."
                    )

            logger.info(
                "import_database_started",
                extra={"dataset": "inventory", "rows": len(inventory_rows)},
            )

            if inventory_rows:
                inventory = self.inventory_service.bulk_upsert_inventory(business_id, inventory_rows)
                self.session.commit()
            else:
                inventory = []

            logger.info(
                "import_database_completed",
                extra={"dataset": "inventory", "rows_written": len(inventory_rows)},
            )

            # A file that parsed cleanly but wrote nothing is not a successful
            # import, and calling it one is how "Imported successfully" ends up on
            # screen above an empty table. The usual cause is running this file
            # before the products file, so every external product id resolves to
            # nothing; say so, and record the import as failed, rather than
            # reporting completion.
            if valid_records and not inventory_rows:
                message = (
                    f"None of the {len(valid_records)} inventory rows could be matched "
                    "to a product in this business's catalogue, so nothing was written. "
                    "Import the products file first, then retry."
                )
                self.import_service.mark_failed(import_record.id, message)
                self.session.commit()
                return {
                    "import_id": import_record.id,
                    "status": "failed",
                    "rows_processed": 0,
                    "rows_persisted": 0,
                    "rows_failed": len(df),
                    "error": message,
                    "errors": errors,
                    **mapping_summary(resolved),
                }

            self.import_service.mark_completed(import_record.id, row_count=len(inventory_rows))
            self.session.commit()

            return {
                "import_id": import_record.id,
                "status": "completed",
                # Counted from what was actually written, not from what parsed.
                # Rows whose product could not be resolved were skipped, and
                # reporting them as processed would be a lie the dashboard inherits.
                "rows_processed": len(inventory_rows),
                "rows_persisted": len(inventory),
                "rows_failed": len(df) - len(inventory_rows),
                "errors": errors,
                **mapping_summary(resolved),
            }
        except ColumnMappingError as mapping_error:
            return self._mapping_failure(import_record.id, mapping_error)
        except Exception as e:
            error_msg = str(e)
            self.session.rollback()
            self.import_service.mark_failed(import_record.id, error_msg)
            self.session.commit()
            return {
                "import_id": import_record.id,
                "status": "failed",
                "error": error_msg,
            }

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def import_orders(
        self,
        business_id: str,
        file_path: str | Path,
        source_type: str = "csv",
    ) -> dict[str, Any]:
        """Import order data from a file.

        Orders reference customers by external id, so each row is resolved to an
        internal ``customer_id`` first. When the referenced customer is not yet in
        the database a shell row carrying only that external id is created.

        That choice is deliberate. Dropping the order instead would silently
        understate the customer's frequency and monetary value for as long as the
        data lives, and RFM computed from a partial order history is worse than
        useless because it looks authoritative. Nothing is invented: the external
        id comes from the file, and every other customer field stays empty until a
        customer import fills it in. The count is reported back so the caller can
        see it happened and import customers first next time.
        """
        file_path = Path(file_path)
        file_name = file_path.name

        import_record = self.import_service.create_import(
            business_id=business_id,
            dataset_type="order",
            source_type=source_type,
            file_name=file_name,
        )

        try:
            self.import_service.mark_processing(import_record.id)

            df, resolved = self._parse_and_map(file_path, "order")

            logger.info(
                "import_validation_started",
                extra={"dataset": "order", "row_count": len(df)},
            )
            valid_records, errors = DataValidator.validate_order_data(df)
            logger.info(
                "import_validation_completed",
                extra={
                    "dataset": "order",
                    "rows_valid": len(valid_records),
                    # Derived from the frame, not from len(errors): the error list is
                    # capped for response size, so counting it would under-report.
                    "rows_invalid": len(df) - len(valid_records),
                },
            )

            # ---- Resolve customers in bulk ------------------------------------
            # Every order references a customer by external id. This used to be a
            # SELECT (and sometimes an INSERT plus flush) per row inside the loop
            # below, so a 38,000-order file meant tens of thousands of sequential
            # round-trips before the first order was written.
            #
            # Note the name: the customer cache is `customer_ids`, not `resolved`.
            # It was previously called `resolved`, which shadowed the ResolvedColumns
            # returned by _parse_and_map above — so `mapping_summary(resolved)` at the
            # end of this method received a plain dict, raised AttributeError, and was
            # caught by the `except Exception` below. The orders had already been
            # committed by then, so a completely successful import reported
            # `status: "failed"` and no column mapping. That is fixed by keeping the
            # two names distinct.
            external_customer_ids = [
                str(record["external_customer_id"]) for record in valid_records
            ]
            customer_ids = self.customer_service.customer_ids_by_external_ids(
                business_id, external_customer_ids
            )

            unknown = [
                external_id
                for external_id in dict.fromkeys(external_customer_ids)
                if external_id not in customer_ids
            ]
            customers_created = 0

            if unknown:
                # Shell rows carrying only the external id, created in one batch.
                # Dropping these orders instead would understate the customer's
                # frequency and monetary value permanently.
                created = self.customer_service.bulk_upsert_customers(
                    business_id,
                    [{"external_customer_id": external_id} for external_id in unknown],
                )

                for customer in created:
                    customer_ids[str(customer.external_customer_id)] = str(customer.id)

                customers_created = len(created)

            order_rows: list[dict[str, Any]] = []

            for record in valid_records:
                payload = dict(record)
                external_customer_id = str(payload.pop("external_customer_id"))
                customer_id = customer_ids.get(external_customer_id)

                if customer_id is None:
                    # Only reachable if the batch insert silently dropped a row.
                    # Reported rather than crashing the whole import.
                    errors.append(
                        f"Could not resolve customer: {external_customer_id}"
                    )
                    continue

                payload["customer_id"] = customer_id
                order_rows.append(payload)

            logger.info(
                "import_database_started",
                extra={"dataset": "order", "rows": len(order_rows)},
            )

            if order_rows:
                orders = self.order_service.bulk_upsert_orders(business_id, order_rows)
                self.session.commit()
            else:
                orders = []

            logger.info(
                "import_database_completed",
                extra={
                    "dataset": "order",
                    "rows_written": len(order_rows),
                    "customers_created": customers_created,
                },
            )

            self.import_service.mark_completed(import_record.id, row_count=len(order_rows))
            self.session.commit()

            logger.info(
                "Order import finished",
                extra={
                    "rows_processed": len(order_rows),
                    "rows_failed": len(df) - len(order_rows),
                    "customers_created": customers_created,
                },
            )

            return {
                "import_id": import_record.id,
                "status": "completed",
                "rows_processed": len(order_rows),
                "rows_persisted": len(orders),
                "rows_failed": len(df) - len(order_rows),
                "errors": errors,
                "customers_created": customers_created,
                **mapping_summary(resolved),
            }
        except ColumnMappingError as mapping_error:
            return self._mapping_failure(import_record.id, mapping_error)
        except Exception as e:
            error_msg = str(e)
            self.session.rollback()
            self.import_service.mark_failed(import_record.id, error_msg)
            self.session.commit()
            return {
                "import_id": import_record.id,
                "status": "failed",
                "error": error_msg,
            }
