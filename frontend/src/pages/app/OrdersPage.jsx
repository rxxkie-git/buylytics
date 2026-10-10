/**
 * Orders.
 *
 * The only page that pages server-side, because a tenant with a year of trading has
 * far more orders than customers and the endpoint caps `limit` at 500 regardless of
 * what is asked for. So `RecordTable` runs with `disableInternalPagination` and this
 * page owns the controls: sorting and searching apply to the page in hand, which the
 * caption says out loud rather than letting someone assume they searched everything.
 *
 * The status filter options come from `statuses` in the response — the distinct
 * values actually present in this tenant's data. A hardcoded list would offer filters
 * that match nothing, and would miss whatever a webhook or an import invented.
 *
 * `total_amount` is a string from `Numeric(12,2)`. Row totals are summed as numbers
 * only for the "shown on this page" caption, which is a display aid; the revenue
 * figure people act on comes from the dashboard's SQL aggregate, not from adding up
 * a hundred rows in JavaScript.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Dialog from "@mui/material/Dialog";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Divider from "@mui/material/Divider";
import Grid from "@mui/material/Grid";
import IconButton from "@mui/material/IconButton";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TablePagination from "@mui/material/TablePagination";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import CloseRoundedIcon from "@mui/icons-material/CloseRounded";
import CloudUploadRoundedIcon from "@mui/icons-material/CloudUploadRounded";
import ReceiptLongRoundedIcon from "@mui/icons-material/ReceiptLongRounded";
import RefreshRoundedIcon from "@mui/icons-material/RefreshRounded";

import { ROUTES } from "../../app/navigation.js";
import { catalogApi } from "../../api/index.js";
import useApiResource from "../../hooks/useApiResource.js";
import AsyncBoundary, { EmptyBlock } from "../../components/ui/AsyncBoundary.jsx";
import RecordTable from "../../components/ui/RecordTable.jsx";
import SectionCard from "../../components/ui/SectionCard.jsx";
import StatCard from "../../components/ui/StatCard.jsx";
import {
  formatCount,
  formatCurrencyPrecise,
  formatDateTime,
  formatRelative,
  shortId,
} from "../../utils/format.js";

const PAGE_SIZES = [25, 50, 100, 250];

const STATUS_COLOR = {
  completed: "success",
  delivered: "success",
  paid: "success",
  shipped: "info",
  processing: "info",
  pending: "warning",
  cancelled: "error",
  refunded: "error",
  failed: "error",
};

const statusColor = (status) => STATUS_COLOR[String(status ?? "").toLowerCase()] ?? "default";

/** Line items for one order, fetched on demand when the dialog opens. */
const OrderDetailDialog = ({ orderId, onClose }) => {
  const detail = useApiResource((options) => catalogApi.fetchOrder(orderId, options), {
    // Guarded rather than conditionally called: hooks cannot be skipped, so the
    // request is disabled until there is an id to fetch.
    enabled: Boolean(orderId),
    deps: [orderId],
  });

  const order = detail.data;

  const itemColumns = useMemo(
    () => [
      {
        id: "product_id",
        label: "Product",
        render: (row) => (
          <Typography variant="body2" sx={{ fontFamily: "monospace" }} title={row.product_id}>
            {shortId(row.product_id)}
          </Typography>
        ),
      },
      { id: "quantity", label: "Qty", numeric: true },
      {
        id: "unit_price",
        label: "Unit price",
        numeric: true,
        sortValue: (row) => Number(row.unit_price),
        render: (row) => formatCurrencyPrecise(row.unit_price),
      },
      {
        id: "total_amount",
        label: "Line total",
        numeric: true,
        sortValue: (row) => Number(row.total_amount),
        render: (row) => formatCurrencyPrecise(row.total_amount),
      },
    ],
    []
  );

  return (
    <Dialog open={Boolean(orderId)} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle sx={{ pr: 6 }}>
        Order {order?.external_order_id ?? shortId(orderId)}
        <IconButton onClick={onClose} sx={{ position: "absolute", right: 12, top: 12 }} size="small">
          <CloseRoundedIcon />
        </IconButton>
      </DialogTitle>

      <DialogContent dividers>
        <AsyncBoundary
          isLoading={detail.isLoading}
          error={detail.error}
          onRetry={detail.reload}
          loadingLabel="Loading line items…"
        >
          {order ? (
            <Stack spacing={2.5}>
              <Grid container spacing={2}>
                <Grid item xs={6} md={3}>
                  <Typography variant="caption" color="text.secondary" display="block">
                    Placed
                  </Typography>
                  <Typography variant="body2" fontWeight={600}>
                    {formatDateTime(order.order_date ?? order.created_at)}
                  </Typography>
                </Grid>
                <Grid item xs={6} md={3}>
                  <Typography variant="caption" color="text.secondary" display="block">
                    Status
                  </Typography>
                  <Chip size="small" label={order.status ?? "unknown"} color={statusColor(order.status)} />
                </Grid>
                <Grid item xs={6} md={3}>
                  <Typography variant="caption" color="text.secondary" display="block">
                    Total
                  </Typography>
                  <Typography variant="body2" fontWeight={700}>
                    {formatCurrencyPrecise(order.total_amount)}
                  </Typography>
                </Grid>
                <Grid item xs={6} md={3}>
                  <Typography variant="caption" color="text.secondary" display="block">
                    Customer
                  </Typography>
                  <Typography variant="body2" sx={{ fontFamily: "monospace" }} title={order.customer_id}>
                    {shortId(order.customer_id)}
                  </Typography>
                </Grid>
              </Grid>

              <Divider />

              {order.items?.length ? (
                <RecordTable
                  columns={itemColumns}
                  rows={order.items}
                  getRowId={(row) => row.id}
                  initialSortBy="total_amount"
                  initialSortDirection="desc"
                  disableInternalPagination
                  maxHeight={340}
                />
              ) : (
                <Alert severity="info">
                  This order has no line items on file. The order total is still counted — the orders
                  import records header rows even when the file carries no item detail.
                </Alert>
              )}
            </Stack>
          ) : null}
        </AsyncBoundary>
      </DialogContent>
    </Dialog>
  );
};

