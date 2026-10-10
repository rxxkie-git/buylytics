/**
 * Dashboard.
 *
 * Six independent requests rather than one aggregate, because each is cached
 * separately server-side with its own TTL and each can fail on its own. A slow
 * revenue trend should not delay the headline tiles, and a failing inventory query
 * should not blank the whole page.
 *
 * The page is honest about two states the Phase 1 dashboard papered over:
 *
 * **Nothing imported yet.** Zeroes everywhere look like a broken page. If there are
 * no customers, the page says so and links to the import.
 *
 * **Imported but not analysed.** RFM and segment charts have no data until an
 * analytics job has run. That is not an error and not an empty business — it is a
 * missing step, and the page says which one and offers to start it.
 *
 * The body is passed to `<AsyncBoundary>` as a **function**, not as JSX children.
 * That is load-bearing rather than stylistic. JSX children are evaluated as
 * arguments before the boundary component runs, so writing `value={s.revenue}`
 * inline threw `Cannot read properties of null` on the very first render — while
 * the summary request was still in flight — and with no error boundary in the tree
 * React unmounted the whole application. The symptom was a blank white dashboard
 * on every visit, empty database or not. A function child is only invoked on the
 * branch that renders it, so `s` is guaranteed to exist inside it.
 */

import { Link as RouterLink } from "react-router-dom";
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Grid from "@mui/material/Grid";
import Stack from "@mui/material/Stack";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";
import Typography from "@mui/material/Typography";
import CloudUploadRoundedIcon from "@mui/icons-material/CloudUploadRounded";
import PlayArrowRoundedIcon from "@mui/icons-material/PlayArrowRounded";
import RefreshRoundedIcon from "@mui/icons-material/RefreshRounded";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useState } from "react";

import { ROUTES } from "../../app/navigation.js";
import { dashboardApi } from "../../api/index.js";
import useApiResource from "../../hooks/useApiResource.js";
import AsyncBoundary, { EmptyBlock, ErrorBlock } from "../../components/ui/AsyncBoundary.jsx";
import RecordTable from "../../components/ui/RecordTable.jsx";
import SectionCard from "../../components/ui/SectionCard.jsx";
import StatCard from "../../components/ui/StatCard.jsx";
import {
  formatCompact,
  formatCount,
  formatCurrency,
  formatDate,
  formatRelative,
  formatShare,
  segmentColor,
  segmentRank,
} from "../../utils/format.js";

const TREND_WINDOWS = [7, 30, 90];

