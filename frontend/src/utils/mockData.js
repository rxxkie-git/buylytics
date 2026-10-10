import { CLUSTER_COLORS, SEGMENT_COLORS } from "./formatters";

const SEGMENT_BLUEPRINT = [
  {
    label: "High Value Customers",
    shortLabel: "High Value",
    cluster: "Cluster 1",
    count: 12,
    recencyBase: 3,
    recencyStep: 2,
    frequencyBase: 14,
    monetaryBase: 4200,
  },
  {
    label: "Loyal Customers",
    shortLabel: "Loyal",
    cluster: "Cluster 2",
    count: 14,
    recencyBase: 10,
    recencyStep: 2,
    frequencyBase: 9,
    monetaryBase: 2500,
  },
  {
    label: "At Risk Customers",
    shortLabel: "At Risk",
    cluster: "Cluster 3",
    count: 10,
    recencyBase: 38,
    recencyStep: 4,
    frequencyBase: 4,
    monetaryBase: 1350,
  },
  {
    label: "Low Value Customers",
    shortLabel: "Low Value",
    cluster: "Cluster 4",
    count: 12,
    recencyBase: 18,
    recencyStep: 3,
    frequencyBase: 2,
    monetaryBase: 620,
  },
];

const CHANNELS = ["Website", "Mobile App", "Marketplace", "Retail Store"];
const CITIES = [
  "Bengaluru",
  "Hyderabad",
  "Chennai",
  "Mumbai",
  "Pune",
  "Delhi",
  "Kochi",
  "Ahmedabad",
];
const BASE_DATE = new Date("2026-03-31T00:00:00");

const formatDate = (date) => date.toISOString().slice(0, 10);

const customers = SEGMENT_BLUEPRINT.flatMap((segment, segmentIndex) =>
  Array.from({ length: segment.count }, (_, itemIndex) => {
    const id = `CUST-${1001 + segmentIndex * 20 + itemIndex}`;
    const recency = segment.recencyBase + itemIndex * segment.recencyStep;
    const frequency = segment.frequencyBase + (itemIndex % 4) + Math.floor(itemIndex / 3);
    const monetary = segment.monetaryBase + itemIndex * 150 + (itemIndex % 3) * 120;
    const totalOrders = frequency + 2;
    const avgOrderValue = Math.round(monetary / Math.max(totalOrders, 1));
    const lastPurchase = new Date(BASE_DATE);

    lastPurchase.setDate(BASE_DATE.getDate() - recency);

    return {
      id,
      customerName: `Customer ${segmentIndex + 1}${itemIndex + 1}`,
      city: CITIES[(segmentIndex + itemIndex) % CITIES.length],
      preferredChannel:
        itemIndex % 6 === 0 ? null : CHANNELS[(segmentIndex + itemIndex) % CHANNELS.length],
      segment: segment.label,
      segmentShortLabel: segment.shortLabel,
      cluster: segment.cluster,
      recency,
      frequency,
      monetary,
      totalOrders,
      avgOrderValue: itemIndex % 7 === 0 ? null : avgOrderValue,
      lastPurchaseDate: itemIndex % 8 === 0 ? null : formatDate(lastPurchase),
      churnRisk: recency > 60 ? "High" : recency > 30 ? "Medium" : "Low",
    };
  })
);

const totalCustomers = customers.length;
const totalRevenue = customers.reduce((sum, customer) => sum + customer.monetary, 0);
const totalOrders = customers.reduce((sum, customer) => sum + customer.totalOrders, 0);
const avgPurchaseValue = totalRevenue / totalOrders;

const purchaseTrends = [
  { month: "Oct", revenue: 28500, orders: 144 },
  { month: "Nov", revenue: 32120, orders: 158 },
  { month: "Dec", revenue: 34780, orders: 170 },
  { month: "Jan", revenue: 38240, orders: 188 },
  { month: "Feb", revenue: 36540, orders: 175 },
  { month: "Mar", revenue: 40120, orders: 194 },
];

