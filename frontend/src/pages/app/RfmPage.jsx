/**
 * RFM.
 *
 * The distinction this page exists to make clear is where each number came from.
 * `/api/v1/analytics/rfm` returns a `source` per row: `"orders"` means recency,
 * frequency and monetary were measured by aggregating that customer's orders, and
 * `"import"` means a spreadsheet supplied those columns and they were taken on
 * trust. Both are shown, never blended into one silent total, because a decision
 * made on uploaded numbers deserves to know it is doing so.
 *
 * Customers with no orders and no uploaded RFM columns are absent from the results
 * entirely — the backend excludes rather than invents them — so the "unscored"
 * count from the distribution endpoint is displayed alongside, otherwise the page
 * would quietly imply the whole customer base is represented here.
 */

import { useMemo, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Grid from "@mui/material/Grid";
import Stack from "@mui/material/Stack";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import LeaderboardRoundedIcon from "@mui/icons-material/LeaderboardRounded";
import PlayArrowRoundedIcon from "@mui/icons-material/PlayArrowRounded";
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
import { analyticsApi, dashboardApi } from "../../api/index.js";
import useApiResource from "../../hooks/useApiResource.js";
import AsyncBoundary, { EmptyBlock } from "../../components/ui/AsyncBoundary.jsx";
import RecordTable from "../../components/ui/RecordTable.jsx";
import SectionCard from "../../components/ui/SectionCard.jsx";
import StatCard from "../../components/ui/StatCard.jsx";
import {
  formatCompact,
  formatCount,
  formatCurrency,
  formatDecimal,
  formatRelative,
  formatShare,
  segmentColor,
  segmentRank,
  shortId,
} from "../../utils/format.js";

const SOURCES = {
  ALL: "all",
  ORDERS: "orders",
  IMPORT: "import",
};

const SOURCE_LABEL = { orders: "Measured from orders", import: "From uploaded columns" };

/**
 * The 1–5 score cells. A high R score means *recent*, so the colour scale runs the
 * same direction for all three even though recency's underlying units are inverted —
 * which is exactly the trap of showing raw days next to a score.
 */
const SCORE_COLOR = ["#E2E8F0", "#DC2626", "#EA580C", "#CA8A04", "#0891B2", "#0F766E"];

const ScoreCell = ({ value }) => {
  if (value === null || value === undefined) return "—";

  const score = Number(value);
  const color = SCORE_COLOR[score] ?? SCORE_COLOR[0];

  return (
    <Box
      component="span"
      sx={{
        display: "inline-grid",
        placeItems: "center",
        width: 26,
        height: 26,
        borderRadius: 1,
        fontSize: 12,
        fontWeight: 700,
        color,
        bgcolor: `${color}1F`,
      }}
    >
      {score}
    </Box>
  );
};

const RfmPage = () => {
  const [source, setSource] = useState(SOURCES.ALL);

  const distribution = useApiResource((options) => dashboardApi.fetchRfmDistribution(options));
  const rows = useApiResource((options) => analyticsApi.fetchRfm(options));

  const refreshAll = () => {
    distribution.refresh();
    rows.refresh();
  };

  const allRows = rows.data?.results ?? [];

  const visibleRows = useMemo(() => {
    if (source === SOURCES.ALL) return allRows;
    return allRows.filter((row) => row.source === source);
  }, [allRows, source]);

  const segments = useMemo(() => {
    const list = distribution.data?.segments ?? [];
    // Sorted by RFM rank so the chart reads high-value first, regardless of the
    // order the API happened to return.
    return [...list].sort((a, b) => segmentRank(a.segment) - segmentRank(b.segment));
  }, [distribution.data]);

  const sources = distribution.data?.sources ?? {};

  const columns = useMemo(
    () => [
      {
        id: "customer_id",
        label: "Customer",
        render: (row) => (
          <Typography variant="body2" sx={{ fontFamily: "monospace" }} title={row.customer_id}>
            {shortId(row.customer_id)}
          </Typography>
        ),
      },
      {
        id: "rfm_segment",
        label: "Segment",
        sortValue: (row) => segmentRank(row.rfm_segment),
        render: (row) => (
          <Chip
            size="small"
            label={row.rfm_segment ?? "—"}
            sx={{
              bgcolor: `${segmentColor(row.rfm_segment)}1F`,
              color: segmentColor(row.rfm_segment),
              fontWeight: 600,
            }}
          />
        ),
      },
      {
        id: "recency",
        label: "Recency (days)",
        numeric: true,
        render: (row) => formatCount(row.recency),
      },
      { id: "r_score", label: "R", numeric: true, render: (row) => <ScoreCell value={row.r_score} /> },
      {
        id: "frequency",
        label: "Frequency",
        numeric: true,
        render: (row) => formatCount(row.frequency),
      },
      { id: "f_score", label: "F", numeric: true, render: (row) => <ScoreCell value={row.f_score} /> },
      {
        id: "monetary",
        label: "Monetary",
        numeric: true,
        render: (row) => formatCurrency(row.monetary),
      },
      { id: "m_score", label: "M", numeric: true, render: (row) => <ScoreCell value={row.m_score} /> },
      {
        id: "average_order_value",
        label: "Avg order",
        numeric: true,
        render: (row) => formatCurrency(row.average_order_value),
      },
      {
        id: "source",
        label: "Source",
        render: (row) => (
          <Tooltip title={SOURCE_LABEL[row.source] ?? "Unknown provenance"}>
            <Chip
              size="small"
              variant="outlined"
              color={row.source === "orders" ? "success" : "default"}
              label={row.source === "orders" ? "Measured" : "Uploaded"}
            />
          </Tooltip>
        ),
      },
      {
        id: "calculated_at",
        label: "Computed",
        render: (row) => formatRelative(row.calculated_at),
      },
    ],
    []
  );

  const hasScores = allRows.length > 0 || (distribution.data?.total_customers_scored ?? 0) > 0;

  return (
    <Stack spacing={3}>
      <Stack direction="row" justifyContent="space-between" alignItems="center" spacing={2}>
        <Typography variant="caption" color="text.secondary">
          {distribution.data?.generated_at
            ? `Snapshot generated ${formatRelative(distribution.data.generated_at)}`
            : " "}
        </Typography>
        <Button
          size="small"
          startIcon={<RefreshRoundedIcon />}
          onClick={refreshAll}
          disabled={distribution.isLoading || distribution.isRefreshing}
        >
          Refresh
        </Button>
      </Stack>

      <AsyncBoundary
        isLoading={distribution.isLoading}
        error={distribution.error}
        onRetry={distribution.reload}
        isEmpty={!hasScores}
        empty={
          <EmptyBlock
            icon={<LeaderboardRoundedIcon color="primary" sx={{ fontSize: 46 }} />}
            title="Nothing scored yet"
            description="RFM is measured from your order history. Import orders, then queue an RFM job — nothing here is estimated from customer records alone."
            action={
              <Stack direction="row" spacing={1.5} justifyContent="center">
                <Button component={RouterLink} to={ROUTES.IMPORT} variant="outlined">
                  Import orders
                </Button>
                <Button
                  component={RouterLink}
                  to={ROUTES.JOBS}
                  variant="contained"
                  startIcon={<PlayArrowRoundedIcon />}
                >
                  Queue a job
                </Button>
              </Stack>
            }
          />
        }
      >
        <Stack spacing={3}>
          <Grid container spacing={2.5}>
            <Grid item xs={12} sm={6} md={3}>
              <StatCard
                label="Customers scored"
                value={distribution.data?.total_customers_scored}
                format="count"
                tone="primary"
              />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <StatCard
                label="Measured from orders"
                value={sources.orders}
                format="count"
                tone="success"
                hint="Aggregated from the orders and order_items tables. These are the numbers to trust."
              />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <StatCard
                label="From uploaded columns"
                value={sources.import}
                format="count"
                tone={Number(sources.import ?? 0) > 0 ? "warning" : "neutral"}
                hint="Passed through from recency/frequency/monetary columns in a file. Used only for customers who have no orders on file."
              />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <StatCard
                label="Unscored"
                value={distribution.data?.unscored_customers}
                format="count"
                tone="neutral"
                caption="Not represented below"
                hint="Customers with neither orders nor uploaded RFM columns. They are excluded rather than given invented scores."
              />
            </Grid>
          </Grid>

          <SectionCard
            title="Segment distribution"
            subtitle="Rule-based segments from the R, F and M scores. Hover for the measured averages."
            isRefreshing={distribution.isRefreshing}
          >
            <Box sx={{ height: 34 * Math.max(segments.length, 3) + 40 }}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={segments} layout="vertical" margin={{ top: 4, right: 24, bottom: 4, left: 8 }}>
                  <CartesianGrid horizontal={false} strokeDasharray="3 3" strokeOpacity={0.4} />
                  <XAxis type="number" tickFormatter={formatCompact} tick={{ fontSize: 12 }} />
                  <YAxis
                    type="category"
                    dataKey="segment"
                    width={140}
                    tick={{ fontSize: 12 }}
                    interval={0}
                  />
                  <ChartTooltip
                    contentStyle={{ borderRadius: 10, fontSize: 13 }}
                    formatter={(value, _name, entry) => {
                      const row = entry?.payload ?? {};
                      return [
                        `${formatCount(value)} customers · ${formatShare(row.share)} · avg ${formatCurrency(
                          row.avg_monetary
                        )} over ${formatDecimal(row.avg_frequency)} orders`,
                        row.segment,
                      ];
                    }}
                  />
                  <Bar dataKey="customers" radius={[0, 6, 6, 0]} maxBarSize={22}>
                    {segments.map((row) => (
                      <Cell key={row.segment} fill={segmentColor(row.segment)} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </Box>
          </SectionCard>

          <Grid container spacing={2.5}>
            {segments.slice(0, 4).map((row) => (
              <Grid item xs={12} sm={6} md={3} key={row.segment}>
                <StatCard
                  compact
                  label={row.segment}
                  value={row.total_monetary}
                  format="currency"
                  tone="neutral"
                  caption={`${formatCount(row.customers)} customers · avg ${formatDecimal(
                    row.avg_recency
                  )} days since order`}
                />
              </Grid>
            ))}
          </Grid>

          {rows.error ? (
            <Alert severity="warning">
              The summary above loaded, but the per-customer rows did not: {rows.error.message}
            </Alert>
          ) : null}

          <SectionCard
            title="Every scored customer"
            subtitle={
              source === SOURCES.ALL
                ? `${formatCount(allRows.length)} rows`
                : `${formatCount(visibleRows.length)} of ${formatCount(allRows.length)} rows`
            }
            isRefreshing={rows.isRefreshing}
            action={
              <ToggleButtonGroup
                size="small"
                exclusive
                value={source}
                onChange={(_, next) => next && setSource(next)}
              >
                <ToggleButton value={SOURCES.ALL}>All</ToggleButton>
                <ToggleButton value={SOURCES.ORDERS}>Measured</ToggleButton>
                <ToggleButton value={SOURCES.IMPORT}>Uploaded</ToggleButton>
              </ToggleButtonGroup>
            }
          >
            <RecordTable
              columns={columns}
              rows={visibleRows}
              getRowId={(row) => row.customer_id}
              initialSortBy="monetary"
              initialSortDirection="desc"
              searchable
              searchPlaceholder="Search customer ID or segment"
              searchKeys={["customer_id", "rfm_segment"]}
              emptyMessage={
                source === SOURCES.IMPORT
                  ? "No customer is relying on uploaded RFM columns — every score here was measured from orders."
                  : "No rows match."
              }
            />
          </SectionCard>
        </Stack>
      </AsyncBoundary>
    </Stack>
  );
};

export default RfmPage;
