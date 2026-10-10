/**
 * Customers.
 *
 * Two endpoints are joined in the browser rather than on the server: the customer
 * list from `/api/v1/customers` and the RFM snapshot from
 * `/api/v1/analytics/rfm`. Doing it here is deliberate — both are already whole-
 * tenant reads cached independently, and a customer whose RFM has never been
 * computed still has to appear in the list. A server-side join would have to pick
 * between an inner join (which hides those customers) and an outer join (which
 * duplicates the RFM endpoint's work on a page that may not need it).
 *
 * A customer with no RFM row is shown with a dash and, in the summary line, counted
 * as unscored. That is the honest answer: it means either they have no orders or no
 * analytics job has run since they arrived. It is never rendered as a zero, because
 * "spent nothing" and "we have not measured this" are different facts.
 */

import { useMemo, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Grid from "@mui/material/Grid";
import Stack from "@mui/material/Stack";
import TablePagination from "@mui/material/TablePagination";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import CloudUploadRoundedIcon from "@mui/icons-material/CloudUploadRounded";
import GroupsRoundedIcon from "@mui/icons-material/GroupsRounded";
import RefreshRoundedIcon from "@mui/icons-material/RefreshRounded";

import { ROUTES } from "../../app/navigation.js";
import { catalogApi, dashboardApi } from "../../api/index.js";
import useApiResource from "../../hooks/useApiResource.js";
import AsyncBoundary, { EmptyBlock } from "../../components/ui/AsyncBoundary.jsx";
import RecordTable from "../../components/ui/RecordTable.jsx";
import SectionCard from "../../components/ui/SectionCard.jsx";
import StatCard from "../../components/ui/StatCard.jsx";
import {
  formatCount,
  formatCurrency,
  formatDate,
  formatRelative,
  segmentColor,
  segmentRank,
} from "../../utils/format.js";

/** Where an RFM row came from. The API reports this per customer; it is not a guess. */
const SOURCE_LABEL = {
  orders: "Measured",
  import: "Uploaded",
};

const SOURCE_HINT = {
  orders: "Computed from this customer's orders.",
  import: "Taken from RFM columns in an uploaded file, not measured from orders.",
};

const FILTERS = {
  ALL: "all",
  SCORED: "scored",
  UNSCORED: "unscored",
};

const PAGE_SIZES = [25, 50, 100, 250];

const CustomersPage = () => {
  const [filter, setFilter] = useState(FILTERS.ALL);
  const [pageSize, setPageSize] = useState(100);
  const [page, setPage] = useState(0);

  const summary = useApiResource((options) => dashboardApi.fetchSummary(options));
  const distribution = useApiResource((options) => dashboardApi.fetchRfmDistribution(options));
  const customers = useApiResource(
    (options) =>
      catalogApi.fetchCustomers({
        limit: pageSize,
        offset: page * pageSize,
        ...options,
      }),
    { deps: [pageSize, page] }
  );

  const refreshAll = () => {
    summary.refresh();
    distribution.refresh();
    customers.refresh();
  };

  const data = customers.data;
  const rawList = data?.customers ?? [];
  const totalCount = data?.total ?? summary.data?.customers ?? rawList.length;

  const rows = useMemo(() => {
    return rawList.map((customer) => ({
      ...customer,
      displayName: customer.name || customer.external_customer_id || "Unnamed",
      scored: customer.segment !== null && customer.segment !== undefined,
    }));
  }, [rawList]);

  const visibleRows = useMemo(() => {
    if (filter === FILTERS.SCORED) return rows.filter((row) => row.scored);
    if (filter === FILTERS.UNSCORED) return rows.filter((row) => !row.scored);
    return rows;
  }, [rows, filter]);

  const totals = useMemo(() => {
    const s = summary.data;
    const d = distribution.data;
    const totalCustomers = s?.customers ?? totalCount;
    const scored = d?.total_customers_scored ?? (s?.rfm_rows ?? 0);
    const measured = d?.sources?.orders ?? 0;
    const uploaded = d?.sources?.import ?? 0;
    const monetary = d?.segments
      ? d.segments.reduce((acc, seg) => acc + (seg.total_monetary || 0), 0)
      : (s?.revenue ?? 0);
    const unscored = Math.max(0, totalCustomers - scored);

    return { total: totalCustomers, scored, measured, uploaded, monetary, unscored };
  }, [summary.data, distribution.data, totalCount]);

  const columns = useMemo(
    () => [
      {
        id: "displayName",
        label: "Customer",
        render: (row) => (
          <Stack spacing={0.25} sx={{ minWidth: 0 }}>
            <Typography variant="body2" fontWeight={600} noWrap>
              {row.displayName}
            </Typography>
            {row.email ? (
              <Typography variant="caption" color="text.secondary" noWrap>
                {row.email}
              </Typography>
            ) : null}
          </Stack>
        ),
      },
      { id: "external_customer_id", label: "External ID" },
      { id: "city", label: "City" },
      {
        id: "segment",
        label: "Segment",
        // Sorts by RFM rank, so "Champions" leads rather than "About To Sleep".
        sortValue: (row) => (row.segment ? segmentRank(row.segment) : null),
        render: (row) =>
          row.segment ? (
            <Chip
              size="small"
              label={row.segment}
              sx={{
                bgcolor: `${segmentColor(row.segment)}1F`,
                color: segmentColor(row.segment),
                fontWeight: 600,
              }}
            />
          ) : (
            <Tooltip title="No RFM row for this customer yet — either they have no orders, or no analytics job has run since they were imported.">
              <Typography variant="caption" color="text.disabled">
                unscored
              </Typography>
            </Tooltip>
          ),
      },
      {
        id: "monetary",
        label: "Monetary",
        numeric: true,
        render: (row) => formatCurrency(row.monetary),
      },
      {
        id: "frequency",
        label: "Orders",
        numeric: true,
        render: (row) => (row.frequency === null ? "—" : formatCount(row.frequency)),
      },
      {
        id: "recency",
        label: "Days since",
        numeric: true,
        render: (row) => (row.recency === null ? "—" : formatCount(row.recency)),
      },
      {
        id: "source",
        label: "RFM source",
        render: (row) =>
          row.source ? (
            <Tooltip title={SOURCE_HINT[row.source] ?? ""}>
              <Chip
                size="small"
                variant="outlined"
                color={row.source === "orders" ? "success" : "default"}
                label={SOURCE_LABEL[row.source] ?? row.source}
              />
            </Tooltip>
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
          {customers.lastUpdatedAt ? `Updated ${formatRelative(customers.lastUpdatedAt)}` : " "}
        </Typography>
        <Button
          size="small"
          startIcon={<RefreshRoundedIcon />}
          onClick={refreshAll}
          disabled={customers.isLoading || customers.isRefreshing}
        >
          Refresh
        </Button>
      </Stack>

      <AsyncBoundary
        isLoading={customers.isLoading}
        error={customers.error}
        onRetry={customers.reload}
        isEmpty={rows.length === 0}
        empty={
          <EmptyBlock
            icon={<GroupsRoundedIcon color="primary" sx={{ fontSize: 46 }} />}
            title="No customers yet"
            description="Upload a customer file, or import orders — a referenced customer who is not on file yet is created automatically."
            action={
              <Button component={RouterLink} to={ROUTES.IMPORT} variant="contained" startIcon={<CloudUploadRoundedIcon />}>
                Import data
              </Button>
            }
          />
        }
      >
        <Stack spacing={3}>
          <Grid container spacing={2.5}>
            <Grid item xs={12} sm={6} md={3}>
              <StatCard label="Customers" value={totals.total} format="count" tone="primary" />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <StatCard
                label="Scored"
                value={totals.scored}
                format="count"
                tone="info"
                caption={`${formatCount(totals.measured)} measured · ${formatCount(totals.uploaded)} uploaded`}
                hint="A customer is scored once an RFM row exists for them. Measured rows come from orders; uploaded rows come from RFM columns in a file."
              />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <StatCard
                label="Unscored"
                value={totals.unscored}
                format="count"
                tone={totals.unscored > 0 ? "warning" : "neutral"}
                hint="Not an error. These customers have no orders on file, or no analytics job has run since they were imported."
              />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <StatCard
                label="Measured value"
                value={totals.monetary}
                format="currency"
                tone="success"
                caption="Sum of scored customers only"
              />
            </Grid>
          </Grid>

          {totals.scored === 0 && (
            <Alert
              severity="info"
              action={
                <Button component={RouterLink} to={ROUTES.JOBS} size="small" color="inherit">
                  Run analysis
                </Button>
              }
            >
              No RFM scores exist yet, so value columns are unpopulated. Queue an RFM job to compute
              them from the orders on file.
            </Alert>
          )}

          <SectionCard
            title="All customers"
            subtitle={`${formatCount(totalCount)} on file · Showing page ${page + 1}`}
            isRefreshing={customers.isRefreshing || distribution.isRefreshing}
            action={
              <ToggleButtonGroup
                size="small"
                exclusive
                value={filter}
                onChange={(_, next) => next && setFilter(next)}
              >
                <ToggleButton value={FILTERS.ALL}>All</ToggleButton>
                <ToggleButton value={FILTERS.SCORED}>Scored</ToggleButton>
                <ToggleButton value={FILTERS.UNSCORED}>Unscored</ToggleButton>
              </ToggleButtonGroup>
            }
          >
            <RecordTable
              columns={columns}
              rows={visibleRows}
              getRowId={(row) => row.id}
              initialSortBy="monetary"
              initialSortDirection="desc"
              searchable
              searchPlaceholder="Search name, email, external ID or city (this page)"
              searchKeys={["displayName", "name", "email", "external_customer_id", "city"]}
              disableInternalPagination
              emptyMessage={
                filter === FILTERS.UNSCORED
                  ? "Every customer on this page has an RFM row."
                  : "No customers match that search."
              }
            />
            <TablePagination
              component="div"
              count={totalCount}
              page={page}
              onPageChange={(_, next) => setPage(next)}
              rowsPerPage={pageSize}
              onRowsPerPageChange={(e) => {
                setPageSize(parseInt(e.target.value, 10));
                setPage(0);
              }}
              rowsPerPageOptions={PAGE_SIZES}
              labelRowsPerPage="Per page"
              sx={{ borderTop: 1, borderColor: "divider" }}
            />
          </SectionCard>
        </Stack>
      </AsyncBoundary>
    </Stack>
  );
};

export default CustomersPage;
