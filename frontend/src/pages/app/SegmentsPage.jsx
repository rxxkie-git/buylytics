/**
 * Segments.
 *
 * This page shows the output of the clustering model, which is a different thing
 * from the RFM page's rule-based segments and the distinction is worth keeping
 * visible: RFM segments come from score thresholds and are deterministic, whereas
 * clusters come from a fitted model and their numbering carries no ordering — cluster
 * 3 is not "better" than cluster 1. The page therefore describes each cluster by its
 * measured centroid rather than by its label.
 *
 * `model_version` is displayed prominently. Two clusterings fitted at different times
 * are not comparable, so anyone reading these numbers needs to know which run produced
 * them.
 *
 * Churn risk is whatever the model reported; nothing here derives or smooths it. If a
 * cluster has no churn figure the cell is blank rather than 0%.
 */

import { useMemo } from "react";
import { Link as RouterLink } from "react-router-dom";
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Grid from "@mui/material/Grid";
import LinearProgress from "@mui/material/LinearProgress";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import HubRoundedIcon from "@mui/icons-material/HubRounded";
import PlayArrowRoundedIcon from "@mui/icons-material/PlayArrowRounded";
import RefreshRoundedIcon from "@mui/icons-material/RefreshRounded";
import {
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";

import { ROUTES } from "../../app/navigation.js";
import { analyticsApi, dashboardApi } from "../../api/index.js";
import useApiResource from "../../hooks/useApiResource.js";
import AsyncBoundary, { EmptyBlock } from "../../components/ui/AsyncBoundary.jsx";
import RecordTable from "../../components/ui/RecordTable.jsx";
import SectionCard from "../../components/ui/SectionCard.jsx";
import StatCard from "../../components/ui/StatCard.jsx";
import {
  clusterColor,
  formatCount,
  formatDateTime,
  formatDecimal,
  formatRelative,
  formatShare,
  shortId,
} from "../../utils/format.js";

const CHURN_TONE = (risk) => {
  if (risk === null || risk === undefined) return "neutral";
  const value = Number(risk);
  if (value >= 0.66) return "danger";
  if (value >= 0.33) return "warning";
  return "success";
};

const SegmentsPage = () => {
  const distribution = useApiResource((options) => dashboardApi.fetchSegmentDistribution(options));
  const rows = useApiResource((options) => analyticsApi.fetchSegments(options));

  const refreshAll = () => {
    distribution.refresh();
    rows.refresh();
  };

  const clusters = distribution.data?.clusters ?? [];
  const customerRows = rows.data?.results ?? [];

  // The distribution endpoint reports customers per cluster but not the average
  // churn risk within one, so it is aggregated from the per-customer rows here.
  const churnByCluster = useMemo(() => {
    const totals = new Map();

    for (const row of customerRows) {
      if (row.churn_risk === null || row.churn_risk === undefined) continue;

      const key = String(row.cluster);
      const entry = totals.get(key) ?? { sum: 0, count: 0 };
      entry.sum += Number(row.churn_risk);
      entry.count += 1;
      totals.set(key, entry);
    }

    const averages = new Map();
    for (const [key, { sum, count }] of totals) {
      averages.set(key, count ? sum / count : null);
    }
    return averages;
  }, [customerRows]);

  const clusterRows = useMemo(
    () =>
      clusters.map((cluster) => ({
        ...cluster,
        churn: churnByCluster.get(String(cluster.cluster)) ?? null,
      })),
    [clusters, churnByCluster]
  );

  const scatterData = useMemo(
    () =>
      customerRows
        .filter((row) => row.score !== null && row.score !== undefined)
        .map((row) => ({
          x: Number(row.score),
          y: row.churn_risk === null || row.churn_risk === undefined ? 0 : Number(row.churn_risk) * 100,
          cluster: row.cluster,
          customerId: row.customer_id,
          hasChurn: row.churn_risk !== null && row.churn_risk !== undefined,
        })),
    [customerRows]
  );

  const clusterIds = useMemo(
    () => [...new Set(scatterData.map((point) => point.cluster))].sort((a, b) => Number(a) - Number(b)),
    [scatterData]
  );

  const detailColumns = useMemo(
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
        id: "cluster",
        label: "Cluster",
        numeric: true,
        render: (row) => (
          <Chip
            size="small"
            label={row.cluster}
            sx={{
              bgcolor: `${clusterColor(row.cluster)}1F`,
              color: clusterColor(row.cluster),
              fontWeight: 700,
            }}
          />
        ),
      },
      { id: "segment", label: "Label" },
      {
        id: "score",
        label: "Score",
        numeric: true,
        render: (row) => formatDecimal(row.score),
      },
      {
        id: "churn_risk",
        label: "Churn risk",
        numeric: true,
        render: (row) =>
          row.churn_risk === null || row.churn_risk === undefined ? (
            "—"
          ) : (
            <Typography
              variant="body2"
              color={CHURN_TONE(row.churn_risk) === "danger" ? "error.main" : "text.primary"}
              sx={{ fontVariantNumeric: "tabular-nums" }}
            >
              {formatShare(row.churn_risk)}
            </Typography>
          ),
      },
      { id: "model_version", label: "Model" },
      {
        id: "calculated_at",
        label: "Computed",
        render: (row) => formatRelative(row.calculated_at),
      },
    ],
    []
  );

  const hasClusters = clusters.length > 0;

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
        isEmpty={!hasClusters}
        empty={
          <EmptyBlock
            icon={<HubRoundedIcon color="primary" sx={{ fontSize: 46 }} />}
            title="No segmentation run yet"
            description="Clustering needs an RFM snapshot to work from. Queue a full refresh — it computes RFM from your orders and then fits the model."
            action={
              <Button component={RouterLink} to={ROUTES.JOBS} variant="contained" startIcon={<PlayArrowRoundedIcon />}>
                Queue a job
              </Button>
            }
          />
        }
      >
        <Stack spacing={3}>
          <Grid container spacing={2.5}>
            <Grid item xs={12} sm={6} md={3}>
              <StatCard
                label="Customers clustered"
                value={distribution.data?.total_customers}
                format="count"
                tone="primary"
              />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <StatCard label="Clusters" value={clusters.length} format="count" tone="info" />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <StatCard
                label="Average churn risk"
                value={distribution.data?.churn_risk}
                format="share"
                tone={CHURN_TONE(distribution.data?.churn_risk)}
                hint="Reported by the model across all clustered customers. Blank means the model did not produce a churn figure."
              />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <StatCard
                label="Model version"
                value={distribution.data?.model_version}
                format="text"
                tone="neutral"
                caption={
                  distribution.data?.generated_at ? formatDateTime(distribution.data.generated_at) : undefined
                }
                hint="Clusters from different model versions are not comparable — the numbering is arbitrary and is reassigned on every fit."
              />
            </Grid>
          </Grid>

          <Grid container spacing={2.5}>
            <Grid item xs={12} md={5}>
              <SectionCard
                title="Share of customers"
                subtitle="Cluster numbering is arbitrary; size is not."
                isRefreshing={distribution.isRefreshing}
              >
                <Box sx={{ height: 300 }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie
                        data={clusterRows}
                        dataKey="customers"
                        nameKey="segment"
                        innerRadius={62}
                        outerRadius={104}
                        paddingAngle={2}
                      >
                        {clusterRows.map((cluster) => (
                          <Cell key={cluster.cluster} fill={clusterColor(cluster.cluster)} />
                        ))}
                      </Pie>
                      <ChartTooltip
                        formatter={(value, name) => [`${formatCount(value)} customers`, name]}
                        contentStyle={{ borderRadius: 10, fontSize: 13 }}
                      />
                      <Legend verticalAlign="bottom" height={36} iconType="circle" />
                    </PieChart>
                  </ResponsiveContainer>
                </Box>
              </SectionCard>
            </Grid>

            <Grid item xs={12} md={7}>
              <SectionCard
                title="Cluster profile"
                subtitle="Average score and churn risk per cluster, measured — not assigned."
                isRefreshing={distribution.isRefreshing || rows.isRefreshing}
              >
                <Stack spacing={2}>
                  {clusterRows.map((cluster) => (
                    <Box key={cluster.cluster}>
                      <Stack direction="row" justifyContent="space-between" alignItems="center" spacing={1}>
                        <Stack direction="row" spacing={1} alignItems="center" sx={{ minWidth: 0 }}>
                          <Box
                            sx={{
                              width: 10,
                              height: 10,
                              borderRadius: "50%",
                              bgcolor: clusterColor(cluster.cluster),
                              flexShrink: 0,
                            }}
                          />
                          <Typography variant="body2" fontWeight={600} noWrap>
                            {cluster.segment ?? `Cluster ${cluster.cluster}`}
                          </Typography>
                        </Stack>
                        <Typography variant="caption" color="text.secondary" sx={{ whiteSpace: "nowrap" }}>
                          {formatCount(cluster.customers)} · {formatShare(cluster.share)}
                          {cluster.churn === null ? "" : ` · churn ${formatShare(cluster.churn)}`}
                        </Typography>
                      </Stack>
                      <LinearProgress
                        variant="determinate"
                        // `share` arrives as a fraction; clamped because a rounding
                        // artefact above 1 would render as an overflowing bar.
                        value={Math.min(100, Math.max(0, Number(cluster.share ?? 0) * 100))}
                        sx={{
                          mt: 0.75,
                          height: 8,
                          borderRadius: 4,
                          bgcolor: "action.hover",
                          "& .MuiLinearProgress-bar": {
                            borderRadius: 4,
                            backgroundColor: clusterColor(cluster.cluster),
                          },
                        }}
                      />
                      <Typography variant="caption" color="text.secondary">
                        avg score {formatDecimal(cluster.avg_score)}
                      </Typography>
                    </Box>
                  ))}
                </Stack>
              </SectionCard>
            </Grid>
          </Grid>

          {rows.error ? (
            <Alert severity="warning">
              <AlertTitle>Per-customer segments unavailable</AlertTitle>
              The cluster summary above loaded, but the customer-level rows did not:{" "}
              {rows.error.message}
            </Alert>
          ) : null}

          {!rows.error && scatterData.length > 0 ? (
            <SectionCard
              title="Score against churn risk"
              subtitle={`${formatCount(scatterData.length)} customers with a score. Colour is the cluster.`}
              isRefreshing={rows.isRefreshing}
            >
              <Box sx={{ height: 340 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <ScatterChart margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
                    <XAxis
                      type="number"
                      dataKey="x"
                      name="Score"
                      tick={{ fontSize: 12 }}
                      label={{ value: "Score", position: "insideBottom", offset: -2, fontSize: 12 }}
                    />
                    <YAxis
                      type="number"
                      dataKey="y"
                      name="Churn risk"
                      unit="%"
                      tick={{ fontSize: 12 }}
                      width={54}
                    />
                    <ZAxis range={[38, 38]} />
                    <ChartTooltip
                      contentStyle={{ borderRadius: 10, fontSize: 13 }}
                      formatter={(value, name) =>
                        name === "Churn risk" ? [`${Number(value).toFixed(1)}%`, name] : [formatDecimal(value), name]
                      }
                      labelFormatter={() => ""}
                    />
                    {clusterIds.map((cluster) => (
                      <Scatter
                        key={cluster}
                        name={`Cluster ${cluster}`}
                        data={scatterData.filter((point) => point.cluster === cluster)}
                        fill={clusterColor(cluster)}
                        fillOpacity={0.72}
                      />
                    ))}
                    <Legend verticalAlign="top" height={28} iconType="circle" />
                  </ScatterChart>
                </ResponsiveContainer>
              </Box>
            </SectionCard>
          ) : null}

          <SectionCard
            title="Every clustered customer"
            subtitle={`${formatCount(customerRows.length)} rows from the latest segmentation`}
            isRefreshing={rows.isRefreshing}
          >
            <RecordTable
              columns={detailColumns}
              rows={customerRows}
              getRowId={(row) => row.customer_id}
              initialSortBy="churn_risk"
              initialSortDirection="desc"
              searchable
              searchPlaceholder="Search customer ID, label or model version"
              searchKeys={["customer_id", "segment", "model_version"]}
              emptyMessage="No per-customer segments stored yet."
            />
          </SectionCard>
        </Stack>
      </AsyncBoundary>
    </Stack>
  );
};

export default SegmentsPage;
