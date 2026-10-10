/**
 * Data import.
 *
 * Four datasets, one upload flow. The ordering on screen is deliberate: orders are
 * listed first and described as the import that makes RFM real, because a customers
 * file can only ever pass recency/frequency/monetary columns through on trust,
 * whereas orders let the backend measure them. A user who imports only customers
 * and then wonders why their segments look thin has been failed by the UI, not the
 * data.
 *
 * **Files are not required to use the internal column names.** Selecting a file
 * calls `POST /imports/{dataset}/preview`, which reads the header row and answers
 * with the `file column -> application field` mapping it derived — so the user sees
 * that `Order ID` was understood as `external_order_id` *before* uploading, and
 * sees exactly which field has no candidate column when one is missing. The page
 * previously listed `external_order_id` and friends as the required headers, which
 * told users to go and edit their exports for no reason.
 *
 * Validation happens twice on purpose. `validateFile` mirrors the backend's
 * extension allow-list and 50 MB cap so a bad file is rejected in a millisecond
 * rather than after a long upload; the server checks the same things because a
 * client-side check is a courtesy, not a control.
 *
 * The result panel reports `rows_failed` and the per-row errors rather than
 * summarising them away. A partial import is the normal outcome of a real
 * spreadsheet, and the rows that did not make it are the only ones worth looking at.
 * A column-mapping refusal is rendered as its own thing — naming the field, the
 * headers that would satisfy it and the columns the file actually has — because
 * "0 rows processed, 38,279 skipped" for a header problem is a lie about the data.
 */

