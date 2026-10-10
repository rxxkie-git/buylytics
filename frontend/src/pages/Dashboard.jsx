import AttachMoneyRoundedIcon from "@mui/icons-material/AttachMoneyRounded";
import BubbleChartRoundedIcon from "@mui/icons-material/BubbleChartRounded";
import GroupsRoundedIcon from "@mui/icons-material/GroupsRounded";
import ShoppingBagRoundedIcon from "@mui/icons-material/ShoppingBagRounded";
import { alpha, Box, Grid, Paper, Stack, Typography, useTheme } from "@mui/material";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useNavigate } from "react-router-dom";
import { getDatasetId } from "../utils/storage";
import ChartCard from "../components/ChartCard";
import ErrorState from "../components/ErrorState";
import KpiCard from "../components/KpiCard";
import LoadingState from "../components/LoadingState";
import NoDatasetState from "../components/NoDatasetState";
import PageHeader from "../components/PageHeader";
import useApiData from "../hooks/useApiData";
import { getDashboardData } from "../services/api";
import { formatCurrency } from "../utils/formatters";

const emptyDashboard = {
  kpis: [],
  purchaseTrends: [],
  frequencyDistribution: [],
  segmentMix: [],
  summaryCards: [],
  modelMeta: {
    selectedAlgorithm: "unknown",
    bestSilhouetteScore: 0,
    candidateScores: [],
  },
};

const kpiIcons = [
  <GroupsRoundedIcon />,
  <AttachMoneyRoundedIcon />,
  <ShoppingBagRoundedIcon />,
];

