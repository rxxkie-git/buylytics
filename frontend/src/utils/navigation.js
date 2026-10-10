import AssessmentRoundedIcon from "@mui/icons-material/AssessmentRounded";
import CloudUploadRoundedIcon from "@mui/icons-material/CloudUploadRounded";
import HomeRoundedIcon from "@mui/icons-material/HomeRounded";
import InsightsRoundedIcon from "@mui/icons-material/InsightsRounded";
import InfoRoundedIcon from "@mui/icons-material/InfoRounded";
import TableViewRoundedIcon from "@mui/icons-material/TableViewRounded";
import BubbleChartRoundedIcon from "@mui/icons-material/BubbleChartRounded";
import HubRoundedIcon from "@mui/icons-material/HubRounded";
import QueryStatsRoundedIcon from "@mui/icons-material/QueryStatsRounded";
import InventoryRoundedIcon from "@mui/icons-material/InventoryRounded";
import ShoppingCartRoundedIcon from "@mui/icons-material/ShoppingCartRounded";

export const navigationItems = [
  {
    label: "Dashboard",
    path: "/dashboard",
    icon: AssessmentRoundedIcon,
    description: "KPIs and trends",
  },
  {
    label: "Data Hub",
    path: "/upload",
    icon: CloudUploadRoundedIcon,
    description: "Ingest datasets & inventory",
  },
  {
    label: "Orders",
    path: "/orders",
    icon: ShoppingCartRoundedIcon,
    description: "Customer transactions",
  },
  {
    label: "Product Analysis",
    path: "/product-analysis",
    icon: QueryStatsRoundedIcon,
    description: "Demand and forecasting",
  },
  {
    label: "Data Preview",
    path: "/preview",
    icon: TableViewRoundedIcon,
    description: "Inspect raw records",
  },
  {
    label: "Analysis Results",
    path: "/results",
    icon: BubbleChartRoundedIcon,
    description: "Cluster diagnostics",
  },
  {
    label: "Customer Segments",
    path: "/segments",
    icon: HubRoundedIcon,
    description: "Explore cohorts",
  },
  {
    label: "Insights",
    path: "/insights",
    icon: InsightsRoundedIcon,
    description: "Recommendations",
  },
  {
    label: "Inventory Management",
    path: "/inventory",
    icon: InventoryRoundedIcon,
    description: "Manage items & stock",
  },
  {
    label: "About",
    path: "/about",
    icon: InfoRoundedIcon,
    description: "Project summary",
  },
];

const routeDetails = {
  "/": {
    title: "Customer Buying Pattern Analysis",
    subtitle: "Machine learning powered insights for smarter customer strategy.",
  },
  "/dashboard": {
    title: "Analytics Dashboard",
    subtitle: "Track performance, trends, and customer value in one place.",
  },
  "/upload": {
    title: "Data Hub",
    subtitle: "Centralized ingestion for customers, products, and inventory.",
  },
  "/orders": {
    title: "Orders Management",
    subtitle: "Track customer orders, fulfillment status, and order line items.",
  },
  "/product-analysis": {
    title: "Product Analysis",
    subtitle: "Analyze sales, movement clusters, demand trends, and forecasts.",
  },
  "/preview": {
    title: "Data Preview",
    subtitle: "Review data quality, missing values, and record-level details.",
  },
  "/results": {
    title: "Analysis Results",
    subtitle: "Inspect clustering quality, elbow diagnostics, and RFM separation.",
  },
  "/segments": {
    title: "Customer Segments",
    subtitle: "Filter customer cohorts and compare behavior by segment.",
  },
  "/insights": {
    title: "Insights & Recommendations",
    subtitle: "Turn model output into retention and growth actions.",
  },
  "/inventory": {
    title: "Inventory Management",
    subtitle: "Track, add, edit, and manage store items and serial codes in real-time.",
  },
  "/about": {
    title: "About The Project",
    subtitle: "Understand the RFM and K-Means pipeline driving the frontend.",
  },
};

export const getRouteDetails = (pathname) =>
  routeDetails[pathname] || {
    title: "Customer Buying Pattern Analysis",
    subtitle: "Modern analytics frontend for segmentation and insights.",
  };

export const landingQuickLinks = [
  { label: "Platform Overview", path: "/", icon: HomeRoundedIcon },
  ...navigationItems,
];