import { useCallback, useRef, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Collapse from "@mui/material/Collapse";
import Grid from "@mui/material/Grid";
import LinearProgress from "@mui/material/LinearProgress";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import Typography from "@mui/material/Typography";
import CheckCircleRoundedIcon from "@mui/icons-material/CheckCircleRounded";
import CloudUploadRoundedIcon from "@mui/icons-material/CloudUploadRounded";
import GroupsRoundedIcon from "@mui/icons-material/GroupsRounded";
import Inventory2RoundedIcon from "@mui/icons-material/Inventory2Rounded";
import LocalMallRoundedIcon from "@mui/icons-material/LocalMallRounded";
import PlayArrowRoundedIcon from "@mui/icons-material/PlayArrowRounded";
import ReceiptLongRoundedIcon from "@mui/icons-material/ReceiptLongRounded";

import { ROUTES } from "../../app/navigation.js";
import { importsApi } from "../../api/index.js";
import SectionCard from "../../components/ui/SectionCard.jsx";
import StatCard from "../../components/ui/StatCard.jsx";
import { formatCount } from "../../utils/format.js";

const { ACCEPT_ATTRIBUTE, ACCEPTED_EXTENSIONS, DATASETS, MAX_UPLOAD_BYTES } = importsApi;

/**
 * What each dataset needs, described the way a user's spreadsheet describes it.
 *
 * `examples` are real headers the backend's alias tables accept — they are a
 * sample, not the whole list, and the authoritative mapping for a specific file
 * comes back from the preview endpoint. Only the external id is genuinely required
 * for each dataset; everything else is read with `row.get(...)` and left null when
 * absent, so listing them as two groups tells the truth about what a file must
 * contain versus what it can contain.
 *
 * Deliberately no longer the canonical `external_*` names: those are the internal
 * field names, they remain the database columns, and asking a user to rename their
 * export to match them was the whole problem.
 */
const DATASET_CARDS = [
  {
    key: DATASETS.ORDERS,
    title: "Orders",
    icon: ReceiptLongRoundedIcon,
    summary: "The import that makes RFM real — recency, frequency and monetary are measured from these rows.",
    required: [
      { label: "Order ID", examples: ["order_id", "Order ID", "order_number", "invoice_no"] },
      { label: "Customer ID", examples: ["customer_id", "Customer ID", "buyer_id", "client_id"] },
    ],
    optional: [
      { label: "Order date", examples: ["order_date", "Date", "created_at"] },
      { label: "Order total", examples: ["total_amount", "Amount", "grand_total"] },
      { label: "Status", examples: ["status", "Financial Status"] },
    ],
    note: "A referenced customer who is not on file yet is created carrying only that external id, and the count comes back as customers_created. Dropping the order instead would understate that customer's value permanently. Rows without an order date or total import, but contribute nothing to recency or monetary.",
    tone: "primary",
  },
  {
    key: DATASETS.CUSTOMERS,
    title: "Customers",
    icon: GroupsRoundedIcon,
    summary: "Names, contact details and city. Optionally carries RFM columns.",
    required: [{ label: "Customer ID", examples: ["customer_id", "Customer ID", "client_id"] }],
    optional: [
      { label: "Name", examples: ["name", "Customer Name", "full_name"] },
      { label: "Email", examples: ["email", "Email Address"] },
      { label: "Phone", examples: ["phone", "Mobile"] },
      { label: "City", examples: ["city", "Town"] },
      { label: "Recency", examples: ["recency", "days_since_last_order"] },
      { label: "Frequency", examples: ["frequency", "order_count"] },
      { label: "Monetary", examples: ["monetary", "Total Spend", "lifetime_value"] },
    ],
    note: "All three of recency, frequency and monetary must be present and usable for a row to count — a file with only some of them is reported as rows_with_partial_rfm and ignored rather than half-filled. Passed-through values are used only for customers with no measured orders; they never overwrite a measured score.",
    tone: "info",
  },
  {
    key: DATASETS.PRODUCTS,
    title: "Products",
    icon: LocalMallRoundedIcon,
    summary: "Catalogue: SKU, category and price.",
    required: [{ label: "Product ID", examples: ["product_id", "SKU", "Item Code"] }],
    optional: [
      { label: "Name", examples: ["name", "Product Name", "title"] },
      { label: "SKU", examples: ["sku", "SKU"] },
      { label: "Category", examples: ["category", "Product Type"] },
      { label: "Price", examples: ["price", "Unit Price"] },
    ],
    note: "A catalogue keyed only by SKU is fine: that one column satisfies both the product id and the SKU field.",
    tone: "info",
  },
  {
    key: DATASETS.INVENTORY,
    title: "Inventory",
    icon: Inventory2RoundedIcon,
    summary: "Stock on hand and reorder levels.",
    required: [{ label: "Product ID", examples: ["product_id", "SKU", "Item Code"] }],
    optional: [
      { label: "Quantity", examples: ["quantity", "Qty On Hand", "stock"] },
      { label: "Reorder level", examples: ["reorder_level", "Reorder Point"] },
      { label: "Supplier", examples: ["supplier", "Vendor"] },
      { label: "Status", examples: ["status"] },
    ],
    note: "Rows are matched to products by their product id, so import products first.",
    tone: "info",
  },
];

const megabytes = (bytes) => `${(bytes / (1024 * 1024)).toFixed(1)} MB`;

/**
 * A field the file does not supply, and the headers that would supply it.
 *
 * `accepted_columns` is truncated by the backend to the first handful, which is
 * the right call: a list of fifteen synonyms reads as noise, and the user only
 * needs one of them.
 */
const MissingFields = ({ fields }) => (
  <Stack spacing={0.75}>
    {fields.map((field) => (
      <Box key={field.field}>
        <Typography variant="body2" fontWeight={700}>
          No column supplies {field.label}
        </Typography>
        <Typography variant="caption" sx={{ display: "block" }}>
          Rename a column to any of:{" "}
          <Box component="span" sx={{ fontFamily: "monospace" }}>
            {field.accepted_columns.join(", ")}
          </Box>
        </Typography>
      </Box>
    ))}
  </Stack>
);

/** Two columns mean the same field. Reported, never resolved by guessing. */
const AmbiguousFields = ({ fields }) => (
  <Stack spacing={0.75}>
    {fields.map((field) => (
      <Box key={field.field}>
        <Typography variant="body2" fontWeight={700}>
          {field.label} is ambiguous
        </Typography>
        <Typography variant="caption" sx={{ display: "block" }}>
          <Box component="span" sx={{ fontFamily: "monospace" }}>
            {field.columns.join(", ")}
          </Box>{" "}
          all mean the same thing. Remove or rename one.
        </Typography>
      </Box>
    ))}
  </Stack>
);

/**
 * Everything the server said about a file it cannot map, in one block.
 *
 * Shared by the pre-upload preview and the post-upload failure path, because the
 * payload shape is identical — the import endpoint returns the same structure the
 * preview does, so a mapping refusal reads the same whichever way it is reached.
 */
const MappingProblem = ({ payload }) => (
  <Stack spacing={1.25}>
    {payload.missing_fields?.length ? <MissingFields fields={payload.missing_fields} /> : null}
    {payload.ambiguous_fields?.length ? <AmbiguousFields fields={payload.ambiguous_fields} /> : null}

    {payload.available_columns?.length ? (
      <Box>
        <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
          Columns found in your file
        </Typography>
        <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap sx={{ mt: 0.5 }}>
          {payload.available_columns.map((column) => (
            <Chip
              key={column}
              size="small"
              label={column}
              variant="outlined"
              sx={{ fontFamily: "monospace" }}
            />
          ))}
        </Stack>
      </Box>
    ) : null}
  </Stack>
);

/**
 * Application fields the file supplies no column for, so they will be empty.
 *
 * The mirror image of `unmapped_columns`, and the more important half. "Ignored:
 * order_approved_at" is a shrug; "Order total will be empty for every row" is the
 * reason the revenue tiles read zero after an import that reported success. This
 * project's own `dataset/df_Orders.csv` has no amount column of any kind, so it
 * imports 89,316 perfectly valid orders worth nothing at all — which is fine, as
 * long as the screen says so instead of leaving it to be discovered on the
 * dashboard.
 */
const EmptyFields = ({ fields }) =>
  fields?.length ? (
    <Typography variant="caption" color="warning.main" sx={{ display: "block" }}>
      No column found for {fields.map((entry) => entry.label).join(", ")} — left empty for every row.
    </Typography>
  ) : null;

/** The `file column -> application field` table shown before the user commits. */
const MappingTable = ({ preview }) => (
  <Stack spacing={1}>
    <Typography variant="caption" color="text.secondary">
      {formatCount(preview.row_count)} rows · these columns were recognised
    </Typography>

    <Table size="small" sx={{ "& td, & th": { px: 1, py: 0.4, border: 0 } }}>
      <TableHead>
        <TableRow>
          <TableCell sx={{ fontSize: 11, color: "text.secondary" }}>Your column</TableCell>
          <TableCell sx={{ fontSize: 11, color: "text.secondary" }}>Imported as</TableCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {preview.column_mapping.map((entry) => (
          <TableRow key={entry.field}>
            <TableCell sx={{ fontFamily: "monospace", fontSize: 12 }}>{entry.source}</TableCell>
            <TableCell sx={{ fontSize: 12 }}>{entry.label}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>

    <EmptyFields fields={preview.fields_without_columns} />

    {preview.unmapped_columns?.length ? (
      <Typography variant="caption" color="text.secondary">
        Ignored: {preview.unmapped_columns.join(", ")}
      </Typography>
    ) : null}
  </Stack>
);

/**
 * One dataset's upload card. Local state per card, so uploading orders does not
 * blank out the customers result sitting next to it.
 */
const UploadCard = ({ card, onImported }) => {
  const inputRef = useRef(null);

  const [file, setFile] = useState(null);
  const [validationError, setValidationError] = useState(null);
  const [preview, setPreview] = useState(null);
  const [previewError, setPreviewError] = useState(null);
  const [isPreviewing, setIsPreviewing] = useState(false);
  const [progress, setProgress] = useState(null);
  const [isUploading, setIsUploading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [showErrors, setShowErrors] = useState(false);

  const Icon = card.icon;

  const choose = async (nextFile) => {
    setResult(null);
    setError(null);
    setShowErrors(false);
    setPreview(null);
    setPreviewError(null);

    if (!nextFile) {
      setFile(null);
      setValidationError(null);
      return;
    }

    const problem = importsApi.validateFile(nextFile);
    setValidationError(problem);
    setFile(problem ? null : nextFile);

    if (problem) return;

    // Read the header row server-side before committing to an import. This
    // writes nothing, so it is safe on every selection.
    setIsPreviewing(true);
    try {
      setPreview(await importsApi.previewDataset(card.key, nextFile));
    } catch (caught) {
      // A failed preview must not block the upload: the mapping is applied
      // server-side during the import too, so the worst case is that the user
      // uploads without having seen the table first.
      setPreviewError(caught);
    } finally {
      setIsPreviewing(false);
    }
  };

  const send = useCallback(async () => {
    if (!file) return;

    setIsUploading(true);
    setProgress(0);
    setError(null);
    setResult(null);

    try {
      const payload = await importsApi.uploadDataset(card.key, file, {
        onProgress: (percent) => setProgress(percent),
      });

      setResult(payload);
      onImported?.(card.key, payload);

      // The file input keeps its value after a successful send, so re-selecting the
      // same file would be a no-op and look broken. Clearing both fixes that.
      setFile(null);
      setPreview(null);
      if (inputRef.current) inputRef.current.value = "";
    } catch (caught) {
      setError(caught);
    } finally {
      setIsUploading(false);
      setProgress(null);
    }
  }, [file, card.key, onImported]);

  const failedRows = Number(result?.rows_failed ?? 0);
  const rowErrors = result?.errors ?? [];
  const isUnmappable = preview?.status === "unmappable";
  const mappingFailed = result?.error_code === "column_mapping_failed";


  return (
    <Paper variant="outlined" sx={{ borderRadius: 3, p: 2.5, height: "100%" }}>
      <Stack spacing={2} sx={{ height: "100%" }}>
        <Stack direction="row" spacing={1.5} alignItems="flex-start">
          <Box
            sx={{
              width: 40,
              height: 40,
              borderRadius: 2,
              display: "grid",
              placeItems: "center",
              color: card.tone === "primary" ? "primary.main" : "info.main",
              bgcolor: "action.hover",
              flexShrink: 0,
            }}
          >
            <Icon />
          </Box>
          <Box sx={{ minWidth: 0 }}>
            <Typography variant="subtitle1" fontWeight={800}>
              {card.title}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {card.summary}
            </Typography>
          </Box>
        </Stack>

        <Box>
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 0.5 }}>
            Required — your file needs a column for each of these, under any recognised name
          </Typography>
          <Stack spacing={0.5}>
            {card.required.map((field) => (
              <Box key={field.label}>
                <Chip size="small" label={field.label} color="primary" variant="outlined" />
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ ml: 0.75, fontFamily: "monospace" }}
                >
                  {field.examples.join(", ")}…
                </Typography>
              </Box>
            ))}
          </Stack>

          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1.25, mb: 0.5 }}>
            Optional — left empty when absent
          </Typography>
          <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
            {card.optional.map((field) => (
              <Chip
                key={field.label}
                size="small"
                label={field.label}
                variant="outlined"
                title={`Recognised as: ${field.examples.join(", ")}`}
                sx={{ color: "text.secondary" }}
              />
            ))}
          </Stack>

          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1.25 }}>
            Capitalisation, spaces and hyphens do not matter. Pick a file and the exact mapping is
            shown before anything is imported.
          </Typography>
        </Box>

        {card.note ? (
          <Typography variant="caption" color="text.secondary">
            {card.note}
          </Typography>
        ) : null}

        <Box sx={{ flexGrow: 1 }} />

        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT_ATTRIBUTE}
          hidden
          onChange={(event) => choose(event.target.files?.[0] ?? null)}
        />

        <Stack direction="row" spacing={1} alignItems="center">
          <Button
            variant="outlined"
            size="small"
            onClick={() => inputRef.current?.click()}
            disabled={isUploading}
          >
            Choose file
          </Button>
          <Typography variant="caption" color="text.secondary" noWrap sx={{ minWidth: 0, flex: 1 }}>
            {file ? `${file.name} · ${megabytes(file.size)}` : "No file selected"}
          </Typography>
          <Button
            variant="contained"
            size="small"
            startIcon={<CloudUploadRoundedIcon />}
            onClick={send}
            // Blocked only when the server has already said the file cannot be
            // mapped. Uploading it would produce a failure and no rows.
            disabled={!file || isUploading || isPreviewing || isUnmappable}
          >
            {preview?.status === "ready" ? "Confirm import" : "Upload"}
          </Button>
        </Stack>

        {isPreviewing ? (
          <Stack direction="row" spacing={1} alignItems="center">
            <CircularProgress size={14} />
            <Typography variant="caption" color="text.secondary">
              Reading the header row…
            </Typography>
          </Stack>
        ) : null}

        {preview?.status === "ready" ? (
          <Alert severity="success" icon={<CheckCircleRoundedIcon fontSize="inherit" />}>
            <AlertTitle sx={{ fontSize: 14 }}>Columns recognised</AlertTitle>
            <MappingTable preview={preview} />
          </Alert>
        ) : null}

        {isUnmappable ? (
          <Alert severity="error">
            <AlertTitle sx={{ fontSize: 14 }}>This file cannot be imported yet</AlertTitle>
            <MappingProblem payload={preview} />
          </Alert>
        ) : null}

        {previewError ? (
          <Alert severity="warning" onClose={() => setPreviewError(null)}>
            Could not preview the columns ({previewError.message}). You can still upload — the same
            mapping runs server-side during the import.
          </Alert>
        ) : null}

        {isUploading ? (
          <Box>
            <LinearProgress
              // A null percentage means the browser could not report total size;
              // an indeterminate bar is honest, a fake 50% is not.
              variant={progress === null ? "indeterminate" : "determinate"}
              value={progress ?? 0}
              sx={{ height: 6, borderRadius: 3 }}
            />
            <Typography variant="caption" color="text.secondary">
              {progress === null ? "Uploading…" : `${progress}% uploaded`}
              {progress === 100 ? " — processing on the server" : ""}
            </Typography>
          </Box>
        ) : null}

        {validationError ? (
          <Alert severity="warning" onClose={() => setValidationError(null)}>
            {validationError}
          </Alert>
        ) : null}

        {error ? (
          <Alert severity="error" onClose={() => setError(null)}>
            <AlertTitle>Upload failed</AlertTitle>
            {error.message}
            {/*
              A 500 body is generic on purpose, so `message` alone used to be the
              whole story: "An unexpected error occurred", with no way to tell a
              serialization failure from a dead database. The exception class and
              the request id are both safe to show — one is source code, the other
              is a correlation id — and together they turn this alert into
              something an operator can act on.
            */}
            {error.exceptionType || error.requestId ? (
              <Typography variant="caption" sx={{ display: "block", mt: 0.5, fontFamily: "monospace" }}>
                {[
                  error.exceptionType ? `${error.exceptionType} on the server` : null,
                  error.requestId ? `request ${error.requestId}` : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </Typography>
            ) : null}
          </Alert>
        ) : null}

        {result ? (
          <Alert
            severity={result.status === "failed" ? "error" : failedRows > 0 ? "warning" : "success"}
            icon={result.status === "failed" ? undefined : <CheckCircleRoundedIcon fontSize="inherit" />}
          >
            <AlertTitle>
              {mappingFailed
                ? "Columns could not be matched"
                : result.status === "failed"
                  ? "Import failed"
                  : failedRows > 0
                    ? "Imported with skipped rows"
                    : "Imported"}
            </AlertTitle>

            {mappingFailed ? (
              // Not a data problem, so it must not be reported as skipped rows.
              // Nothing was written; the file needs one column renamed.
              <Stack spacing={1}>
                <Typography variant="body2">
                  No rows were imported and nothing was changed. This is a column-naming problem,
                  not a problem with your data.
                </Typography>
                <MappingProblem payload={result} />
              </Stack>
            ) : result.status === "failed" ? (
              <Typography variant="body2">{result.error ?? "The server rejected the file."}</Typography>
            ) : (
              <Stack spacing={0.25}>
                <Typography variant="body2">
                  {formatCount(result.rows_processed)} rows processed
                  {failedRows > 0 ? `, ${formatCount(failedRows)} skipped` : ""}
                </Typography>

                {/*
                  Rows written can legitimately be lower than rows processed: an
                  import upserts, so a file listing the same product_id twenty
                  times becomes one row. Saying only "3,000 processed" when the
                  products table grew by 2,420 invites someone to go looking for
                  580 lost rows that were never lost. Shown only when the two
                  numbers disagree, so the common case stays quiet.
                */}
                {Number.isFinite(result.rows_persisted) &&
                result.rows_persisted !== result.rows_processed ? (
                  <Typography variant="caption">
                    {formatCount(result.rows_persisted)} rows written — the rest updated existing
                    records rather than adding new ones
                  </Typography>
                ) : null}

                {/*
                  The single most important line in this panel when it appears.
                  An import can be entirely successful and still leave a field
                  empty for every row because the file had no column for it, and
                  the consequence shows up much later as a dashboard tile reading
                  zero. Saying it here, next to the word "Imported", is the whole
                  point of `fields_without_columns`.
                */}
                <EmptyFields fields={result.fields_without_columns} />

                {result.customers_created > 0 ? (
                  <Typography variant="caption">
                    {formatCount(result.customers_created)} customers created from order references
                  </Typography>
                ) : null}

                {result.rfm_rows_from_import > 0 ? (
                  <Typography variant="caption">
                    {formatCount(result.rfm_rows_from_import)} rows carried RFM columns and were kept as
                    passed-through values
                  </Typography>
                ) : null}

                {result.rows_with_partial_rfm > 0 ? (
                  <Typography variant="caption">
                    {formatCount(result.rows_with_partial_rfm)} rows had only some RFM columns and were
                    ignored rather than half-filled
                  </Typography>
                ) : null}

                {result.import_id ? (
                  <Typography variant="caption" sx={{ fontFamily: "monospace", opacity: 0.75 }}>
                    import {result.import_id}
                  </Typography>
                ) : null}
              </Stack>
            )}

            {rowErrors.length ? (
              <Box sx={{ mt: 1 }}>
                <Button size="small" color="inherit" onClick={() => setShowErrors((open) => !open)}>
                  {showErrors ? "Hide" : `Show ${rowErrors.length} row problem${rowErrors.length === 1 ? "" : "s"}`}
                </Button>
                <Collapse in={showErrors}>
                  <Box
                    sx={{
                      mt: 1,
                      maxHeight: 180,
                      overflow: "auto",
                      fontFamily: "monospace",
                      fontSize: 12,
                    }}
                  >
                    {rowErrors.map((rowError, index) => (
                      <Typography key={index} variant="caption" sx={{ display: "block" }}>
                        {typeof rowError === "string" ? rowError : JSON.stringify(rowError)}
                      </Typography>
                    ))}
                  </Box>
                </Collapse>
              </Box>
            ) : null}
          </Alert>
        ) : null}
      </Stack>
    </Paper>
  );
};

