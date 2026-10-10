import AutoAwesomeRoundedIcon from "@mui/icons-material/AutoAwesomeRounded";
import HubRoundedIcon from "@mui/icons-material/HubRounded";
import InsightsRoundedIcon from "@mui/icons-material/InsightsRounded";
import SpeedRoundedIcon from "@mui/icons-material/SpeedRounded";
import {
  alpha,
  Box,
  Chip,
  FormControl,
  Grid,
  InputLabel,
  LinearProgress,
  MenuItem,
  Paper,
  Select,
  Stack,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import {
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import ChartCard from "../components/ChartCard";
import ErrorState from "../components/ErrorState";
import LoadingState from "../components/LoadingState";
import NoDatasetState from "../components/NoDatasetState";
import PageHeader from "../components/PageHeader";
import PcaScatter3D from "../components/PcaScatter3D";
import useApiData from "../hooks/useApiData";
import { getClusteringOutput } from "../services/api";
import { getDatasetId } from "../utils/storage";
import { formatCurrency } from "../utils/formatters";

const emptyResults = {
  totalClusters: 0,
  optimalK: 0,
  silhouetteScore: 0,
  clusterDistribution: [],
  scatterData: [],
  elbowMethod: [],
  clusterProfiles: [],
  modelMeta: {
    selectedAlgorithm: "unknown",
    bestSilhouetteScore: 0,
    candidateScores: [],
  },
};

// Analysis page explains how the clustering model behaved and how the clusters look.
const Results = () => {
  const navigate = useNavigate();
  const theme = useTheme();
  const hasDataset = Boolean(getDatasetId());
  const [clusterBy, setClusterBy] = useState("default");
  const [clusterCount, setClusterCount] = useState(4);

  const fetchAnalysisResults = useCallback(
    () => getClusteringOutput({ clusterBy, clusterCount }),
    [clusterBy, clusterCount]
  );

  const { data, loading, error, reload } = useApiData(fetchAnalysisResults, emptyResults, [fetchAnalysisResults], hasDataset);

  const clusterDistribution = Array.isArray(data?.clusterDistribution) ? data.clusterDistribution : [];
  const scatterData = Array.isArray(data?.scatterData) ? data.scatterData : [];
  const clusterProfiles = Array.isArray(data?.clusterProfiles) ? data.clusterProfiles : [];

  const hasClusterData = clusterDistribution.length > 0;
  const showInitialLoading = loading && !hasClusterData;
  const showBlockingError = Boolean(error) && !hasClusterData;

  if (showInitialLoading) {
    return (
      <LoadingState
        title="Loading analysis results..."
        description="Fetching cluster distribution, elbow scores, and RFM scatter points."
      />
    );
  }

  if (showBlockingError) {
    return <ErrorState message={error} onRetry={reload} />;
  }

  const noClusterData = !hasClusterData;
  const clusterSeries = noClusterData
    ? []
    : clusterDistribution.map((cluster) => ({
        ...cluster,
        points: scatterData.filter((point) => point.cluster === cluster.cluster),
      }));

  const chartTooltipStyle = {
    backgroundColor:
      theme.palette.mode === "light"
        ? alpha("#0F172A", 0.96)
        : alpha("#020617", 0.97),
    border: `1px solid ${alpha(theme.palette.divider, 0.95)}`,
    borderRadius: 14,
    color: "#F8FAFC",
    boxShadow: "0 10px 24px rgba(2, 6, 23, 0.28)",
  };

  const chartTooltipLabelStyle = {
    color: "#F8FAFC",
    fontWeight: 700,
  };

  const chartTooltipItemStyle = {
    color: "#E2E8F0",
    fontWeight: 600,
  };

  if (!loading && noClusterData) {
    return (
      <Box>
        <PageHeader
          eyebrow="Model Diagnostics"
          title="No analysis results available"
          subtitle="Upload a dataset first or refresh after customer segmentation completes."
          chipLabel="Awaiting data"
          actionLabel="Upload Data"
          onActionClick={() => navigate("/upload")}
        />
        <Paper sx={{ p: 4, mt: 3 }}>
          <Typography variant="h6">No clustering results found</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            The analysis results page requires a successfully uploaded customer dataset. If you have already uploaded data, refresh the page or re-open the upload screen to ensure the dataset was stored.
          </Typography>
        </Paper>
      </Box>
    );
  }

  return (
    <Box>
      <PageHeader
        eyebrow="Model Diagnostics"
        title="Validate clustering quality and segment separation"
        subtitle="Review the number of clusters, compare the elbow curve, and inspect how customers are distributed across the RFM space."
        chipLabel={`Silhouette Score: ${data.silhouetteScore}`}
        actionLabel="Explore Customer Segments"
        onActionClick={() => navigate("/segments")}
      />

      {loading ? <LinearProgress sx={{ mb: 2 }} /> : null}
      {error ? (
        <Paper sx={{ p: 1.5, mb: 2 }}>
          <Typography variant="body2" color="warning.main">
            {`Unable to refresh results right now: ${error}`}
          </Typography>
        </Paper>
      ) : null}

      <Paper sx={{ p: 2.4, mb: 3 }}>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={2} alignItems="center">
          <FormControl size="small" sx={{ minWidth: 220 }}>
            <InputLabel id="customer-cluster-by-label">Cluster By</InputLabel>
            <Select
              labelId="customer-cluster-by-label"
              label="Cluster By"
              value={clusterBy}
              onChange={(event) => setClusterBy(event.target.value)}
            >
              <MenuItem value="default">Default (Recency + Frequency + Monetary)</MenuItem>
              <MenuItem value="recency_frequency">Recency + Frequency</MenuItem>
              <MenuItem value="recency_monetary">Recency + Monetary</MenuItem>
              <MenuItem value="frequency_monetary">Frequency + Monetary</MenuItem>
              <MenuItem value="recency">Recency only</MenuItem>
              <MenuItem value="frequency">Frequency only</MenuItem>
              <MenuItem value="monetary">Monetary only</MenuItem>
            </Select>
          </FormControl>

          <TextField
            size="small"
            label="Cluster Count"
            type="number"
            value={clusterCount}
            onChange={(event) => setClusterCount(Number(event.target.value))}
            inputProps={{ min: 2, max: 12 }}
            sx={{ width: 160 }}
          />

          <Chip label={`Active clusterBy: ${clusterBy}`} variant="outlined" />
          <Chip label={`Active clusterCount: ${clusterCount}`} variant="outlined" />
        </Stack>
      </Paper>

      <Grid container spacing={3}>
        <Grid item xs={12}>
          <Paper
            sx={{
              p: 2.4,
              borderRadius: "24px",
              border: "1px solid",
              borderColor: alpha(theme.palette.divider, theme.palette.mode === "dark" ? 0.55 : 0.8),
              background:
                theme.palette.mode === "dark"
                  ? "linear-gradient(135deg, rgba(2,6,23,0.96), rgba(15,118,110,0.22), rgba(37,99,235,0.14))"
                  : "linear-gradient(135deg, rgba(15,118,110,0.10), rgba(37,99,235,0.08), rgba(249,115,22,0.06))",
            }}
          >
            <Typography
              variant="subtitle2"
              sx={{
                color: theme.palette.mode === "dark" ? theme.palette.primary.light : theme.palette.primary.main,
                letterSpacing: 0.8,
              }}
            >
              Trained Model Summary
            </Typography>
            <Typography
              variant="h6"
              sx={{
                mt: 0.6,
                color: theme.palette.text.primary,
                textShadow: theme.palette.mode === "dark" ? "0 1px 0 rgba(0,0,0,0.4)" : "none",
              }}
            >
              {`Selected algorithm: ${String(data.modelMeta?.selectedAlgorithm || "unknown").toUpperCase()}`}
            </Typography>
            <Typography variant="body2" sx={{ mt: 0.6, color: theme.palette.text.secondary }}>
              {`Best silhouette score: ${Number(data.modelMeta?.bestSilhouetteScore || 0).toFixed(2)}`}
            </Typography>

            <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} sx={{ mt: 1.5 }}>
              <Chip label={`Cluster by: ${clusterBy}`} variant="outlined" />
              <Chip label={`Cluster count: ${clusterCount}`} variant="outlined" />
            </Stack>

            {Array.isArray(data.modelMeta?.candidateScores) && data.modelMeta.candidateScores.length > 0 ? (
              <Stack direction="row" spacing={1.5} sx={{ mt: 1.5, flexWrap: "wrap" }} useFlexGap>
                {data.modelMeta.candidateScores.map((candidate) => (
                  <Chip
                    key={candidate.algorithm}
                    label={`${candidate.algorithm.toUpperCase()} ${Number(candidate.silhouetteScore || 0).toFixed(2)}`}
                    variant="outlined"
                    sx={{
                      color: theme.palette.text.primary,
                      borderColor: alpha(theme.palette.divider, theme.palette.mode === "dark" ? 0.55 : 0.85),
                      bgcolor:
                        theme.palette.mode === "dark"
                          ? alpha(theme.palette.background.paper, 0.55)
                          : alpha("#FFFFFF", 0.75),
                    }}
                  />
                ))}
              </Stack>
            ) : null}
          </Paper>
        </Grid>

        <Grid item xs={12} md={4}>
          <Paper sx={{ p: 2.5, height: "100%" }}>
            <Stack direction="row" spacing={1.4} alignItems="center">
              <HubRoundedIcon color="primary" />
              <Box>
                <Typography variant="subtitle2" color="text.secondary">
                  Number of Clusters
                </Typography>
                <Typography variant="h4">{data.totalClusters}</Typography>
              </Box>
            </Stack>
          </Paper>
        </Grid>

        <Grid item xs={12} md={4}>
          <Paper sx={{ p: 2.5, height: "100%" }}>
            <Stack direction="row" spacing={1.4} alignItems="center">
              <AutoAwesomeRoundedIcon color="secondary" />
              <Box>
                <Typography variant="subtitle2" color="text.secondary">
                  Optimal K
                </Typography>
                <Typography variant="h4">{data.optimalK}</Typography>
              </Box>
            </Stack>
          </Paper>
        </Grid>

        <Grid item xs={12} md={4}>
          <Paper sx={{ p: 2.5, height: "100%" }}>
            <Stack direction="row" spacing={1.4} alignItems="center">
              <SpeedRoundedIcon color="success" />
              <Box sx={{ flexGrow: 1 }}>
                <Typography variant="subtitle2" color="text.secondary">
                  Silhouette Score
                </Typography>
                <Typography variant="h4">{data.silhouetteScore}</Typography>
              </Box>
            </Stack>
            <LinearProgress
              variant="determinate"
              value={data.silhouetteScore * 100}
              sx={{ mt: 2, height: 10, borderRadius: 999 }}
            />
          </Paper>
        </Grid>

        <Grid item xs={12} lg={4}>
          <ChartCard
            title="Cluster Distribution"
            subtitle="Customer counts inside each K-Means output cluster."
            height={340}
          >
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={clusterDistribution}
                  dataKey="customers"
                  nameKey="label"
                  outerRadius={110}
                  innerRadius={60}
                  paddingAngle={4}
                >
                  {clusterDistribution.map((entry) => (
                    <Cell key={entry.cluster} fill={entry.fill} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={chartTooltipStyle}
                  labelStyle={chartTooltipLabelStyle}
                  itemStyle={chartTooltipItemStyle}
                />
                <Legend
                  verticalAlign="bottom"
                  formatter={(value) => (
                    <span style={{ color: theme.palette.text.secondary, fontWeight: 700 }}>{value}</span>
                  )}
                />
              </PieChart>
            </ResponsiveContainer>
          </ChartCard>
        </Grid>

        <Grid item xs={12} lg={8}>
          <ChartCard
            title="RFM Scatter Plot"
            subtitle="Recency vs frequency, with bubble size representing monetary value."
            height={360}
          >
            <ResponsiveContainer width="100%" height="100%">
              <ScatterChart margin={{ top: 14, right: 20, bottom: 12, left: 6 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={alpha(theme.palette.divider, 0.9)} />
                <XAxis
                  type="number"
                  dataKey="recency"
                  name="Recency"
                  tick={{ fill: theme.palette.text.secondary }}
                />
                <YAxis
                  type="number"
                  dataKey="frequency"
                  name="Frequency"
                  tick={{ fill: theme.palette.text.secondary }}
                />
                <ZAxis type="number" dataKey="monetary" range={[130, 560]} name="Monetary" />
                <Tooltip
                  cursor={{ strokeDasharray: "4 4", stroke: alpha("#E2E8F0", 0.82) }}
                  contentStyle={chartTooltipStyle}
                  labelStyle={chartTooltipLabelStyle}
                  itemStyle={chartTooltipItemStyle}
                  formatter={(value, name) => {
                    if (name === "Monetary") {
                      return [formatCurrency(value), name];
                    }
                    return [Number(value).toFixed(2), name];
                  }}
                />
                <Legend
                  formatter={(value) => (
                    <span style={{ color: theme.palette.text.secondary, fontWeight: 700 }}>{value}</span>
                  )}
                />
                {clusterSeries.map((cluster) => (
                  <Scatter
                    key={cluster.cluster}
                    name={cluster.label}
                    data={cluster.points}
                    fill={cluster.fill}
                    fillOpacity={0.96}
                    stroke={alpha(cluster.fill, 0.78)}
                    strokeWidth={1.4}
                  />
                ))}
              </ScatterChart>
            </ResponsiveContainer>
          </ChartCard>
        </Grid>

        <Grid item xs={12} lg={6}>
          <ChartCard
            title="Elbow Method"
            subtitle="Within-cluster sum of squares across different K values."
            height={290}
          >
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={data.elbowMethod}>
                <CartesianGrid strokeDasharray="3 3" stroke={theme.palette.divider} />
                <XAxis dataKey="k" tick={{ fill: theme.palette.text.secondary }} />
                <YAxis tick={{ fill: theme.palette.text.secondary }} />
                <Tooltip
                  contentStyle={chartTooltipStyle}
                  labelStyle={chartTooltipLabelStyle}
                  itemStyle={chartTooltipItemStyle}
                />
                <Line
                  type="monotone"
                  dataKey="inertia"
                  stroke={theme.palette.secondary.main}
                  strokeWidth={3}
                  dot={{ r: 4 }}
                />
              </LineChart>
            </ResponsiveContainer>
          </ChartCard>
        </Grid>

        <Grid item xs={12} lg={6}>
          <Paper sx={{ p: 2.5, height: "100%" }}>
            <Stack direction="row" spacing={1.3} alignItems="center">
              <InsightsRoundedIcon color="secondary" />
              <Box>
                <Typography variant="h6">Cluster Profiles</Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mt: 0.6 }}>
                  Average RFM values help interpret each segment in business language.
                </Typography>
              </Box>
            </Stack>

            <Stack spacing={1.5} sx={{ mt: 2.4 }}>
              {clusterProfiles.map((profile) => (
                <Box
                  key={profile.cluster}
                  sx={{
                    p: 2,
                    borderRadius: "24px",
                    backgroundColor: alpha(profile.fill, 0.08),
                    border: "1px solid",
                    borderColor: alpha(profile.fill, 0.22),
                  }}
                >
                  <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>
                    {profile.cluster} - {profile.label}
                  </Typography>
                  <Typography variant="body2" color="text.secondary" sx={{ mt: 0.8 }}>
                    Avg Recency: {profile.avgRecency} days - Avg Frequency: {profile.avgFrequency} - Avg Monetary: {formatCurrency(profile.avgMonetary)}
                  </Typography>
                </Box>
              ))}
            </Stack>
          </Paper>
        </Grid>
      </Grid>
    </Box>
  );
};

export default Results;
