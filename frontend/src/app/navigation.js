/**
 * Navigation for the Phase 2 shell.
 *
 * The sidebar is organised by what a user is trying to do, not by which endpoint
 * backs the page. Grouping matters here: an operator arriving with a spreadsheet
 * needs Data, someone reviewing results needs Analyse, and mixing the two into one
 * flat list of nine items is what made the Phase 1 nav hard to scan.
 *
 * The Phase 1 pages that duplicated one another — Data Preview, Analysis Results,
 * Insights — are no longer in the nav. Their content is either shown inline on the
 * page it belongs to or superseded by a tenant-scoped equivalent. Product Analysis
 * is kept because forecasting is real work with no v1 replacement, and its legacy
 * backend route is still mounted.
 */

import AssessmentRoundedIcon from "@mui/icons-material/AssessmentRounded";
import CloudUploadRoundedIcon from "@mui/icons-material/CloudUploadRounded";
import GroupsRoundedIcon from "@mui/icons-material/GroupsRounded";
import HubRoundedIcon from "@mui/icons-material/HubRounded";
import Inventory2RoundedIcon from "@mui/icons-material/Inventory2Rounded";
import LocalMallRoundedIcon from "@mui/icons-material/LocalMallRounded";
import PlaylistAddCheckRoundedIcon from "@mui/icons-material/PlaylistAddCheckRounded";
import QueryStatsRoundedIcon from "@mui/icons-material/QueryStatsRounded";
import ReceiptLongRoundedIcon from "@mui/icons-material/ReceiptLongRounded";
import SettingsRoundedIcon from "@mui/icons-material/SettingsRounded";
import LeaderboardRoundedIcon from "@mui/icons-material/LeaderboardRounded";

export const ROUTES = {
  DASHBOARD: "/app",
  CUSTOMERS: "/app/customers",
  RFM: "/app/rfm",
  SEGMENTS: "/app/segments",
  PRODUCTS: "/app/products",
  ORDERS: "/app/orders",
  INVENTORY: "/app/inventory",
  IMPORT: "/app/import",
  JOBS: "/app/jobs",
  PRODUCT_ANALYSIS: "/app/product-analysis",
  SETTINGS: "/app/settings",
};

export const navigationGroups = [
  {
    heading: "Overview",
    items: [
      {
        label: "Dashboard",
        path: ROUTES.DASHBOARD,
        icon: AssessmentRoundedIcon,
        description: "Revenue, customers, stock health",
      },
    ],
  },
  {
    heading: "Analyse",
    items: [
      {
        label: "RFM",
        path: ROUTES.RFM,
        icon: LeaderboardRoundedIcon,
        description: "Recency, frequency, monetary scores",
      },
      {
        label: "Segments",
        path: ROUTES.SEGMENTS,
        icon: HubRoundedIcon,
        description: "Clusters and churn risk",
      },
      {
        label: "Product analysis",
        path: ROUTES.PRODUCT_ANALYSIS,
        icon: QueryStatsRoundedIcon,
        description: "Demand trends and forecasts",
        legacy: true,
      },
    ],
  },
  {
    heading: "Data",
    items: [
      {
        label: "Customers",
        path: ROUTES.CUSTOMERS,
        icon: GroupsRoundedIcon,
        description: "Everyone on file",
      },
      {
        label: "Orders",
        path: ROUTES.ORDERS,
        icon: ReceiptLongRoundedIcon,
        description: "Transactions behind the metrics",
      },
      {
        label: "Products",
        path: ROUTES.PRODUCTS,
        icon: LocalMallRoundedIcon,
        description: "Catalogue",
      },
      {
        label: "Inventory",
        path: ROUTES.INVENTORY,
        icon: Inventory2RoundedIcon,
        description: "Stock levels and reorder points",
      },
      {
        label: "Import",
        path: ROUTES.IMPORT,
        icon: CloudUploadRoundedIcon,
        description: "Upload CSV or Excel files",
      },
    ],
  },
  {
    heading: "Operations",
    items: [
      {
        label: "Analytics jobs",
        path: ROUTES.JOBS,
        icon: PlaylistAddCheckRoundedIcon,
        description: "Queue, run, and inspect",
      },
      {
        label: "Settings",
        path: ROUTES.SETTINGS,
        icon: SettingsRoundedIcon,
        description: "Integrations and system status",
      },
    ],
  },
];

/** Flat list, for breadcrumb and title lookup. */
export const allNavigationItems = navigationGroups.flatMap((group) =>
  group.items.map((item) => ({ ...item, group: group.heading }))
);

const PAGE_META = {
  [ROUTES.DASHBOARD]: {
    title: "Dashboard",
    subtitle: "How the business is performing right now.",
  },
  [ROUTES.CUSTOMERS]: {
    title: "Customers",
    subtitle: "Every customer on file, with their measured value where it exists.",
  },
  [ROUTES.RFM]: {
    title: "RFM",
    subtitle: "Recency, frequency and monetary value, scored across the whole customer base.",
  },
  [ROUTES.SEGMENTS]: {
    title: "Segments",
    subtitle: "Behavioural clusters and churn risk from the latest segmentation run.",
  },
  [ROUTES.PRODUCTS]: { title: "Products", subtitle: "Your catalogue." },
  [ROUTES.ORDERS]: {
    title: "Orders",
    subtitle: "The transactions RFM is measured from.",
  },
  [ROUTES.INVENTORY]: {
    title: "Inventory",
    subtitle: "Stock on hand against reorder levels.",
  },
  [ROUTES.IMPORT]: {
    title: "Import data",
    subtitle: "Bring customers, orders, products and inventory in from a file.",
  },
  [ROUTES.JOBS]: {
    title: "Analytics jobs",
    subtitle: "Queue an analysis and watch it run.",
  },
  [ROUTES.PRODUCT_ANALYSIS]: {
    title: "Product analysis",
    subtitle: "Movement clusters, demand trends and forecasts.",
  },
  [ROUTES.SETTINGS]: {
    title: "Settings",
    subtitle: "Integration details and dependency status.",
  },
};

export const getPageMeta = (pathname) =>
  PAGE_META[pathname] ?? { title: "Retail analytics", subtitle: "" };