const DashboardPage = () => {
  const [days, setDays] = useState(30);

  const summary = useApiResource((options) => dashboardApi.fetchSummary(options));
  const trend = useApiResource((options) => dashboardApi.fetchRevenueTrend({ days, ...options }), {
    deps: [days],
  });
  const rfm = useApiResource((options) => dashboardApi.fetchRfmDistribution(options));
  const segments = useApiResource((options) => dashboardApi.fetchSegmentDistribution(options));
  const topCustomers = useApiResource((options) => dashboardApi.fetchTopCustomers({ limit: 10, ...options }));
  const inventory = useApiResource((options) => dashboardApi.fetchInventorySummary(options));

  const refreshAll = () => {
    summary.refresh();
    trend.refresh();
    rfm.refresh();
    segments.refresh();
    topCustomers.refresh();
    inventory.refresh();
  };

  const s = summary.data;
  const hasData = Boolean(s && (s.customers > 0 || s.orders > 0 || s.products > 0));

  // A tenant with rows but no analytics run is the common first-week state.
  const needsAnalysis = Boolean(s && s.customers > 0 && !s.analytics_ready);

  return (
    <Stack spacing={3}>
      <Stack direction="row" justifyContent="space-between" alignItems="center" spacing={2}>
        <Typography variant="caption" color="text.secondary">
          {summary.lastUpdatedAt ? `Updated ${formatRelative(summary.lastUpdatedAt)}` : " "}
        </Typography>
        <Button
          size="small"
          startIcon={<RefreshRoundedIcon />}
          onClick={refreshAll}
          disabled={summary.isLoading || summary.isRefreshing}
        >
          Refresh
        </Button>
      </Stack>

      <AsyncBoundary
        isLoading={summary.isLoading}
        error={summary.error}
        onRetry={summary.reload}
        isEmpty={!hasData}
        empty={
          <EmptyBlock
            icon={<CloudUploadRoundedIcon color="primary" sx={{ fontSize: 46 }} />}
            title="No data yet"
            description="Import customers, orders, products or inventory and every figure on this page will be computed from them."
            action={
              <Button variant="contained" component={RouterLink} to={ROUTES.IMPORT}>
                Import a file
              </Button>
            }
          />
        }
      >
        {() => (
          <Stack spacing={3}>
            {needsAnalysis ? (
              <Alert
                severity="info"
                action={
                  <Button
                    size="small"
                    component={RouterLink}
                    to={ROUTES.JOBS}
                    startIcon={<PlayArrowRoundedIcon />}
                  >
                    Run analysis
                  </Button>
                }
              >
                <AlertTitle sx={{ fontSize: 14 }}>Analytics have not run yet</AlertTitle>
                {formatCount(s.customers)} customers are on file, but no RFM scores have been
                computed. The segment charts below stay empty until a job completes.
              </Alert>
            ) : null}

            <Grid container spacing={2}>
              <Grid item xs={12} sm={6} lg={3}>
                <StatCard
                  label="Revenue"
                  value={s.revenue}
                  format="currency"
                  caption={`Across ${formatCount(s.orders)} orders`}
                />
              </Grid>
              <Grid item xs={12} sm={6} lg={3}>
                <StatCard
                  label="Average order"
                  value={s.average_order_value}
                  format="currency"
                  caption="Mean order total"
                />
              </Grid>
              <Grid item xs={12} sm={6} lg={3}>
                <StatCard
                  label="Customers"
                  value={s.customers}
                  format="count"
                  // The gap between customers on file and customers who bought is
                  // the number that tells you whether RFM covers your base.
                  caption={`${formatCount(s.customers_with_orders)} have ordered`}
                  hint={`${formatCount(s.customers_without_orders)} customers have no orders on file, so they cannot be scored.`}
                />
              </Grid>
              <Grid item xs={12} sm={6} lg={3}>
                <StatCard
                  label="Needs restocking"
                  value={s.low_stock_items}
                  format="count"
                  tone={s.low_stock_items > 0 ? "warning" : "primary"}
                  caption={`At or below ${formatCount(s.low_stock_threshold)} units`}
                />
              </Grid>
            </Grid>

            <SectionCard
              title="Revenue"
              subtitle={
                trend.data
                  ? `${formatDate(trend.data.start_date)} to ${formatDate(trend.data.end_date)} · ${formatCurrency(trend.data.total_revenue)} total`
                  : "Daily totals"
              }
              isRefreshing={trend.isRefreshing}
              action={
                <ToggleButtonGroup
                  size="small"
                  exclusive
                  value={days}
                  onChange={(_, next) => next && setDays(next)}
                >
                  {TREND_WINDOWS.map((window) => (
                    <ToggleButton key={window} value={window} sx={{ px: 1.5 }}>
                      {window}d
                    </ToggleButton>
                  ))}
                </ToggleButtonGroup>
              }
            >
              <AsyncBoundary
                isLoading={trend.isLoading}
                error={trend.error}
                onRetry={trend.reload}
                isEmpty={!trend.data?.points?.length || trend.data.total_orders === 0}
                empty={
                  <Box sx={{ py: 6, textAlign: "center" }}>
                    <Typography variant="body2" color="text.secondary">
                      No orders in this window.
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      Orders need an order date to appear on this chart.
                    </Typography>
                  </Box>
                }
              >
                <Box sx={{ height: 300 }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={trend.data?.points ?? []} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                      <defs>
                        <linearGradient id="revenueFill" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="#0F766E" stopOpacity={0.35} />
                          <stop offset="100%" stopColor="#0F766E" stopOpacity={0.02} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} opacity={0.35} />
                      <XAxis
                        dataKey="date"
                        tickFormatter={formatDate}
                        tick={{ fontSize: 11 }}
                        // A 90-day window has more labels than fit; recharts drops
                        // them itself but only if it is allowed to.
                        interval="preserveStartEnd"
                        minTickGap={28}
                      />
                      <YAxis tickFormatter={formatCompact} tick={{ fontSize: 11 }} width={54} />
                      <Tooltip
                        formatter={(value, name) =>
                          name === "revenue" ? [formatCurrency(value), "Revenue"] : [formatCount(value), "Orders"]
                        }
                        labelFormatter={formatDate}
                      />
                      <Area
                        type="monotone"
                        dataKey="revenue"
                        stroke="#0F766E"
                        strokeWidth={2}
                        fill="url(#revenueFill)"
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                </Box>
              </AsyncBoundary>
            </SectionCard>

            <Grid container spacing={3}>
              <Grid item xs={12} lg={7}>
                <SectionCard
                  title="RFM segments"
                  subtitle={
                    rfm.data?.total_customers_scored
                      ? `${formatCount(rfm.data.total_customers_scored)} customers scored`
                      : "Behavioural segments from the latest run"
                  }
                  isRefreshing={rfm.isRefreshing}
                  action={
                    <Button size="small" component={RouterLink} to={ROUTES.RFM}>
                      Details
                    </Button>
                  }
                >
                  <AsyncBoundary
                    isLoading={rfm.isLoading}
                    error={rfm.error}
                    onRetry={rfm.reload}
                    isEmpty={!rfm.data?.segments?.length}
                    empty={
                      <Box sx={{ py: 6, textAlign: "center" }}>
                        <Typography variant="body2" color="text.secondary">
                          No RFM scores yet.
                        </Typography>
                        <Button size="small" component={RouterLink} to={ROUTES.JOBS} sx={{ mt: 1 }}>
                          Run an RFM job
                        </Button>
                      </Box>
                    }
                  >
                    <Box sx={{ height: 300 }}>
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart
                          data={rfm.data?.segments ?? []}
                          layout="vertical"
                          margin={{ top: 4, right: 16, left: 8, bottom: 4 }}
                        >
                          <CartesianGrid strokeDasharray="3 3" horizontal={false} opacity={0.35} />
                          <XAxis type="number" tickFormatter={formatCompact} tick={{ fontSize: 11 }} />
                          <YAxis
                            type="category"
                            dataKey="segment"
                            tick={{ fontSize: 11 }}
                            width={130}
                          />
                          <Tooltip
                            formatter={(value) => [formatCount(value), "Customers"]}
                            labelFormatter={(label) => label}
                          />
                          <Bar dataKey="customers" radius={[0, 6, 6, 0]}>
                            {(rfm.data?.segments ?? []).map((entry) => (
                              <Cell key={entry.segment} fill={segmentColor(entry.segment)} />
                            ))}
                          </Bar>
                        </BarChart>
                      </ResponsiveContainer>
                    </Box>

                    {/* Provenance, stated rather than buried. A dashboard built
                        mostly from uploaded RFM columns is a different claim from
                        one measured off transactions. */}
                    {rfm.data?.sources ? (
                      <Stack direction="row" spacing={1} sx={{ mt: 1.5 }} flexWrap="wrap" useFlexGap>
                        <Chip
                          size="small"
                          variant="outlined"
                          label={`${formatCount(rfm.data.sources.orders)} measured from orders`}
                        />
                        {rfm.data.sources.import > 0 ? (
                          <Chip
                            size="small"
                            variant="outlined"
                            color="info"
                            label={`${formatCount(rfm.data.sources.import)} from uploaded columns`}
                          />
                        ) : null}
                        {rfm.data.unscored_customers > 0 ? (
                          <Chip
                            size="small"
                            variant="outlined"
                            label={`${formatCount(rfm.data.unscored_customers)} unscored`}
                          />
                        ) : null}
                      </Stack>
                    ) : null}
                  </AsyncBoundary>
                </SectionCard>
              </Grid>

              <Grid item xs={12} lg={5}>
                <SectionCard
                  title="Stock health"
                  subtitle={
                    inventory.data
                      ? `${formatCount(inventory.data.tracked_products)} tracked products`
                      : "Inventory position"
                  }
                  isRefreshing={inventory.isRefreshing}
                  action={
                    <Button size="small" component={RouterLink} to={ROUTES.INVENTORY}>
                      Details
                    </Button>
                  }
                >
                  <AsyncBoundary
                    isLoading={inventory.isLoading}
                    error={inventory.error}
                    onRetry={inventory.reload}
                    isEmpty={!inventory.data?.tracked_products}
                    empty={
                      <Box sx={{ py: 6, textAlign: "center" }}>
                        <Typography variant="body2" color="text.secondary">
                          No inventory tracked.
                        </Typography>
                      </Box>
                    }
                  >
                    <Stack spacing={2}>
                      <Grid container spacing={1.5}>
                        <Grid item xs={6}>
                          <StatCard label="Healthy" value={inventory.data?.healthy} format="count" compact />
                        </Grid>
                        <Grid item xs={6}>
                          <StatCard
                            label="Low"
                            value={inventory.data?.low_stock}
                            format="count"
                            tone={inventory.data?.low_stock > 0 ? "warning" : "primary"}
                            compact
                          />
                        </Grid>
                        <Grid item xs={6}>
                          <StatCard
                            label="Out of stock"
                            value={inventory.data?.out_of_stock}
                            format="count"
                            tone={inventory.data?.out_of_stock > 0 ? "danger" : "primary"}
                            compact
                          />
                        </Grid>
                        <Grid item xs={6}>
                          <StatCard label="Units on hand" value={inventory.data?.total_units} format="count" compact />
                        </Grid>
                      </Grid>
                    </Stack>
                  </AsyncBoundary>
                </SectionCard>
              </Grid>
            </Grid>

            <SectionCard
              title="Highest-value customers"
              subtitle="Ranked by measured monetary value"
              isRefreshing={topCustomers.isRefreshing}
              action={
                <Button size="small" component={RouterLink} to={ROUTES.CUSTOMERS}>
                  All customers
                </Button>
              }
            >
              <AsyncBoundary
                isLoading={topCustomers.isLoading}
                error={topCustomers.error}
                onRetry={topCustomers.reload}
                isEmpty={!topCustomers.data?.length}
                empty={
                  <Box sx={{ py: 5, textAlign: "center" }}>
                    <Typography variant="body2" color="text.secondary">
                      No scored customers yet.
                    </Typography>
                  </Box>
                }
              >
                <RecordTable
                  rows={topCustomers.data ?? []}
                  getRowId={(row) => row.customer_id}
                  initialSortBy="monetary"
                  initialSortDirection="desc"
                  disableInternalPagination
                  maxHeight={420}
                  columns={[
                    {
                      id: "name",
                      label: "Customer",
                      render: (row) => (
                        <Box>
                          <Typography variant="body2" fontWeight={600}>
                            {/* A customer created as a shell by the orders import
                                has no name; showing the external id is more useful
                                than an empty cell. */}
                            {row.name || row.external_customer_id || "Unnamed"}
                          </Typography>
                          {row.email ? (
                            <Typography variant="caption" color="text.secondary">
                              {row.email}
                            </Typography>
                          ) : null}
                        </Box>
                      ),
                    },
                    {
                      id: "segment",
                      label: "Segment",
                      sortValue: (row) => segmentRank(row.segment),
                      render: (row) =>
                        row.segment ? (
                          <Chip
                            size="small"
                            label={row.segment}
                            sx={{
                              bgcolor: `${segmentColor(row.segment)}1A`,
                              color: segmentColor(row.segment),
                              fontWeight: 700,
                            }}
                          />
                        ) : (
                          "—"
                        ),
                    },
                    {
                      id: "monetary",
                      label: "Value",
                      numeric: true,
                      render: (row) => formatCurrency(row.monetary),
                    },
                    {
                      id: "frequency",
                      label: "Orders",
                      numeric: true,
                      render: (row) => formatCount(row.frequency),
                    },
                    {
                      id: "recency",
                      label: "Days since",
                      numeric: true,
                      render: (row) => (row.recency === null ? "—" : formatCount(Math.round(row.recency))),
                    },
                    {
                      id: "source",
                      label: "Source",
                      render: (row) => (
                        <Typography variant="caption" color="text.secondary">
                          {row.source === "import" ? "uploaded" : "orders"}
                        </Typography>
                      ),
                    },
                  ]}
                />
              </AsyncBoundary>
            </SectionCard>

            {/* Clusters are a separate model from RFM and only exist after a
                segmentation job, so they get their own section rather than being
                mixed into the RFM chart. */}
            {segments.error ? (
              <ErrorBlock error={segments.error} onRetry={segments.reload} />
            ) : segments.data?.clusters?.length ? (
              <SectionCard
                title="Behavioural clusters"
                subtitle={
                  segments.data.model_version
                    ? `Model ${segments.data.model_version} · ${formatCount(segments.data.total_customers)} customers`
                    : `${formatCount(segments.data.total_customers)} customers`
                }
                isRefreshing={segments.isRefreshing}
                action={
                  <Button size="small" component={RouterLink} to={ROUTES.SEGMENTS}>
                    Details
                  </Button>
                }
              >
                <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                  {segments.data.clusters.map((cluster) => (
                    <Chip
                      key={`${cluster.segment}-${cluster.cluster}`}
                      variant="outlined"
                      label={`${cluster.segment ?? `Cluster ${cluster.cluster}`} · ${formatCount(cluster.customers)} (${formatShare(cluster.share)})`}
                    />
                  ))}
                </Stack>
              </SectionCard>
            ) : null}
          </Stack>
        )}
      </AsyncBoundary>
    </Stack>
  );
};

export default DashboardPage;
