/**
 * Inventory.
 *
 * Two things this page must not do. It must not compute stock health itself — the
 * threshold lives in backend configuration and the `/inventory-summary` endpoint
 * reports it, so recomputing "low stock" in the browser against a hardcoded 10 would
 * disagree with the dashboard the moment someone changed the setting. And it must not
 * treat a missing reorder level as zero, because "no reorder point set" and "reorder
 * at zero" lead to opposite decisions.
 *
 * Available quantity is shown alongside quantity on hand. Reserved stock is committed
 * to orders, so a product with 40 units and 38 reserved is nearly out even though the
 * raw count looks comfortable — the `status` column comes from the server, and the
 * available figure is what explains it.
 */

import { useMemo, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Grid from "@mui/material/Grid";
import LinearProgress from "@mui/material/LinearProgress";
import Stack from "@mui/material/Stack";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import CloudUploadRoundedIcon from "@mui/icons-material/CloudUploadRounded";
import Inventory2RoundedIcon from "@mui/icons-material/Inventory2Rounded";
import RefreshRoundedIcon from "@mui/icons-material/RefreshRounded";

import { ROUTES } from "../../app/navigation.js";
import { catalogApi, dashboardApi } from "../../api/index.js";
import useApiResource from "../../hooks/useApiResource.js";
import AsyncBoundary, { EmptyBlock } from "../../components/ui/AsyncBoundary.jsx";
import RecordTable from "../../components/ui/RecordTable.jsx";
import SectionCard from "../../components/ui/SectionCard.jsx";
import StatCard from "../../components/ui/StatCard.jsx";
import { formatCount, formatDateTime, formatRelative, formatShare } from "../../utils/format.js";

const VIEWS = {
  ALL: "all",
  ATTENTION: "attention",
  OUT: "out",
};

/** The server sets `status`; this only chooses a colour for whatever it said. */
const STATUS_COLOR = {
  "Out of Stock": "error",
  "Low Stock": "warning",
  "In Stock": "success",
};

const statusColor = (status) => STATUS_COLOR[status] ?? "default";

const InventoryPage = () => {
  const [view, setView] = useState(VIEWS.ALL);

  const inventory = useApiResource((options) => catalogApi.fetchInventory(options));
  const products = useApiResource((options) => catalogApi.fetchProducts(options));
  const summary = useApiResource((options) => dashboardApi.fetchInventorySummary(options));

  const refreshAll = () => {
    inventory.refresh();
    products.refresh();
    summary.refresh();
  };

  const productNames = useMemo(() => {
    const index = new Map();
    for (const product of products.data?.products ?? []) {
      index.set(String(product.id), product);
    }
    return index;
  }, [products.data]);

  // The threshold comes from the server so the page and the dashboard agree.
  const threshold = summary.data?.threshold ?? null;

  const rows = useMemo(() => {
    const items = inventory.data?.inventory ?? [];

    return items.map((item) => {
      const product = productNames.get(String(item.product_id)) ?? null;
      const quantity = Number(item.quantity ?? 0);
      const reserved = Number(item.reserved_quantity ?? 0);

      return {
        ...item,
        productName: product?.name || product?.sku || item.product_id,
        sku: product?.sku ?? null,
        category: product?.category ?? null,
        available: quantity - reserved,
        // Null, not zero: an unset reorder level is not a reorder level of zero.
        reorderLevel:
          item.reorder_level === null || item.reorder_level === undefined
            ? null
            : Number(item.reorder_level),
      };
    });
  }, [inventory.data, productNames]);

  const visibleRows = useMemo(() => {
    if (view === VIEWS.OUT) return rows.filter((row) => row.status === "Out of Stock");
    if (view === VIEWS.ATTENTION) {
      return rows.filter((row) => row.status === "Out of Stock" || row.status === "Low Stock");
    }
    return rows;
  }, [rows, view]);

  const columns = useMemo(
    () => [
      {
        id: "productName",
        label: "Product",
        render: (row) => (
          <Stack spacing={0.25} sx={{ minWidth: 0 }}>
            <Typography variant="body2" fontWeight={600} noWrap>
              {row.productName}
            </Typography>
            {row.sku ? (
              <Typography variant="caption" color="text.secondary" sx={{ fontFamily: "monospace" }}>
                {row.sku}
              </Typography>
            ) : null}
          </Stack>
        ),
      },
      { id: "category", label: "Category" },
      {
        id: "status",
        label: "Status",
        render: (row) => (
          <Chip size="small" label={row.status ?? "Unknown"} color={statusColor(row.status)} variant="outlined" />
        ),
      },
      {
        id: "quantity",
        label: "On hand",
        numeric: true,
        render: (row) => formatCount(row.quantity),
      },
      {
        id: "reserved_quantity",
        label: "Reserved",
        numeric: true,
        render: (row) => formatCount(row.reserved_quantity ?? 0),
      },
      {
        id: "available",
        label: "Available",
        numeric: true,
        render: (row) => (
          <Tooltip title="On hand minus reserved. This is what can actually be sold.">
            <Typography
              variant="body2"
              fontWeight={700}
              color={row.available <= 0 ? "error.main" : "text.primary"}
              sx={{ fontVariantNumeric: "tabular-nums" }}
            >
              {formatCount(row.available)}
            </Typography>
          </Tooltip>
        ),
      },
      {
        id: "reorderLevel",
        label: "Reorder at",
        numeric: true,
        render: (row) =>
          row.reorderLevel === null ? (
            <Tooltip title="No reorder level set for this item — it is not the same as a reorder level of zero.">
              <Typography variant="caption" color="text.disabled">
                not set
              </Typography>
            </Tooltip>
          ) : (
            formatCount(row.reorderLevel)
          ),
      },
      { id: "supplier", label: "Supplier" },
      {
        id: "updated_at",
        label: "Updated",
        render: (row) => (
          <Tooltip title={formatDateTime(row.updated_at)}>
            <span>{formatRelative(row.updated_at)}</span>
          </Tooltip>
        ),
      },
    ],
    []
  );

  const s = summary.data;
  const tracked = s?.tracked_products ?? rows.length;
  const healthyShare = tracked ? Number(s?.healthy ?? 0) / tracked : null;

  return (
    <Stack spacing={3}>
      <Stack direction="row" justifyContent="space-between" alignItems="center" spacing={2}>
        <Typography variant="caption" color="text.secondary">
          {threshold === null ? " " : `Low-stock threshold: ${formatCount(threshold)} units (set server-side)`}
        </Typography>
        <Button
          size="small"
          startIcon={<RefreshRoundedIcon />}
          onClick={refreshAll}
          disabled={inventory.isLoading || inventory.isRefreshing}
        >
          Refresh
        </Button>
      </Stack>

      <AsyncBoundary
        isLoading={inventory.isLoading}
        error={inventory.error}
        onRetry={inventory.reload}
        isEmpty={rows.length === 0}
        empty={
          <EmptyBlock
            icon={<Inventory2RoundedIcon color="primary" sx={{ fontSize: 46 }} />}
            title="Nothing tracked yet"
            description="Import products first, then an inventory file — rows are matched to products by external_product_id."
            action={
              <Button
                component={RouterLink}
                to={ROUTES.IMPORT}
                variant="contained"
                startIcon={<CloudUploadRoundedIcon />}
              >
                Import inventory
              </Button>
            }
          />
        }
      >
        <Stack spacing={3}>
          <Grid container spacing={2.5}>
            <Grid item xs={12} sm={6} md={3}>
              <StatCard label="Tracked products" value={tracked} format="count" tone="primary" />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <StatCard
                label="Out of stock"
                value={s?.out_of_stock}
                format="count"
                tone={Number(s?.out_of_stock ?? 0) > 0 ? "danger" : "success"}
                caption={Number(s?.out_of_stock ?? 0) > 0 ? "Cannot be sold right now" : "Nothing at zero"}
              />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <StatCard
                label="Low stock"
                value={s?.low_stock}
                format="count"
                tone={Number(s?.low_stock ?? 0) > 0 ? "warning" : "success"}
                hint={
                  threshold === null
                    ? "Counted server-side against the configured threshold."
                    : `At or below ${threshold} units, per the server's configured threshold.`
                }
              />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <StatCard label="Total units" value={s?.total_units} format="count" tone="info" />
            </Grid>
          </Grid>

          {summary.error ? (
            <Alert severity="warning">
              Stock rows loaded, but the summary endpoint did not: {summary.error.message}. The tiles
              above fall back to counting the rows on this page.
            </Alert>
          ) : null}

          {healthyShare !== null ? (
            <SectionCard title="Stock health" subtitle="Share of tracked products in each state">
              <Stack spacing={1.5}>
                <Stack direction="row" justifyContent="space-between">
                  <Typography variant="caption" color="text.secondary">
                    {formatCount(s?.healthy)} healthy of {formatCount(tracked)} tracked
                  </Typography>
                  <Typography variant="caption" fontWeight={700}>
                    {formatShare(healthyShare)}
                  </Typography>
                </Stack>
                <Box sx={{ display: "flex", height: 12, borderRadius: 6, overflow: "hidden", bgcolor: "action.hover" }}>
                  {[
                    { key: "healthy", value: s?.healthy, color: "#16A34A" },
                    { key: "low", value: s?.low_stock, color: "#EA580C" },
                    { key: "out", value: s?.out_of_stock, color: "#DC2626" },
                  ].map((band) => {
                    const share = tracked ? (Number(band.value ?? 0) / tracked) * 100 : 0;
                    if (share <= 0) return null;
                    return <Box key={band.key} sx={{ width: `${share}%`, bgcolor: band.color }} />;
                  })}
                </Box>
                {/* A determinate bar is kept for screen readers and narrow layouts,
                    where the stacked band above collapses to something unreadable. */}
                <LinearProgress
                  variant="determinate"
                  value={Math.min(100, Math.max(0, healthyShare * 100))}
                  sx={{ display: { xs: "block", sm: "none" }, height: 6, borderRadius: 3 }}
                />
              </Stack>
            </SectionCard>
          ) : null}

          <SectionCard
            title="Stock levels"
            subtitle={
              view === VIEWS.ALL
                ? `${formatCount(rows.length)} items`
                : `${formatCount(visibleRows.length)} of ${formatCount(rows.length)} items`
            }
            isRefreshing={inventory.isRefreshing || products.isRefreshing}
            action={
              <ToggleButtonGroup size="small" exclusive value={view} onChange={(_, next) => next && setView(next)}>
                <ToggleButton value={VIEWS.ALL}>All</ToggleButton>
                <ToggleButton value={VIEWS.ATTENTION}>Needs attention</ToggleButton>
                <ToggleButton value={VIEWS.OUT}>Out of stock</ToggleButton>
              </ToggleButtonGroup>
            }
          >
            <RecordTable
              columns={columns}
              rows={visibleRows}
              getRowId={(row) => row.id}
              initialSortBy="available"
              initialSortDirection="asc"
              searchable
              searchPlaceholder="Search product, SKU, category or supplier"
              searchKeys={["productName", "sku", "category", "supplier", "status"]}
              emptyMessage={
                view === VIEWS.OUT
                  ? "Nothing is out of stock."
                  : view === VIEWS.ATTENTION
                    ? "Every tracked item is above its reorder point."
                    : "No items match."
              }
            />
          </SectionCard>
        </Stack>
      </AsyncBoundary>
    </Stack>
  );
};

export default InventoryPage;