const frequencyDistribution = [
  { bucket: "1-4", customers: customers.filter((item) => item.frequency <= 4).length },
  {
    bucket: "5-8",
    customers: customers.filter((item) => item.frequency >= 5 && item.frequency <= 8).length,
  },
  {
    bucket: "9-12",
    customers: customers.filter((item) => item.frequency >= 9 && item.frequency <= 12).length,
  },
  {
    bucket: "13-16",
    customers: customers.filter((item) => item.frequency >= 13 && item.frequency <= 16).length,
  },
  {
    bucket: "17+",
    customers: customers.filter((item) => item.frequency >= 17).length,
  },
];

const segmentMix = SEGMENT_BLUEPRINT.map((segment, index) => ({
  name: segment.shortLabel,
  value: customers.filter((item) => item.segment === segment.label).length,
  fill: CLUSTER_COLORS[index],
}));

const clusterDistribution = SEGMENT_BLUEPRINT.map((segment, index) => ({
  cluster: segment.cluster,
  label: segment.shortLabel,
  customers: customers.filter((item) => item.cluster === segment.cluster).length,
  fill: CLUSTER_COLORS[index],
}));

const elbowMethod = [
  { k: 2, inertia: 1840 },
  { k: 3, inertia: 1210 },
  { k: 4, inertia: 820 },
  { k: 5, inertia: 700 },
  { k: 6, inertia: 648 },
];

const clusterProfiles = SEGMENT_BLUEPRINT.map((segment, index) => {
  const segmentCustomers = customers.filter((item) => item.segment === segment.label);

  return {
    cluster: segment.cluster,
    label: segment.shortLabel,
    fill: CLUSTER_COLORS[index],
    avgRecency: Number(
      (
        segmentCustomers.reduce((sum, item) => sum + item.recency, 0) / segmentCustomers.length
      ).toFixed(1)
    ),
    avgFrequency: Number(
      (
        segmentCustomers.reduce((sum, item) => sum + item.frequency, 0) / segmentCustomers.length
      ).toFixed(1)
    ),
    avgMonetary: Math.round(
      segmentCustomers.reduce((sum, item) => sum + item.monetary, 0) / segmentCustomers.length
    ),
  };
});

const previewColumns = [
  { id: "id", label: "Customer ID" },
  { id: "customerName", label: "Customer Name" },
  { id: "segment", label: "Segment" },
  { id: "recency", label: "Recency" },
  { id: "frequency", label: "Frequency" },
  { id: "monetary", label: "Monetary" },
  { id: "avgOrderValue", label: "Avg Order Value" },
  { id: "preferredChannel", label: "Channel" },
  { id: "lastPurchaseDate", label: "Last Purchase" },
];

const missingCells = customers.reduce(
  (sum, customer) =>
    sum +
    Object.values(customer).filter((value) => value === null || value === undefined || value === "")
      .length,
  0
);

const rowsWithMissingValues = customers.filter((customer) =>
  Object.values(customer).some((value) => value === null || value === undefined || value === "")
).length;

const sortedCustomersByRevenue = [...customers].sort((left, right) => right.monetary - left.monetary);
const topCustomerCount = Math.ceil(totalCustomers * 0.2);
const topRevenue = sortedCustomersByRevenue
  .slice(0, topCustomerCount)
  .reduce((sum, customer) => sum + customer.monetary, 0);
const topRevenueShare = (topRevenue / totalRevenue) * 100;
const inactiveCustomers = customers.filter((customer) => customer.recency >= 45).length;
const atRiskRevenue = customers
  .filter((customer) => customer.segment === "At Risk Customers")
  .reduce((sum, customer) => sum + customer.monetary, 0);

