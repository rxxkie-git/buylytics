import AutoGraphRoundedIcon from "@mui/icons-material/AutoGraphRounded";
import BubbleChartRoundedIcon from "@mui/icons-material/BubbleChartRounded";
import InsightsRoundedIcon from "@mui/icons-material/InsightsRounded";
import QueryStatsRoundedIcon from "@mui/icons-material/QueryStatsRounded";
import RefreshRoundedIcon from "@mui/icons-material/RefreshRounded";
import {
  alpha,
  Box,
  Button,
  FormControl,
  Grid,
  InputLabel,
  MenuItem,
  Paper,
  Select,
  Stack,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import { useEffect, useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  LineChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useNavigate } from "react-router-dom";
import ChartCard from "../components/ChartCard";
import DataTable from "../components/DataTable";
import ErrorState from "../components/ErrorState";
import LoadingState from "../components/LoadingState";
import NoDatasetState from "../components/NoDatasetState";
import PageHeader from "../components/PageHeader";
import { getProductAnalysisData } from "../services/api";
import { getProductDatasetId } from "../utils/storage";
import { formatCurrency, formatNumber, formatPercent } from "../utils/formatters";

const emptyProductAnalysis = {
  generatedAt: "",
  salesAnalysis: {
    available: false,
    summary: {
      productsAnalyzed: 0,
      totalQuantitySold: 0,
      totalRevenue: 0,
      averageSalesPerProduct: 0,
    },
    productPerformanceTable: [],
    topSellingProducts: [],
    leastSellingProducts: [],
    warnings: [],
  },
  productClustering: {
    available: false,
    optimalClusters: 0,
    elbowMethod: [],
    clusteredProducts: [],
    clusterProfiles: [],
    warnings: [],
  },
  demandAnalysis: {
    available: false,
    frequency: null,
    timeSeries: [],
    summary: {},
    warnings: [],
  },
  salesForecast: {
    available: false,
    modelUsed: null,
    frequency: null,
    forecastPeriods: 0,
    history: [],
    forecast: [],
    warnings: [],
  },
  visualizations: {
    topProductsBarChart: { data: [] },
    clusterScatterPlot: { data: [] },
    demandTimeSeriesChart: { data: [] },
    forecastChart: { history: [], forecast: [] },
  },
};

const performanceColumns = [
  { id: "rank", label: "Rank", minWidth: 80, render: (value) => Number(value || 0) || "-" },
  { id: "product", label: "Product", minWidth: 180 },
  {
    id: "total_quantity_sold",
    label: "Total Quantity",
    minWidth: 130,
    render: (value) => formatNumber(value),
  },
  {
    id: "sales_frequency",
    label: "Sales Frequency",
    minWidth: 130,
    render: (value) => formatNumber(value),
  },
  {
    id: "average_sales",
    label: "Avg Sales",
    minWidth: 130,
    render: (value) => formatCurrency(value),
  },
  {
    id: "total_revenue",
    label: "Total Revenue",
    minWidth: 150,
    render: (value) => formatCurrency(value),
  },
];

const movementColors = {
  "Fast-moving": "#0F9D8A",
  "Medium-moving": "#2563EB",
  "Slow-moving": "#F97316",
};

const ProductAnalysis = () => {
  const navigate = useNavigate();
  const theme = useTheme();

  const [data, setData] = useState(emptyProductAnalysis);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [frequency, setFrequency] = useState("auto");
  const [forecastPeriods, setForecastPeriods] = useState(12);
  const [clusterBy, setClusterBy] = useState("default");
  const [clusterCount, setClusterCount] = useState(4);

  const runAnalysis = async (forceRefresh = false) => {
    setLoading(true);
    setError("");

    try {
      const response = await getProductAnalysisData({
        frequency,
        forecastPeriods: Number(forecastPeriods) || 12,
        clusterBy,
        clusterCount: Number(clusterCount) || 4,
        forceRefresh,
      });
      setData(response || emptyProductAnalysis);
    } catch (fetchError) {
      setError(
        fetchError?.response?.data?.detail ||
          fetchError?.response?.data?.message ||
          fetchError?.message ||
          "Unable to load product analysis."
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    runAnalysis(false);
  }, [frequency, forecastPeriods, clusterBy, clusterCount]);

  const warnings = useMemo(() => {
    const allWarnings = [
      ...(data?.salesAnalysis?.warnings || []),
      ...(data?.productClustering?.warnings || []),
      ...(data?.demandAnalysis?.warnings || []),
      ...(data?.salesForecast?.warnings || []),
    ].filter(Boolean);
    return Array.from(new Set(allWarnings));
  }, [data]);

  const topProducts = data?.visualizations?.topProductsBarChart?.data || [];
  const clusteredProducts = data?.visualizations?.clusterScatterPlot?.data || [];
  const demandSeries = data?.demandAnalysis?.timeSeries || [];

  const forecastSeries = useMemo(() => {
    const map = new Map();
    (data?.salesForecast?.history || []).forEach((item) => {
      map.set(item.period, {
        period: item.period,
        actual: Number(item.total_sales || 0),
        forecast: null,
      });
    });
    (data?.salesForecast?.forecast || []).forEach((item) => {
      if (map.has(item.period)) {
        const existing = map.get(item.period);
        existing.forecast = Number(item.predicted_sales || 0);
      } else {
        map.set(item.period, {
          period: item.period,
          actual: null,
          forecast: Number(item.predicted_sales || 0),
        });
      }
    });

    const sorted = Array.from(map.values()).sort(
      (left, right) => new Date(left.period).getTime() - new Date(right.period).getTime()
    );

    let lastActualIndex = -1;
    for (let i = 0; i < sorted.length; i++) {
      if (sorted[i].actual !== null) {
        lastActualIndex = i;
      }
    }

    if (lastActualIndex !== -1 && lastActualIndex + 1 < sorted.length) {
      sorted[lastActualIndex].forecast = sorted[lastActualIndex].actual;
    }

    return sorted;
  }, [data]);

  const clusterSeries = useMemo(
    () =>
      Object.keys(movementColors).map((label) => ({
        label,
        color: movementColors[label],
        data: clusteredProducts.filter((row) => row.movement_label === label),
      })),
    [clusteredProducts]
  );

  if (loading) {
    return (
      <LoadingState
        title="Loading product analytics..."
        description="Computing product performance, movement clusters, and sales forecasts."
      />
    );
  }

  if (error) {
    return <ErrorState message={error} onRetry={() => runAnalysis(true)} />;
  }

  const hasData = Boolean(
    data?.salesAnalysis?.summary?.productsAnalyzed > 0 ||
      (data?.salesAnalysis?.productPerformanceTable && data.salesAnalysis.productPerformanceTable.length > 0)
  );

  if (!hasData) {
    return (
      <NoDatasetState
        message="No product or order records found. Import products or orders to view product intelligence, movement clusters, and sales forecasts."
        buttonText="Import Data"
        uploadPath="/app/import"
      />
    );
  }

  return (
    <Box>
      <PageHeader
        eyebrow="Product Intelligence"
        title="Store product sales analysis and demand prediction"
        subtitle="Analyze best and worst products, segment movement behavior, evaluate demand trends, and project future sales from your store data."
        chipLabel={data?.salesForecast?.modelUsed ? `Forecast: ${data.salesForecast.modelUsed}` : "Analysis ready"}
        actionLabel="Import Data"
        onActionClick={() => navigate("/app/import")}
      />

      <Grid container spacing={3}>
        <Grid item xs={12}>
          <Paper sx={{ p: 2.4 }}>
            <Stack
              direction={{ xs: "column", md: "row" }}
              spacing={1.8}
              alignItems={{ xs: "stretch", md: "center" }}
            >
              <FormControl size="small" sx={{ minWidth: 180 }}>
                <InputLabel id="frequency-select-label">Trend Frequency</InputLabel>
                <Select
                  labelId="frequency-select-label"
                  label="Trend Frequency"
                  value={frequency}
                  onChange={(event) => setFrequency(event.target.value)}
                >
                  <MenuItem value="auto">Auto</MenuItem>
                  <MenuItem value="daily">Daily</MenuItem>
                  <MenuItem value="monthly">Monthly</MenuItem>
                </Select>
              </FormControl>

              <FormControl size="small" sx={{ minWidth: 220 }}>
                <InputLabel id="cluster-by-select-label">Cluster By</InputLabel>
                <Select
                  labelId="cluster-by-select-label"
                  label="Cluster By"
                  value={clusterBy}
                  onChange={(event) => setClusterBy(event.target.value)}
                >
                  <MenuItem value="default">Default (Qty, Revenue, Frequency)</MenuItem>
                  <MenuItem value="quantity_revenue">Quantity + Revenue</MenuItem>
                  <MenuItem value="quantity_frequency">Quantity + Frequency</MenuItem>
                  <MenuItem value="revenue_frequency">Revenue + Frequency</MenuItem>
                  <MenuItem value="quantity">Quantity only</MenuItem>
                  <MenuItem value="revenue">Revenue only</MenuItem>
                  <MenuItem value="frequency">Frequency only</MenuItem>
                </Select>
              </FormControl>

              <TextField
                size="small"
                label="Cluster Count"
                type="number"
                value={clusterCount}
                onChange={(event) => setClusterCount(Number(event.target.value))}
                inputProps={{ min: 2, max: 12 }}
                sx={{ width: 140 }}
              />

              <TextField
                size="small"
                label="Forecast Periods"
                type="number"
                value={forecastPeriods}
                onChange={(event) => setForecastPeriods(Number(event.target.value))}
                inputProps={{ min: 1, max: 120 }}
                sx={{ width: 180 }}
              />

              <Button
                variant="contained"
                startIcon={<RefreshRoundedIcon />}
                onClick={() => runAnalysis(true)}
              >
                Run Analysis
              </Button>

              <Typography variant="body2" color="text.secondary" sx={{ ml: { md: "auto" } }}>
                {`Generated at: ${data.generatedAt ? new Date(data.generatedAt).toLocaleString() : "-"}`}
              </Typography>
            </Stack>
          </Paper>
        </Grid>

          <Grid item xs={12}>
            <Paper sx={{ p: 2.4, mb: 2 }}>
              <Typography variant="subtitle2" color="text.secondary">
                Cluster option: {data?.productClustering?.clusterBy || clusterBy}
              </Typography>
            </Paper>
          </Grid>

        <Grid item xs={12} md={3}>
          <Paper sx={{ p: 2.4, height: "100%" }}>
            <Stack direction="row" spacing={1.2} alignItems="center">
              <AutoGraphRoundedIcon color="primary" />
              <Box>
                <Typography variant="subtitle2" color="text.secondary">
                  Products Analyzed
                </Typography>
                <Typography variant="h4">
                  {formatNumber(data?.salesAnalysis?.summary?.productsAnalyzed)}
                </Typography>
              </Box>
            </Stack>
          </Paper>
        </Grid>

        <Grid item xs={12} md={3}>
          <Paper sx={{ p: 2.4, height: "100%" }}>
            <Stack direction="row" spacing={1.2} alignItems="center">
              <AutoGraphRoundedIcon color="secondary" />
              <Box>
                <Typography variant="subtitle2" color="text.secondary">
                  Total Quantity Sold
                </Typography>
                <Typography variant="h4">
                  {formatNumber(data?.salesAnalysis?.summary?.totalQuantitySold)}
                </Typography>
              </Box>
            </Stack>
          </Paper>
        </Grid>

        <Grid item xs={12} md={3}>
          <Paper sx={{ p: 2.4, height: "100%" }}>
            <Stack direction="row" spacing={1.2} alignItems="center">
              <QueryStatsRoundedIcon color="success" />
              <Box>
                <Typography variant="subtitle2" color="text.secondary">
                  Total Revenue
                </Typography>
                <Typography variant="h4">
                  {formatCurrency(data?.salesAnalysis?.summary?.totalRevenue)}
                </Typography>
              </Box>
            </Stack>
          </Paper>
        </Grid>

        <Grid item xs={12} md={3}>
          <Paper sx={{ p: 2.4, height: "100%" }}>
            <Stack direction="row" spacing={1.2} alignItems="center">
              <BubbleChartRoundedIcon color="warning" />
              <Box>
                <Typography variant="subtitle2" color="text.secondary">
                  Optimal Clusters
                </Typography>
                <Typography variant="h4">
                  {formatNumber(data?.productClustering?.optimalClusters)}
                </Typography>
              </Box>
            </Stack>
          </Paper>
        </Grid>

        <Grid item xs={12} lg={6}>
          <ChartCard
            title="Top Products by Revenue"
            subtitle="Highest-performing products from grouped sales analysis."
            height={320}
          >
            {topProducts.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={topProducts.slice(0, 10)}>
                  <CartesianGrid strokeDasharray="3 3" stroke={theme.palette.divider} />
                  <XAxis
                    dataKey="product"
                    tick={{ fill: theme.palette.text.secondary, fontSize: 11 }}
                    interval={0}
                    angle={-20}
                    textAnchor="end"
                    height={70}
                  />
                  <YAxis tick={{ fill: theme.palette.text.secondary }} width={75} />
                  <Tooltip
                    cursor={{ fill: alpha(theme.palette.text.primary, 0.05) }}
                    contentStyle={{
                      backgroundColor: theme.palette.background.paper,
                      border: `1px solid ${theme.palette.divider}`,
                      borderRadius: 12,
                      color: theme.palette.text.primary,
                    }}
                    itemStyle={{ color: theme.palette.text.primary }}
                    formatter={(value, key) =>
                      key === "total_revenue" ? [formatCurrency(value), "Revenue"] : [value, key]
                    }
                  />
                  <Bar dataKey="total_revenue" radius={[10, 10, 0, 0]}>
                    {topProducts.slice(0, 10).map((entry) => (
                      <Cell
                        key={entry.product}
                        fill={entry.rank <= 3 ? "#0F9D8A" : theme.palette.primary.main}
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <Typography variant="body2" color="text.secondary">
                No top-product chart data available.
              </Typography>
            )}
          </ChartCard>
        </Grid>

        <Grid item xs={12} lg={6}>
          <ChartCard
            title="Product Movement Clusters"
            subtitle="Quantity vs revenue with fast, medium, and slow-moving labels."
            height={320}
          >
            {clusteredProducts.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <ScatterChart>
                  <CartesianGrid strokeDasharray="3 3" stroke={theme.palette.divider} />
                  <XAxis
                    type="number"
                    dataKey="total_quantity_sold"
                    name="Total Quantity"
                    tick={{ fill: theme.palette.text.secondary }}
                  />
                  <YAxis
                    type="number"
                    dataKey="total_revenue"
                    name="Total Revenue"
                    tick={{ fill: theme.palette.text.secondary }}
                    width={75}
                  />
                  <Tooltip
                    cursor={{ strokeDasharray: '3 3', stroke: alpha(theme.palette.text.primary, 0.2) }}
                    contentStyle={{
                      backgroundColor: theme.palette.background.paper,
                      border: `1px solid ${theme.palette.divider}`,
                      borderRadius: 12,
                      color: theme.palette.text.primary,
                    }}
                    itemStyle={{ color: theme.palette.text.primary }}
                    formatter={(value, name) =>
                      name === "Total Revenue" ? [formatCurrency(value), name] : [value, name]
                    }
                  />
                  {clusterSeries.map((series) => (
                    <Scatter
                      key={series.label}
                      name={series.label}
                      data={series.data}
                      fill={series.color}
                    />
                  ))}
                </ScatterChart>
              </ResponsiveContainer>
            ) : (
              <Typography variant="body2" color="text.secondary">
                Clustering data is not available for this dataset.
              </Typography>
            )}
          </ChartCard>
        </Grid>

        <Grid item xs={12} lg={6}>
          <ChartCard
            title="Demand Trends"
            subtitle="Historical sales with moving average and growth trend."
            height={320}
          >
            {demandSeries.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={demandSeries}>
                  <defs>
                    <linearGradient id="colorDemand" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor={theme.palette.primary.main} stopOpacity={0.3} />
                      <stop offset="95%" stopColor={theme.palette.primary.main} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke={theme.palette.divider} vertical={false} />
                  <XAxis dataKey="period" tick={{ fill: theme.palette.text.secondary }} minTickGap={30} />
                  <YAxis
                    tick={{ fill: theme.palette.text.secondary }}
                    tickFormatter={(val) => new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(val)}
                    width={50}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: theme.palette.background.paper,
                      border: `1px solid ${theme.palette.divider}`,
                      borderRadius: 12,
                      color: theme.palette.text.primary,
                    }}
                    itemStyle={{ color: theme.palette.text.primary }}
                    formatter={(value, key) => {
                      if (key === "total_sales") return [formatCurrency(value), "Total Sales"];
                      if (key === "moving_average") return [formatCurrency(value), "Average Sales"];
                      return [value, key];
                    }}
                  />
                  <Area
                    type="monotone"
                    dataKey="total_sales"
                    stroke={theme.palette.primary.main}
                    strokeWidth={2.8}
                    fillOpacity={1}
                    fill="url(#colorDemand)"
                  />
                  <Line
                    type="monotone"
                    dataKey="moving_average"
                    stroke={theme.palette.secondary.main}
                    strokeWidth={2.2}
                    dot={false}
                  />
                </ComposedChart>
              </ResponsiveContainer>
            ) : (
              <Typography variant="body2" color="text.secondary">
                Demand trend analysis requires valid date information.
              </Typography>
            )}
          </ChartCard>
        </Grid>

        <Grid item xs={12} lg={6}>
          <ChartCard
            title="Forecast"
            subtitle="Historical sales and predicted future demand."
            height={320}
          >
            {data?.salesForecast?.available && forecastSeries.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={forecastSeries}>
                  <defs>
                    <linearGradient id="colorActual" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor={theme.palette.primary.main} stopOpacity={0.3} />
                      <stop offset="95%" stopColor={theme.palette.primary.main} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke={theme.palette.divider} vertical={false} />
                  <XAxis dataKey="period" tick={{ fill: theme.palette.text.secondary }} minTickGap={30} />
                  <YAxis
                    tick={{ fill: theme.palette.text.secondary }}
                    tickFormatter={(val) => new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(val)}
                    width={50}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: theme.palette.background.paper,
                      border: `1px solid ${theme.palette.divider}`,
                      borderRadius: 12,
                      color: theme.palette.text.primary,
                    }}
                    itemStyle={{ color: theme.palette.text.primary }}
                    formatter={(value, key) =>
                      key === "actual" || key === "forecast"
                        ? [formatCurrency(value), key === "actual" ? "Actual Sales" : "Forecast Sales"]
                        : [value, key]
                    }
                  />
                  <Area
                    type="monotone"
                    dataKey="actual"
                    stroke={theme.palette.primary.main}
                    strokeWidth={2.8}
                    fillOpacity={1}
                    fill="url(#colorActual)"
                  />
                  <Line
                    type="monotone"
                    dataKey="forecast"
                    stroke="#F97316"
                    strokeWidth={2.8}
                    strokeDasharray="6 4"
                    dot={false}
                  />
                </ComposedChart>
              </ResponsiveContainer>
            ) : (
              <Typography variant="body2" color="text.secondary">
                Forecast data is unavailable. Ensure enough historical periods with date and sales values.
              </Typography>
            )}
          </ChartCard>
        </Grid>

        <Grid item xs={12}>
          <Paper sx={{ p: 2.4 }}>
            <Typography variant="h6">Product Performance Table</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.7, mb: 2 }}>
              Ranked table of products by revenue, quantity sold, and sales frequency.
            </Typography>
            <DataTable
              columns={performanceColumns}
              rows={data?.salesAnalysis?.productPerformanceTable || []}
              defaultOrderBy="total_revenue"
              initialRowsPerPage={10}
              highlightMissingValues={false}
            />
          </Paper>
        </Grid>

        {data?.productClustering?.clusterProfiles?.length > 0 ? (
          <Grid item xs={12}>
            <Paper sx={{ p: 2.4 }}>
              <Typography variant="h6">Cluster Profiles</Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mt: 0.7 }}>
                Aggregated profile per cluster used for fast/medium/slow movement labels.
              </Typography>
              <Grid container spacing={2} sx={{ mt: 0.4 }}>
                {data.productClustering.clusterProfiles.map((profile) => (
                  <Grid item xs={12} md={4} key={`${profile.cluster_id}-${profile.movement_label}`}>
                    <Paper
                      variant="outlined"
                      sx={{
                        p: 2,
                        borderRadius: 3,
                        borderColor: movementColors[profile.movement_label] || "divider",
                      }}
                    >
                      <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>
                        {`Cluster ${profile.cluster_id} - ${profile.movement_label}`}
                      </Typography>
                      <Typography variant="body2" color="text.secondary" sx={{ mt: 0.7 }}>
                        {`Products: ${formatNumber(profile.products)} | Avg Qty: ${formatNumber(profile.avg_quantity_sold)}`}
                      </Typography>
                      <Typography variant="body2" color="text.secondary">
                        {`Avg Revenue: ${formatCurrency(profile.avg_total_revenue)} | Avg Frequency: ${formatNumber(profile.avg_frequency)}`}
                      </Typography>
                    </Paper>
                  </Grid>
                ))}
              </Grid>
            </Paper>
          </Grid>
        ) : null}

        {warnings.length > 0 ? (
          <Grid item xs={12}>
            <Paper sx={{ p: 2.2 }}>
              <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>
                Analysis Notes
              </Typography>
              <Stack spacing={0.7} sx={{ mt: 1.2 }}>
                {warnings.map((warning) => (
                  <Typography key={warning} variant="body2" color="text.secondary">
                    - {warning}
                  </Typography>
                ))}
              </Stack>
            </Paper>
          </Grid>
        ) : null}
      </Grid>
    </Box>
  );
};

export default ProductAnalysis;
