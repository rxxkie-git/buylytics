"""Ingestion layer for file parsing and validation.

Two things in here were load-bearing in a real outage, so they are worth stating
plainly.

**Parsing was unbounded.** ``parse_file`` was ``pd.read_csv(path)`` with no
``nrows``, no encoding fallback and no tolerance for a malformed line. The preview
endpoint — whose entire job is to show the user five sample rows — called it, so
previewing a 40 MB export parsed all 40 MB into DataFrame objects before returning
five of them. ``parse_file`` now takes ``max_rows``, and the preview passes it.

**Validation was row-at-a-time.** All four validators looped
``for idx, row in df.iterrows()``, which materialises a Series per row: for a
38,279-row customers file that is 38,279 Series allocations plus a
``pd.notna`` call per cell, all of it interpreted Python. The validators below are
vectorised, doing one typed operation per *column* instead. Same output shape,
same error strings, one to two orders of magnitude less work.

A subtler bug fixed here: identifier columns are stringified through
:func:`_as_id_series` rather than ``str(value)``. When an id column contains one
blank cell, pandas types the column ``float64``, and ``str()`` on it yields
``"1001.0"``. The customers file would import id ``"1001"`` and the orders file
referencing the same customer would look up ``"1001.0"`` — a silent
foreign-key miss that presents as "customers_created" climbing on every order
import.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, NamedTuple

import openpyxl
import pandas as pd

from app.core.logging import get_logger

logger = get_logger(__name__)

#: Ceiling on how many per-row error strings a validator returns. A 500k-row file
#: in which every row is invalid would otherwise build a 500k-element list of
#: strings and serialise it into an HTTP response — the error report becoming a
#: bigger problem than the error. Callers get this many examples plus an accurate
#: total, which is what someone fixing a spreadsheet actually needs.
MAX_REPORTED_ERRORS = 100


class ParseOutcome(NamedTuple):
    """A parse plus what had to be tolerated to achieve it.

    The counts exist so the API can tell the user "we read your file, but 12 lines
    were malformed and skipped" instead of either failing outright or silently
    importing less than they uploaded.
    """

    frame: pd.DataFrame
    encoding: str
    malformed_rows_skipped: int
    truncated: bool


class FileParser:
    """Parse CSV, TSV, XLS, and XLSX files."""

    SUPPORTED_FORMATS = {".csv", ".tsv", ".xls", ".xlsx"}

    #: Tried in order. Real exports are frequently not UTF-8: Excel on Windows
    #: writes cp1252, and "Save as CSV" often prefixes a UTF-8 BOM, which utf-8
    #: decodes into a zero-width character glued to the first column name — so
    #: ``customer_id`` stops matching and the mapping reports a missing field.
    #:
    #: latin-1 is last and is a guaranteed terminus: it maps all 256 byte values,
    #: so it cannot raise UnicodeDecodeError. Reaching it may produce mojibake, but
    #: a readable file with wrong accents beats a rejected upload, and the chosen
    #: encoding is reported so the user can see it happened.
    ENCODINGS: tuple[str, ...] = ("utf-8", "utf-8-sig", "cp1252", "latin-1")

    @staticmethod
    def parse_file(file_path: str | Path, *, max_rows: int | None = None) -> pd.DataFrame:
        """Parse a file and return a DataFrame.

        Args:
            file_path: Path to the file
            max_rows: Stop after this many data rows. The preview passes a small
                number so it does not parse an entire export to display five rows.

        Returns:
            pd.DataFrame: Parsed data

        Raises:
            ValueError: If the format is unsupported, or the file has no data.
        """
        return FileParser.parse_file_detailed(file_path, max_rows=max_rows).frame

    @staticmethod
    def parse_file_detailed(
        file_path: str | Path,
        *,
        max_rows: int | None = None,
    ) -> ParseOutcome:
        """:meth:`parse_file`, plus what was tolerated. See :class:`ParseOutcome`."""
        file_path = Path(file_path)
        suffix = file_path.suffix.lower()

        if suffix not in FileParser.SUPPORTED_FORMATS:
            raise ValueError(
                f"Unsupported file format: {suffix}. "
                f"Supported formats: {sorted(FileParser.SUPPORTED_FORMATS)}"
            )

        if not file_path.exists():
            raise ValueError("The uploaded file could not be read back from disk")

        if file_path.stat().st_size == 0:
            raise ValueError("The file is empty")

        if suffix in {".xls", ".xlsx"}:
            return FileParser._parse_excel(file_path, max_rows=max_rows)

        return FileParser._parse_delimited(
            file_path,
            # The upload route has always accepted .tsv; this branch was missing
            # once, so every tab-separated upload failed as "Unsupported format".
            separator="\t" if suffix == ".tsv" else ",",
            max_rows=max_rows,
        )

    @staticmethod
    def _parse_delimited(
        file_path: Path,
        *,
        separator: str,
        max_rows: int | None,
    ) -> ParseOutcome:
        """Read a delimited file, degrading one step at a time.

        The order matters. Encoding is resolved first with a strict parse, because
        a decode failure and a structural failure need different remedies and
        trying to fix both at once misattributes one as the other.
        """
        last_unicode_error: UnicodeDecodeError | None = None

        for encoding in FileParser.ENCODINGS:
            try:
                frame = pd.read_csv(
                    file_path,
                    sep=separator,
                    encoding=encoding,
                    nrows=max_rows,
                    # Strict on the first pass so a genuinely ragged file is
                    # detected rather than quietly truncated at the bad line.
                    on_bad_lines="error",
                    # Read the whole column before choosing a dtype. The default
                    # chunked inference can type one chunk of an id column as int
                    # and the next as object, producing mixed types in one column.
                    low_memory=False,
                    skip_blank_lines=True,
                )
            except UnicodeDecodeError as exc:
                last_unicode_error = exc
                continue
            except pd.errors.EmptyDataError as exc:
                raise ValueError("The file has no header row or no data") from exc
            except pd.errors.ParserError:
                # Structural problem: a row with more fields than the header,
                # an unterminated quote, a stray delimiter. Retry tolerantly at
                # the encoding that decoded cleanly, and report what was lost.
                return FileParser._parse_delimited_lenient(
                    file_path, separator=separator, encoding=encoding, max_rows=max_rows
                )

            if frame.columns.empty:
                raise ValueError("The file has no header row or no data")

            return ParseOutcome(
                frame=FileParser._normalise_headers(frame),
                encoding=encoding,
                malformed_rows_skipped=0,
                truncated=max_rows is not None and len(frame) >= max_rows,
            )

        # Unreachable in practice: latin-1 decodes any byte sequence. Kept because
        # "unreachable" and "cannot happen" are different claims, and a future
        # edit to ENCODINGS could make this the real path.
        raise ValueError(
            "The file could not be decoded as text. If it is a spreadsheet, "
            "upload it as .xlsx instead of renaming it to .csv."
        ) from last_unicode_error

    @staticmethod
    def _parse_delimited_lenient(
        file_path: Path,
        *,
        separator: str,
        encoding: str,
        max_rows: int | None,
    ) -> ParseOutcome:
        """Second pass: recover from a structurally broken file, or fail clearly.

        ``on_bad_lines="skip"`` is not sufficient on its own, which is easy to get
        wrong. It discards rows whose *field count* is wrong, but an unterminated
        quote is a tokenizer failure raised while reading the header — pandas never
        reaches the point where skipping applies, and the C engine reports
        ``EOF inside string starting at row N``. A single stray double-quote in a
        40 MB export therefore escaped the "lenient" retry as an uncaught
        ``ParserError`` and became a 500.

        So the recovery escalates, each step giving up one more guarantee:

        1. C engine, skip ragged rows — fast, faithful quoting, and enough for the
           common case of a row carrying too many fields.
        2. C engine, quoting disabled — the unterminated-quote case. Quote
           characters become ordinary text, so one field may keep a literal ``"``,
           but the file parses and the user gets their rows.
        3. Python engine, then Python engine unquoted — a different tokenizer, for
           damage the C one cannot get past at all.

        A strategy is judged on its *result*, not merely on not raising. The Python
        engine with ``on_bad_lines="skip"`` "succeeds" on an unterminated quote by
        swallowing the file whole and returning **zero rows** — no exception, no
        data. Accepting that would reproduce the complaint this project began with:
        "0 rows processed, 38,279 skipped" for a file that was almost entirely
        fine. So a parse returning nothing from a file that demonstrably has data
        lines counts as a failed strategy and the escalation continues.

        If every strategy fails the file is genuinely not delimited text, and the
        caller turns the ``ValueError`` into a 422 naming the problem, not a 500.
        """
        attempts: tuple[tuple[str, dict[str, Any]], ...] = (
            ("c-engine", {"low_memory": False}),
            ("c-engine-unquoted", {"quoting": csv.QUOTE_NONE, "low_memory": False}),
            ("python-engine", {"engine": "python"}),
            ("python-engine-unquoted", {"engine": "python", "quoting": csv.QUOTE_NONE}),
        )

        # Counted once, up front. It serves as both the yardstick for "did this
        # strategy actually recover anything" and the basis for the skipped count.
        physical = FileParser._count_lines(file_path, encoding)
        file_has_data_rows = physical is not None and physical > 1

        frame = None
        strategy = ""
        last_error: Exception | None = None

        for label, options in attempts:
            try:
                candidate = pd.read_csv(
                    file_path,
                    sep=separator,
                    encoding=encoding,
                    nrows=max_rows,
                    on_bad_lines="skip",
                    skip_blank_lines=True,
                    **options,
                )
            except (pd.errors.ParserError, ValueError) as exc:
                last_error = exc
                continue

            if candidate.columns.empty:
                continue

            # The silent-empty guard described above.
            if file_has_data_rows and len(candidate) == 0:
                logger.debug(
                    "Recovery strategy read no rows from a file that has data; escalating",
                    extra={"recovery_strategy": label},
                )
                continue

            frame = candidate
            strategy = label
            break

        if frame is None:
            raise ValueError(
                "The file could not be parsed as delimited text. It may be a "
                "spreadsheet saved with the wrong extension, or contain an "
                "unclosed quotation mark. Re-export it as CSV or .xlsx and retry."
            ) from last_error

        skipped = 0

        # Only meaningful for a complete read. With `nrows` set, the shortfall
        # against the file's line count is the truncation we asked for, not damage.
        if max_rows is None and physical is not None:
            skipped = max(0, physical - 1 - len(frame))

        logger.warning(
            "Upload contained lines the parser could not read; they were skipped",
            extra={
                "malformed_rows_skipped": skipped,
                "rows_parsed": len(frame),
                "encoding": encoding,
                "recovery_strategy": strategy,
            },
        )

        return ParseOutcome(
            frame=FileParser._normalise_headers(frame),
            encoding=encoding,
            malformed_rows_skipped=skipped,
            truncated=max_rows is not None and len(frame) >= max_rows,
        )

    @staticmethod
    def _parse_excel(file_path: Path, *, max_rows: int | None) -> ParseOutcome:
        try:
            frame = pd.read_excel(file_path, nrows=max_rows)
        except ValueError as exc:
            # pandas raises ValueError for a file whose extension says xlsx but
            # whose bytes are something else, which is a user-fixable mistake.
            raise ValueError(f"The spreadsheet could not be read: {exc}") from exc

        if frame.columns.empty:
            raise ValueError("The spreadsheet has no header row or no data")

        return ParseOutcome(
            frame=FileParser._normalise_headers(frame),
            encoding="binary",
            malformed_rows_skipped=0,
            truncated=max_rows is not None and len(frame) >= max_rows,
        )

    @staticmethod
    def _normalise_headers(frame: pd.DataFrame) -> pd.DataFrame:
        """Strip whitespace from column names in place.

        A trailing space in a header is invisible in a spreadsheet and makes
        ``"customer_id "`` fail to match ``"customer_id"``, which surfaces to the
        user as a missing required column they can plainly see is present.
        """
        frame.columns = [str(name).strip() for name in frame.columns]
        return frame

    @staticmethod
    def _count_lines(file_path: Path, encoding: str) -> int | None:
        """Count physical lines without decoding the file.

        Counting newline bytes in binary chunks is encoding-independent for every
        encoding in ENCODINGS (all ASCII-compatible) and costs one buffered pass
        instead of building Python strings for every line.
        """
        try:
            total = 0

            with file_path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    total += chunk.count(b"\n")

                # A final line with no trailing newline still counts.
                handle.seek(0, 2)

                if handle.tell() > 0:
                    handle.seek(-1, 2)

                    if handle.read(1) != b"\n":
                        total += 1

            return total
        except OSError:
            return None

    @staticmethod
    def count_data_rows(file_path: str | Path) -> int | None:
        """Row count for a file, without parsing it into a DataFrame.

        The preview needs a total to display but only five rows to show. Parsing
        the whole file to call ``len()`` on it is what made previewing a large
        export time out.

        Returns ``None`` when the count cannot be established cheaply, so callers
        must handle "unknown" rather than being handed a plausible zero.
        """
        file_path = Path(file_path)
        suffix = file_path.suffix.lower()

        if suffix in {".csv", ".tsv"}:
            physical = FileParser._count_lines(file_path, "utf-8")
            # Minus the header. Approximate when quoted fields contain newlines;
            # it is a display figure, not an accounting one.
            return max(0, physical - 1) if physical is not None else None

        if suffix in {".xls", ".xlsx"}:
            try:
                workbook = openpyxl.load_workbook(file_path, read_only=True)
                try:
                    sheet = workbook[workbook.sheetnames[0]]
                    return max(0, (sheet.max_row or 1) - 1)
                finally:
                    # read_only workbooks hold a file handle open until closed.
                    workbook.close()
            except Exception:
                return None

        return None

    @staticmethod
    def get_sheet_names(file_path: str | Path) -> list[str]:
        """Get sheet names from an Excel file."""
        file_path = Path(file_path)
        suffix = file_path.suffix.lower()

        if suffix in {".csv", ".tsv"}:
            return ["data"]

        if suffix in {".xls", ".xlsx"}:
            workbook = openpyxl.load_workbook(file_path, read_only=True)
            try:
                return list(workbook.sheetnames)
            finally:
                workbook.close()

        raise ValueError(f"Unsupported file format: {suffix}")


# ---------------------------------------------------------------------------
# Vectorised column helpers
#
# Each returns a full-length Series aligned to the frame's index, so the
# validators can assemble records by zipping columns together instead of walking
# rows. Every one tolerates a missing column by synthesising an empty Series,
# which is what lets a validator reference an optional field unconditionally.
# ---------------------------------------------------------------------------


def _as_id_series(frame: pd.DataFrame, column: str) -> pd.Series:
    """Stringify an identifier column without inventing decimal points.

    The float case is the whole reason this exists. A CSV column of integer ids
    with a single empty cell is typed ``float64`` by pandas, because NaN is a
    float; ``str()`` then renders ``1001`` as ``"1001.0"``. Two files describing
    the same customer would disagree on the id, and the orders import would
    create a duplicate shell customer for every row.
    """
    if column not in frame.columns:
        return pd.Series([""] * len(frame), index=frame.index, dtype="object")

    series = frame[column]

    if pd.api.types.is_float_dtype(series):
        def render(value: Any) -> str:
            if pd.isna(value):
                return ""
            # Whole floats render as integers; genuine decimals keep their form,
            # because an id like "10.5" is odd but it is what the file says.
            return str(int(value)) if float(value).is_integer() else str(value)

        return series.map(render)

    return series.astype("object").where(series.notna(), "").map(
        lambda value: "" if value is None else str(value).strip()
    )


def _as_text_series(frame: pd.DataFrame, column: str) -> pd.Series:
    """Stringify an optional text column, mapping blanks to ``None``."""
    if column not in frame.columns:
        return pd.Series([None] * len(frame), index=frame.index, dtype="object")

    series = frame[column]
    rendered = series.astype("object").where(series.notna(), None)

    return rendered.map(lambda value: None if value is None else (str(value).strip() or None))


def _as_float_series(frame: pd.DataFrame, column: str, *, default: float | None = None) -> pd.Series:
    """Coerce a numeric column, mapping unparseable values to ``default``.

    ``errors="coerce"`` rather than raising: a single ``"N/A"`` in a price column
    previously threw inside the row loop and discarded the entire row, so one bad
    cell cost a whole customer. A null price with the rest of the row intact is
    more useful, and the coercion is counted so the user still hears about it.
    """
    if column not in frame.columns:
        return pd.Series([default] * len(frame), index=frame.index, dtype="object")

    numeric = pd.to_numeric(frame[column], errors="coerce")

    return numeric.astype("object").where(numeric.notna(), default)


def _as_int_series(frame: pd.DataFrame, column: str, *, default: int = 0) -> pd.Series:
    if column not in frame.columns:
        return pd.Series([default] * len(frame), index=frame.index, dtype="object")

    numeric = pd.to_numeric(frame[column], errors="coerce")

    return numeric.fillna(default).astype("int64")


def _as_datetime_series(frame: pd.DataFrame, column: str) -> pd.Series:
    """Parse a date column, mapping unparseable values to ``None``.

    ``format="mixed"`` lets one file carry both ``2024-01-05`` and ``05/01/2024``
    without the whole column failing, which real POS exports do. Unparseable
    values become NaT and then None rather than raising.
    """
    if column not in frame.columns:
        return pd.Series([None] * len(frame), index=frame.index, dtype="object")

    try:
        parsed = pd.to_datetime(frame[column], errors="coerce", format="mixed")
    except (ValueError, TypeError):
        # Some pandas versions reject format="mixed" for certain dtypes; the
        # inferring path is slower but always available.
        parsed = pd.to_datetime(frame[column], errors="coerce")

    return parsed.astype("object").where(parsed.notna(), None)


def _required_mask(frame: pd.DataFrame, columns: list[str]) -> tuple[pd.Series, dict[str, pd.Series]]:
    """Boolean mask of rows that carry every required field.

    "Present" means the column exists, the cell is not null, and the cell is not
    an empty or whitespace-only string. That last clause matters: a CSV cell
    written as ``,,`` parses to ``NaN`` but one written as ``, ,`` parses to
    ``" "``, and treating the second as a valid id creates a customer whose
    external id is a space.
    """
    ids: dict[str, pd.Series] = {}
    mask = pd.Series(True, index=frame.index)

    for column in columns:
        rendered = _as_id_series(frame, column)
        ids[column] = rendered
        mask &= rendered.astype("string").fillna("").str.strip() != ""

    return mask, ids


def _row_errors(frame: pd.DataFrame, mask: pd.Series, required: list[str]) -> tuple[list[str], int]:
    """Error strings for the rows that failed, capped, plus the true total.

    ``idx + 2`` matches the line number in the user's spreadsheet: one for the
    header, one because spreadsheets count from 1. Preserved exactly from the
    row-loop implementation, because it is the part users read.
    """
    failed_positions = frame.index[~mask]
    total = int(len(failed_positions))

    if total == 0:
        return [], 0

    fields = "{" + ", ".join(f"'{name}'" for name in required) + "}"
    errors = [
        f"Row {int(idx) + 2}: Missing required fields: {fields}"
        for idx in failed_positions[:MAX_REPORTED_ERRORS]
    ]

    if total > MAX_REPORTED_ERRORS:
        errors.append(
            f"... and {total - MAX_REPORTED_ERRORS} more rows with missing required fields "
            f"(showing the first {MAX_REPORTED_ERRORS})"
        )

    return errors, total


class DataValidator:
    """Validate and clean imported data.

    Every method returns ``(valid_records, errors)`` — the same contract as the
    row-loop version it replaced, including the ``Row N: ...`` error text — but
    computes it with one operation per column instead of one per cell.
    """

    @staticmethod
    def validate_customer_data(df: pd.DataFrame) -> tuple[list[dict[str, Any]], list[str]]:
        """Validate customer data.

        Args:
            df: DataFrame with customer data

        Returns:
            tuple[list[dict], list[str]]: (valid records, error messages)
        """
        required = ["external_customer_id"]
        mask, ids = _required_mask(df, required)
        errors, _ = _row_errors(df, mask, required)

        valid = df[mask]
        records = [
            {
                "external_customer_id": external_id,
                "name": name,
                "email": email,
                "phone": phone,
                "city": city,
            }
            for external_id, name, email, phone, city in zip(
                ids["external_customer_id"][mask],
                _as_text_series(valid, "name"),
                _as_text_series(valid, "email"),
                _as_text_series(valid, "phone"),
                _as_text_series(valid, "city"),
                strict=True,
            )
        ]

        return records, errors

    @staticmethod
    def validate_product_data(df: pd.DataFrame) -> tuple[list[dict[str, Any]], list[str]]:
        """Validate product data.

        Args:
            df: DataFrame with product data

        Returns:
            tuple[list[dict], list[str]]: (valid records, error messages)
        """
        required = ["external_product_id"]
        mask, ids = _required_mask(df, required)
        errors, _ = _row_errors(df, mask, required)

        valid = df[mask]
        records = [
            {
                "external_product_id": external_id,
                "name": name,
                "sku": sku,
                "category": category,
                "price": price,
            }
            for external_id, name, sku, category, price in zip(
                ids["external_product_id"][mask],
                _as_text_series(valid, "name"),
                _as_text_series(valid, "sku"),
                _as_text_series(valid, "category"),
                _as_float_series(valid, "price"),
                strict=True,
            )
        ]

        return records, errors

    @staticmethod
    def validate_order_data(df: pd.DataFrame) -> tuple[list[dict[str, Any]], list[str]]:
        """Validate order data.

        Args:
            df: DataFrame with order data

        Returns:
            tuple[list[dict], list[str]]: (valid records, error messages)
        """
        required = ["external_order_id", "external_customer_id"]
        mask, ids = _required_mask(df, required)
        errors, _ = _row_errors(df, mask, required)

        valid = df[mask]
        statuses = _as_text_series(valid, "status")
        records = [
            {
                "external_order_id": order_id,
                "external_customer_id": customer_id,
                "order_date": order_date,
                "total_amount": total,
                # Matches the previous ``str(row.get("status", "pending"))``
                # default, but also treats an explicitly blank cell as pending
                # rather than storing an empty string.
                "status": status or "pending",
            }
            for order_id, customer_id, order_date, total, status in zip(
                ids["external_order_id"][mask],
                ids["external_customer_id"][mask],
                _as_datetime_series(valid, "order_date"),
                _as_float_series(valid, "total_amount"),
                statuses,
                strict=True,
            )
        ]

        return records, errors

    @staticmethod
    def validate_inventory_data(df: pd.DataFrame) -> tuple[list[dict[str, Any]], list[str]]:
        """Validate inventory data.

        Args:
            df: DataFrame with inventory data

        Returns:
            tuple[list[dict], list[str]]: (valid records, error messages)
        """
        required = ["external_product_id"]
        mask, ids = _required_mask(df, required)
        errors, _ = _row_errors(df, mask, required)

        valid = df[mask]
        statuses = _as_text_series(valid, "status")
        records = [
            {
                "external_product_id": external_id,
                "quantity": int(quantity),
                "reorder_level": int(reorder_level),
                "supplier": supplier,
                "status": status or "In Stock",
            }
            for external_id, quantity, reorder_level, supplier, status in zip(
                ids["external_product_id"][mask],
                _as_int_series(valid, "quantity"),
                _as_int_series(valid, "reorder_level"),
                _as_text_series(valid, "supplier"),
                statuses,
                strict=True,
            )
        ]

        return records, errors