const ImportPage = () => {
  const [imported, setImported] = useState({});

  const handleImported = useCallback((dataset, payload) => {
    setImported((current) => ({ ...current, [dataset]: payload }));
  }, []);

  const importedOrders = Boolean(imported[DATASETS.ORDERS]);
  const importedAnything = Object.keys(imported).length > 0;

  const totalRows = Object.values(imported).reduce(
    (sum, payload) => sum + Number(payload?.rows_processed ?? 0),
    0
  );

  return (
    <Stack spacing={3}>
      <Alert severity="info" icon={false}>
        <Stack direction={{ xs: "column", md: "row" }} spacing={2} justifyContent="space-between">
          <Box>
            <AlertTitle sx={{ fontSize: 14 }}>How importing works</AlertTitle>
            <Typography variant="body2">
              Files are read, validated row by row and written to your business only — nothing is
              shared across tenants. Accepted formats are {ACCEPTED_EXTENSIONS.join(", ")} up to{" "}
              {megabytes(MAX_UPLOAD_BYTES)}. Valid rows are saved even when other rows fail, so a
              messy spreadsheet still gets you most of the way.
            </Typography>
            <Typography variant="body2" sx={{ mt: 1 }}>
              Export straight from your store or POS — column names do not need editing.{" "}
              <Box component="span" sx={{ fontFamily: "monospace" }}>
                order_id
              </Box>
              ,{" "}
              <Box component="span" sx={{ fontFamily: "monospace" }}>
                Order ID
              </Box>{" "}
              and{" "}
              <Box component="span" sx={{ fontFamily: "monospace" }}>
                Order Number
              </Box>{" "}
              are all understood. When you pick a file you get the exact column mapping to check
              before anything is imported, and if a required field has no matching column the file is
              rejected with that field named rather than imported as zero rows.
            </Typography>
          </Box>
        </Stack>
      </Alert>

      {importedAnything ? (
        <Grid container spacing={2.5}>
          <Grid item xs={12} sm={6} md={3}>
            <StatCard label="Rows imported this session" value={totalRows} format="count" tone="success" />
          </Grid>
          <Grid item xs={12} sm={6} md={9}>
            <Paper variant="outlined" sx={{ borderRadius: 3, p: 2.5, height: "100%" }}>
              <Typography variant="subtitle2" fontWeight={700}>
                Next step
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                {importedOrders
                  ? "Orders are in. Queue a full refresh to measure RFM from them and fit segments — imported rows do not analyse themselves."
                  : "Importing records does not compute anything on its own. Queue an analytics job when you are ready, or import orders first for measured RFM rather than passed-through values."}
              </Typography>
              <Stack direction="row" spacing={1.5} sx={{ mt: 2 }}>
                <Button
                  component={RouterLink}
                  to={ROUTES.JOBS}
                  variant="contained"
                  size="small"
                  startIcon={<PlayArrowRoundedIcon />}
                >
                  Queue analysis
                </Button>
                <Button component={RouterLink} to={ROUTES.CUSTOMERS} variant="outlined" size="small">
                  View customers
                </Button>
              </Stack>
            </Paper>
          </Grid>
        </Grid>
      ) : null}

      <SectionCard
        title="Upload a file"
        subtitle="Each dataset is independent — upload in any order, as often as you like. Rows are matched on their external id, so re-uploading updates rather than duplicates."
      >
        <Grid container spacing={2.5}>
          {DATASET_CARDS.map((card) => (
            <Grid item xs={12} md={6} key={card.key}>
              <UploadCard card={card} onImported={handleImported} />
            </Grid>
          ))}
        </Grid>
      </SectionCard>
    </Stack>
  );
};

export default ImportPage;
