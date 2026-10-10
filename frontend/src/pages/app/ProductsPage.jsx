/**
 * Products.
 *
 * A catalogue view, joined in the browser with the inventory read so a product's
 * stock state is visible without switching pages — that pairing is the question
 * people actually have about a catalogue row.
 *
 * `price` arrives as a string from a `Numeric(10,2)` column. It is formatted through
 * `formatCurrencyPrecise` and only coerced to a number for aggregate arithmetic,
 * which is the narrow place where losing the last cent to binary floating point does
 * not change a decision. It is never round-tripped back to the API.
 *
 * There is no "add product" button. Products arrive through imports and webhooks;
 * offering a create form here would imply a write endpoint that does not exist, and
 * a disabled button that never becomes enabled is worse than no button.
 */

import { useMemo } from "react";
import { Link as RouterLink } from "react-router-dom";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Grid from "@mui/material/Grid";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import CloudUploadRoundedIcon from "@mui/icons-material/CloudUploadRounded";
import LocalMallRoundedIcon from "@mui/icons-material/LocalMallRounded";
import QueryStatsRoundedIcon from "@mui/icons-material/QueryStatsRounded";
import RefreshRoundedIcon from "@mui/icons-material/RefreshRounded";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
} from "recharts";

import { ROUTES } from "../../app/navigation.js";
import { catalogApi } from "../../api/index.js";
import useApiResource from "../../hooks/useApiResource.js";
import AsyncBoundary, { EmptyBlock } from "../../components/ui/AsyncBoundary.jsx";
import RecordTable from "../../components/ui/RecordTable.jsx";
import SectionCard from "../../components/ui/SectionCard.jsx";
import StatCard from "../../components/ui/StatCard.jsx";
import {
  clusterColor,
  formatCount,
  formatCurrency,
  formatCurrencyPrecise,
  formatDate,
  formatRelative,
} from "../../utils/format.js";

const STOCK_COLOR = {
  "Out of Stock": "error",
  "Low Stock": "warning",
  "In Stock": "success",
};