const OrdersPage = () => {
  const [status, setStatus] = useState("");
  const [pageSize, setPageSize] = useState(100);
  const [page, setPage] = useState(0);
  const [openOrderId, setOpenOrderId] = useState(null);

  const orders = useApiResource(
    (options) =>
      catalogApi.fetchOrders({
        limit: pageSize,
        offset: page * pageSize,
        status: status || undefined,
        ...options,
      }),
    { deps: [status, pageSize, page] }
  );

  const data = orders.data;
  const rows = data?.orders ?? [];
  const total = data?.total ?? 0;

  // Changing the filter while on page 4 would otherwise request an offset past the
  // end of the filtered set and show an empty table that looks like a failure.
  const changeStatus = useCallback((next) => {
    setStatus(next);
    setPage(0);
  }, []);

  useEffect(() => {
    // The same hazard from the other direction: a deletion elsewhere can shrink
    // `total` below the current offset between two loads.
    if (total > 0 && page * pageSize >= total) setPage(0);
  }, [total, page, pageSize]);

  const pageTotal = useMemo(
    () => rows.reduce((sum, row) => sum + Number(row.total_amount ?? 0), 0),
    [rows]
  );

  const withoutItems = useMemo(() => rows.filter((row) => Number(row.item_count ?? 0) === 0).length, [rows]);

  const columns = useMemo(
    () => [
      {
        id: "external_order_id",
        label: "Order",
        render: (row) => (
          <Typography variant="body2" fontWeight={600} sx={{ fontFamily: "monospace" }}>
            {row.external_order_id ?? shortId(row.id)}
          </Typography>
        ),
      },
      {
        id: "customer_name",
        label: "Customer",
        render: (row) =>
          row.customer_name ? (
            <Typography variant="body2" noWrap>
              {row.customer_name}
            </Typography>
          ) : (
            <Tooltip title="This customer was created from an order reference and has no name on file yet.">
              <Typography variant="caption" sx={{ fontFamily: "monospace", color: "text.secondary" }}>
                {shortId(row.customer_id)}
              </Typography>
            </Tooltip>
          ),
      },
      {
        id: "order_date",
        label: "Placed",
        render: (row) => (
          <Tooltip title={formatDateTime(row.order_date ?? row.created_at)}>
            <span>{formatRelative(row.order_date ?? row.created_at)}</span>
          </Tooltip>
        ),
      },
      {
        id: "status",
        label: "Status",
        render: (row) => (
          <Chip size="small" label={row.status ?? "unknown"} color={statusColor(row.status)} variant="outlined" />
        ),
      },
      {
        id: "item_count",
        label: "Items",
        numeric: true,
        render: (row) => formatCount(row.item_count ?? 0),
      },
      {
        id: "total_amount",
        label: "Total",
        numeric: true,
        sortValue: (row) => (row.total_amount === null ? null : Number(row.total_amount)),
        render: (row) => formatCurrencyPrecise(row.total_amount),
      },
    ],
    []
  );

  const statuses = data?.statuses ?? [];
  const isFirstLoad = orders.isLoading && !data;

  return (
    <Stack spacing={3}>
      <Stack direction="row" justifyContent="space-between" alignItems="center" spacing={2}>
        <Typography variant="caption" color="text.secondary">
          {orders.lastUpdatedAt ? `Updated ${formatRelative(orders.lastUpdatedAt)}` : " "}
        </Typography>
        <Button
          size="small"
          startIcon={orders.isRefreshing ? <CircularProgress size={14} /> : <RefreshRoundedIcon />}
          onClick={() => orders.refresh()}
          disabled={orders.isLoading || orders.isRefreshing}
        >
          Refresh
        </Button>
      </Stack>

      <AsyncBoundary
        isLoading={isFirstLoad}
        error={orders.error}
        onRetry={orders.reload}
        isEmpty={total === 0 && !status}
        empty={
          <EmptyBlock
            icon={<ReceiptLongRoundedIcon color="primary" sx={{ fontSize: 46 }} />}
            title="No orders yet"
            description="Orders are what make RFM measured rather than assumed. Import them, or send them in over the webhook endpoint."
            action={
              <Button
                component={RouterLink}
                to={ROUTES.IMPORT}
                variant="contained"
                startIcon={<CloudUploadRoundedIcon />}
              >
                Import orders
              </Button>
            }
          />
        }
      >
        <Stack spacing={3}>
          <Grid container spacing={2.5}>
            <Grid item xs={12} sm={6} md={3}>
              <StatCard
                label={status ? `Orders (${status})` : "Orders"}
                value={total}
                format="count"
                tone="primary"
              />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <StatCard
                label="Value on this page"
                value={pageTotal}
                format="currency"
                tone="success"
                caption={`Sum of the ${formatCount(rows.length)} rows shown`}
                hint="Deliberately not a business total. The dashboard's revenue figure is aggregated in SQL across every order."
              />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <StatCard label="Distinct statuses" value={statuses.length} format="count" tone="info" />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <StatCard
                label="No line items"
                value={withoutItems}
                format="count"
                tone={withoutItems > 0 ? "warning" : "neutral"}
                caption="On this page"
                hint="Header-only orders. Their totals still count toward revenue and monetary value; only per-product analysis is affected."
              />
            </Grid>
          </Grid>

          <SectionCard
            title="Transactions"
            subtitle={
              total > rows.length
                ? `Showing ${formatCount(page * pageSize + 1)}–${formatCount(
                    page * pageSize + rows.length
                  )} of ${formatCount(total)}. Sorting and search apply to this page only.`
                : `${formatCount(rows.length)} orders`
            }
            isRefreshing={orders.isRefreshing || orders.isLoading}
            action={
              <TextField
                select
                size="small"
                label="Status"
                value={status}
                onChange={(event) => changeStatus(event.target.value)}
                sx={{ minWidth: 170 }}
              >
                <MenuItem value="">All statuses</MenuItem>
                {statuses.map((option) => (
                  <MenuItem key={option} value={option}>
                    {option}
                  </MenuItem>
                ))}
              </TextField>
            }
          >
            <RecordTable
              columns={columns}
              rows={rows}
              getRowId={(row) => row.id}
              initialSortBy="order_date"
              initialSortDirection="desc"
              searchable
              searchPlaceholder="Search this page by order ID, customer or status"
              searchKeys={["external_order_id", "customer_name", "status"]}
              disableInternalPagination
              onRowClick={(row) => setOpenOrderId(row.id)}
              emptyMessage={
                status ? `No orders with status “${status}” in this page.` : "No orders on this page."
              }
              footer={
                <Box sx={{ borderTop: "1px solid", borderColor: "divider", mt: 1 }}>
                  <TablePagination
                    component="div"
                    count={total}
                    page={page}
                    onPageChange={(_, next) => setPage(next)}
                    rowsPerPage={pageSize}
                    onRowsPerPageChange={(event) => {
                      setPageSize(Number(event.target.value));
                      setPage(0);
                    }}
                    rowsPerPageOptions={PAGE_SIZES}
                    labelDisplayedRows={({ from, to, count }) =>
                      `${formatCount(from)}–${formatCount(to)} of ${formatCount(count)}`
                    }
                  />
                </Box>
              }
            />

            <Typography variant="caption" color="text.secondary">
              Select a row to see its line items.
            </Typography>
          </SectionCard>
        </Stack>
      </AsyncBoundary>

      {openOrderId ? <OrderDetailDialog orderId={openOrderId} onClose={() => setOpenOrderId(null)} /> : null}
    </Stack>
  );
};

export default OrdersPage;