export const dashboardData = {
  kpis: [
    {
      id: "total-customers",
      label: "Total Customers",
      value: totalCustomers,
      delta: "+8.4%",
      tone: "success",
      description: "Active profiles in the latest uploaded dataset",
    },
    {
      id: "total-revenue",
      label: "Total Revenue",
      value: totalRevenue,
      delta: "+12.1%",
      tone: "primary",
      description: "Monetary contribution captured for clustering",
    },
    {
      id: "avg-purchase-value",
      label: "Avg Purchase Value",
      value: avgPurchaseValue,
      delta: "+4.6%",
      tone: "secondary",
      description: "Average value per transaction across customers",
    },
  ],
  purchaseTrends,
  frequencyDistribution,
  segmentMix,
  summaryCards: [
    {
      title: "Model Readiness",
      value: "96%",
      subtitle: "Dataset is complete enough for stable clustering.",
    },
    {
      title: "Missing Value Rows",
      value: rowsWithMissingValues,
      subtitle: "Rows with at least one empty or null field.",
    },
    {
      title: "Repeat Purchase Rate",
      value: "68%",
      subtitle: "Customers with more than one repeat purchase.",
    },
  ],
};

export const previewData = {
  rows: customers,
  columns: previewColumns,
  quality: {
    totalRows: customers.length,
    totalColumns: previewColumns.length,
    missingCells,
    rowsWithMissingValues,
  },
};

export const analysisResults = {
  totalClusters: 4,
  optimalK: 4,
  silhouetteScore: 0.68,
  clusterDistribution,
  scatterData: customers.map((customer) => ({
    customerId: customer.id,
    cluster: customer.cluster,
    segment: customer.segmentShortLabel,
    recency: customer.recency,
    frequency: customer.frequency,
    monetary: customer.monetary,
    fill: SEGMENT_COLORS[customer.segment],
  })),
  elbowMethod,
  clusterProfiles,
};

export const clusteringOutput = {
  segments: segmentMix.map((item) => ({
    ...item,
    segment: SEGMENT_BLUEPRINT.find((segment) => segment.shortLabel === item.name)?.label || item.name,
  })),
  customers,
};

export const insightsData = {
  highlights: [
    {
      title: "Revenue Concentration",
      statement: `Top 20% customers generate ${topRevenueShare.toFixed(0)}% of revenue.`,
      metric: `${topRevenueShare.toFixed(0)}%`,
      tone: "primary",
    },
    {
      title: "Inactive Customers",
      statement: `${inactiveCustomers} customers have not purchased in the last 45+ days.`,
      metric: inactiveCustomers,
      tone: "warning",
    },
    {
      title: "At-Risk Revenue",
      statement: `At-risk customers account for ${Math.round((atRiskRevenue / totalRevenue) * 100)}% of tracked revenue.`,
      metric: `${Math.round((atRiskRevenue / totalRevenue) * 100)}%`,
      tone: "secondary",
    },
  ],
  recommendations: [
    {
      title: "Protect High-Value Customers",
      summary: "Launch priority rewards and concierge communication for the highest value cohort.",
      actions: [
        "Trigger a premium loyalty flow after every purchase above the cohort median.",
        "Offer early access campaigns and personalized bundles to retain share of wallet.",
        "Track purchase gap anomalies weekly to catch disengagement earlier.",
      ],
    },
    {
      title: "Re-activate At-Risk Customers",
      summary: "Use win-back campaigns for customers with growing recency but historically strong frequency.",
      actions: [
        "Send time-boxed discount codes to customers with recency above 45 days.",
        "Recommend previously purchased categories to reduce decision friction.",
        "Escalate to call-center or CRM touchpoints for top-spend at-risk users.",
      ],
    },
    {
      title: "Grow Low-Value Customers",
      summary: "Nurture smaller accounts into repeat buyers with onboarding and cross-sell journeys.",
      actions: [
        "Bundle first three purchases with guided product education.",
        "Push free-shipping thresholds just above average order value.",
        "Use drip campaigns to encourage second and third orders within 30 days.",
      ],
    },
  ],
  opportunityAreas: [
    {
      label: "Upsell Opportunity",
      value: "18 customers",
      detail: "Loyal customers with frequency above 10 but moderate spend.",
    },
    {
      label: "Retention Priority",
      value: "10 customers",
      detail: "At-risk customers with healthy historical order count.",
    },
    {
      label: "Data Quality Action",
      value: `${missingCells} fields`,
      detail: "Missing values should be imputed or cleaned before retraining.",
    },
  ],
};