const ProductsPage = () => {
  const products = useApiResource((options) => catalogApi.fetchProducts(options));
  const inventory = useApiResource((options) => catalogApi.fetchInventory(options));

  const refreshAll = () => {
    products.refresh();
    inventory.refresh();
  };

  const stockByProduct = useMemo(() => {
    const index = new Map();
    for (const item of inventory.data?.inventory ?? []) {
      index.set(String(item.product_id), item);
    }
    return index;
  }, [inventory.data]);

  const rows = useMemo(() => {
    const list = products.data?.products ?? [];

    return list.map((product) => {
      const stock = stockByProduct.get(String(product.id)) ?? null;

      return {
        ...product,
        displayName: product.name || product.sku || product.external_product_id || "Unnamed",
        // Null rather than 0: a product with no inventory row is untracked, which is
        // a different statement from "we have none of it".
        quantity: stock ? Number(stock.quantity ?? 0) : null,
        stockStatus: stock?.status ?? null,
      };
    });
  }, [products.data, stockByProduct]);

  const stats = useMemo(() => {
    let priced = 0;
    let priceTotal = 0;
    let highest = null;

    for (const row of rows) {
      if (row.price === null || row.price === undefined || row.price === "") continue;

      const price = Number(row.price);
      if (Number.isNaN(price)) continue;

      priced += 1;
      priceTotal += price;
      if (highest === null || price > Number(highest.price)) highest = row;
    }

    return {
      priced,
      unpriced: rows.length - priced,
      averagePrice: priced ? priceTotal / priced : null,
      highest,
      untracked: rows.filter((row) => row.quantity === null).length,
      categories: new Set(rows.map((row) => row.category).filter(Boolean)).size,
    };
  }, [rows]);

  const byCategory = useMemo(() => {
    const totals = new Map();

    for (const row of rows) {
      const key = row.category || "Uncategorised";
      const entry = totals.get(key) ?? { category: key, products: 0 };
      entry.products += 1;
      totals.set(key, entry);
    }

    return [...totals.values()].sort((a, b) => b.products - a.products).slice(0, 10);
  }, [rows]);

  const columns = useMemo(
    () => [
      {
        id: "displayName",
        label: "Product",
        render: (row) => (
          <Stack spacing={0.25} sx={{ minWidth: 0 }}>
            <Typography variant="body2" fontWeight={600} noWrap>
              {row.displayName}
            </Typography>
            {row.external_product_id ? (
              <Typography variant="caption" color="text.secondary" sx={{ fontFamily: "monospace" }}>
                {row.external_product_id}
              </Typography>
            ) : null}
          </Stack>
        ),
      },
      {
        id: "sku",
        label: "SKU",
        render: (row) =>
          row.sku ? (
            <Typography variant="body2" sx={{ fontFamily: "monospace" }}>
              {row.sku}
            </Typography>
          ) : (
            "—"
          ),
      },
      {
        id: "category",
        label: "Category",
        render: (row) =>
          row.category ? (
            <Chip size="small" variant="outlined" label={row.category} />
          ) : (
            <Typography variant="caption" color="text.disabled">
              uncategorised
            </Typography>
          ),
      },
      {
        id: "price",
        label: "Price",
        numeric: true,
        // Sorted as a number; the string form would put 9 above 10.
        sortValue: (row) => (row.price === null || row.price === undefined ? null : Number(row.price)),
        render: (row) => formatCurrencyPrecise(row.price),
      },
      {
        id: "quantity",
        label: "In stock",
        numeric: true,
        render: (row) =>
          row.quantity === null ? (
            <Tooltip title="No inventory row for this product — it is untracked, not empty.">
              <Typography variant="caption" color="text.disabled">
                untracked
              </Typography>
            </Tooltip>
          ) : (
            formatCount(row.quantity)
          ),
      },
      {
        id: "stockStatus",
        label: "Stock",
        render: (row) =>
          row.stockStatus ? (
            <Chip
              size="small"
              label={row.stockStatus}
              color={STOCK_COLOR[row.stockStatus] ?? "default"}
              variant="outlined"
            />
          ) : (
            "—"
          ),
      },
      {
        id: "created_at",
        label: "Added",
        render: (row) => formatDate(row.created_at),
      },
    ],
    []
  );

  return (
    <Stack spacing={3}>
      <Stack direction="row" justifyContent="space-between" alignItems="center" spacing={2}>
        <Typography variant="caption" color="text.secondary">
          {products.lastUpdatedAt ? `Updated ${formatRelative(products.lastUpdatedAt)}` : " "}
        </Typography>
        <Stack direction="row" spacing={1}>
          <Button
            component={RouterLink}
            to={ROUTES.PRODUCT_ANALYSIS}
            size="small"
            startIcon={<QueryStatsRoundedIcon />}
          >
            Product analysis
          </Button>
          <Button
            size="small"
            startIcon={<RefreshRoundedIcon />}
            onClick={refreshAll}
            disabled={products.isLoading || products.isRefreshing}
          >
            Refresh
          </Button>
        </Stack>
      </Stack>

      <AsyncBoundary
        isLoading={products.isLoading}
        error={products.error}
        onRetry={products.reload}
        isEmpty={rows.length === 0}
        empty={
          <EmptyBlock
            icon={<LocalMallRoundedIcon color="primary" sx={{ fontSize: 46 }} />}
            title="No products yet"
            description="Upload a product file with external_product_id and whatever else you have — name, SKU, category and price are all optional."
            action={
              <Button
                component={RouterLink}
                to={ROUTES.IMPORT}
                variant="contained"
                startIcon={<CloudUploadRoundedIcon />}
              >
                Import products
              </Button>
            }
          />
        }
      >
        <Stack spacing={3}>
          <Grid container spacing={2.5}>
            <Grid item xs={12} sm={6} md={3}>
              <StatCard label="Products" value={rows.length} format="count" tone="primary" />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <StatCard label="Categories" value={stats.categories} format="count" tone="info" />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <StatCard
                label="Average price"
                value={stats.averagePrice}
                format="currency"
                tone="success"
                caption={
                  stats.unpriced > 0
                    ? `${formatCount(stats.unpriced)} products have no price and are excluded`
                    : "Across every product"
                }
              />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <StatCard
                label="Untracked"
                value={stats.untracked}
                format="count"
                tone={stats.untracked > 0 ? "warning" : "neutral"}
                hint="Products with no inventory row. Import inventory to see stock levels for them."
              />
            </Grid>
          </Grid>

          {inventory.error ? (
            <Alert severity="warning">
              The catalogue loaded, but inventory did not: {inventory.error.message}. Stock columns
              below are blank as a result.
            </Alert>
          ) : null}

          {byCategory.length > 1 ? (
            <SectionCard
              title="Products per category"
              subtitle={byCategory.length === 10 ? "Ten largest categories" : "Every category"}
              isRefreshing={products.isRefreshing}
            >
              <Box sx={{ height: 30 * byCategory.length + 40 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={byCategory} layout="vertical" margin={{ top: 4, right: 24, bottom: 4, left: 8 }}>
                    <CartesianGrid horizontal={false} strokeDasharray="3 3" strokeOpacity={0.4} />
                    <XAxis type="number" allowDecimals={false} tick={{ fontSize: 12 }} />
                    <YAxis type="category" dataKey="category" width={140} tick={{ fontSize: 12 }} interval={0} />
                    <ChartTooltip
                      contentStyle={{ borderRadius: 10, fontSize: 13 }}
                      formatter={(value) => [`${formatCount(value)} products`, "Products"]}
                    />
                    <Bar dataKey="products" radius={[0, 6, 6, 0]} maxBarSize={20}>
                      {byCategory.map((row, index) => (
                        <Cell key={row.category} fill={clusterColor(index)} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </Box>
            </SectionCard>
          ) : null}

          <SectionCard
            title="Catalogue"
            subtitle={
              stats.highest
                ? `${formatCount(rows.length)} products · most expensive is ${stats.highest.displayName} at ${formatCurrency(stats.highest.price)}`
                : `${formatCount(rows.length)} products`
            }
            isRefreshing={products.isRefreshing || inventory.isRefreshing}
          >
            <RecordTable
              columns={columns}
              rows={rows}
              getRowId={(row) => row.id}
              initialSortBy="displayName"
              initialSortDirection="asc"
              searchable
              searchPlaceholder="Search name, SKU, category or external ID"
              searchKeys={["displayName", "name", "sku", "category", "external_product_id"]}
              emptyMessage="No products match that search."
            />
          </SectionCard>
        </Stack>
      </AsyncBoundary>
    </Stack>
  );
};

export default ProductsPage;