// Dashboard page surfaces KPIs and business visuals at a glance.
const Dashboard = () => {
  const navigate = useNavigate();
  const theme = useTheme();
  const hasDataset = Boolean(getDatasetId());
  const { data, loading, error, reload } = useApiData(getDashboardData, emptyDashboard);

  if (loading && hasDataset) {
    return (
      <LoadingState
        title="Loading dashboard..."
        description="Aggregating KPIs, trends, and customer distribution."
      />
    );
  }

  if (!hasDataset) {
    return (
      <NoDatasetState 
        message="Please upload a customer dataset to view your dashboard insights and analytics."
        buttonText="Upload Dataset"
        uploadPath="/upload"
      />
    );
  }

  if (error) {
    return <ErrorState message={error} onRetry={reload} />;
  }

  return (
    <Box>
      <PageHeader
        eyebrow="Executive Overview"
        title="Business performance and customer behavior in one dashboard"
        subtitle="Monitor revenue, customer value, purchase trends, and cohort distribution before moving into deeper clustering analysis."
        chipLabel={
          data.uploadMeta?.fileName ? `Source: ${data.uploadMeta.fileName}` : "Demo dataset active"
        }
        actionLabel="Open Analysis Results"
        onActionClick={() => navigate("/results")}
      />

      <Grid container spacing={3}>
        {data.kpis.map((kpi, index) => (
          <Grid item xs={12} md={4} key={kpi.id}>
            <KpiCard {...kpi} icon={kpiIcons[index]} />
          </Grid>
        ))}

        <Grid item xs={12} lg={8}>
          <ChartCard
            title="Purchase Trends"
            subtitle="Monthly revenue and order movement from the uploaded retail dataset."
            height={330}
          >
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={data.purchaseTrends}>
                <defs>
                  <linearGradient id="revenueArea" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={theme.palette.primary.main} stopOpacity={0.42} />
                    <stop offset="100%" stopColor={theme.palette.primary.main} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke={theme.palette.divider} />
                <XAxis dataKey="month" tick={{ fill: theme.palette.text.secondary }} />
                <YAxis
                  tick={{ fill: theme.palette.text.secondary }}
                  width={75}
                  tickFormatter={(value) =>
                    new Intl.NumberFormat("en-US", {
                      notation: "compact",
                      compactDisplay: "short",
                    }).format(value)
                  }
                />
                <Tooltip
                  contentStyle={{
                    backgroundColor: theme.palette.background.paper,
                    border: `1px solid ${theme.palette.divider}`,
                    borderRadius: 14,
                    color: theme.palette.text.primary,
                  }}
                  itemStyle={{ color: theme.palette.text.primary }}
                  formatter={(value, name) =>
                    name === "revenue" ? [formatCurrency(value), "Revenue"] : [value, "Orders"]
                  }
                />
                <Legend />
                <Area
                  type="monotone"
                  dataKey="revenue"
                  stroke={theme.palette.primary.main}
                  fill="url(#revenueArea)"
                  strokeWidth={3}
                />
              </AreaChart>
            </ResponsiveContainer>
          </ChartCard>
        </Grid>

        <Grid item xs={12} lg={4}>
          <ChartCard
            title="Segment Mix"
            subtitle="How the customer base is distributed across clustered groups."
            height={330}
          >
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={data.segmentMix}
                  dataKey="value"
                  nameKey="name"
                  innerRadius={70}
                  outerRadius={104}
                  paddingAngle={4}
                >
                  {data.segmentMix.map((entry) => (
                    <Cell key={entry.name} fill={entry.fill} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{
                    backgroundColor: theme.palette.background.paper,
                    border: `1px solid ${theme.palette.divider}`,
                    borderRadius: 14,
                    color: theme.palette.text.primary,
                  }}
                  itemStyle={{ color: theme.palette.text.primary }}
                />
                <Legend verticalAlign="bottom" />
              </PieChart>
            </ResponsiveContainer>
          </ChartCard>
        </Grid>

        <Grid item xs={12} lg={7}>
          <ChartCard
            title="Frequency Distribution"
            subtitle="Customer counts grouped by purchase frequency bands."
            height={300}
          >
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data.frequencyDistribution}>
                <CartesianGrid strokeDasharray="3 3" stroke={theme.palette.divider} />
                <XAxis dataKey="bucket" tick={{ fill: theme.palette.text.secondary }} />
                <YAxis tick={{ fill: theme.palette.text.secondary }} width={60} />
                <Tooltip
                  cursor={{ fill: alpha(theme.palette.text.primary, 0.05) }}
                  formatter={(value) => [value, "Customers"]}
                  contentStyle={{
                    backgroundColor: theme.palette.background.paper,
                    border: `1px solid ${theme.palette.divider}`,
                    borderRadius: 14,
                    color: theme.palette.text.primary,
                  }}
                  itemStyle={{ color: theme.palette.text.primary }}
                />
                <Bar dataKey="customers" radius={[10, 10, 0, 0]} fill={theme.palette.secondary.main} />
              </BarChart>
            </ResponsiveContainer>
          </ChartCard>
        </Grid>

        <Grid item xs={12} lg={5}>
          <Paper sx={{ p: 2.5, height: "100%" }}>
            <Typography variant="h6">Operational Snapshot</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.8 }}>
              Use this panel to understand dataset health and readiness before retraining the
              clustering model.
            </Typography>

            <Stack spacing={1.6} sx={{ mt: 2.5 }}>
              {data.summaryCards.map((card, index) => (
                <Box
                  key={card.title}
                  sx={{
                    p: 2,
                    borderRadius: "24px",
                    backgroundColor:
                      index === 0
                        ? alpha(theme.palette.primary.main, 0.08)
                        : alpha(theme.palette.secondary.main, 0.06),
                    border: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  <Typography variant="subtitle2" color="text.secondary">
                    {card.title}
                  </Typography>
                  <Typography variant="h5" sx={{ mt: 0.8 }}>
                    {card.value}
                  </Typography>
                  <Typography variant="body2" color="text.secondary" sx={{ mt: 0.7 }}>
                    {card.subtitle}
                  </Typography>
                </Box>
              ))}
            </Stack>

            <Paper
              sx={{
                mt: 2.2,
                p: 2,
                borderRadius: "28px",
                background:
                  "linear-gradient(135deg, rgba(15,118,110,0.16), rgba(37,99,235,0.12), rgba(249,115,22,0.1))",
              }}
            >
              <Stack direction="row" spacing={1.3} alignItems="center">
                <BubbleChartRoundedIcon color="secondary" />
                <Box>
                  <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>
                    Trained Model Summary
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    {`Selected: ${String(data.modelMeta?.selectedAlgorithm || "unknown").toUpperCase()} | Best silhouette: ${Number(data.modelMeta?.bestSilhouetteScore || 0).toFixed(2)}`}
                  </Typography>
                </Box>
              </Stack>

              {Array.isArray(data.modelMeta?.candidateScores) && data.modelMeta.candidateScores.length > 0 ? (
                <Stack spacing={0.6} sx={{ mt: 1.5 }}>
                  {data.modelMeta.candidateScores.map((candidate) => (
                    <Typography key={candidate.algorithm} variant="caption" color="text.secondary">
                      {`${candidate.algorithm.toUpperCase()}: silhouette ${Number(candidate.silhouetteScore || 0).toFixed(2)} | clusters ${candidate.clusterCount}`}
                    </Typography>
                  ))}
                </Stack>
              ) : null}
            </Paper>
          </Paper>
        </Grid>
      </Grid>
    </Box>
  );
};

export default Dashboard;
