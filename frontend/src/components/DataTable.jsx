import ArrowDownwardRoundedIcon from "@mui/icons-material/ArrowDownwardRounded";
import RemoveRoundedIcon from "@mui/icons-material/RemoveRounded";
import {
  Box,
  Chip,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TablePagination,
  TableRow,
  TableSortLabel,
  Typography,
} from "@mui/material";
import { useEffect, useState } from "react";
import { isMissingValue } from "../utils/formatters";

const descendingComparator = (left, right, orderBy) => {
  const leftValue = left[orderBy];
  const rightValue = right[orderBy];

  if (leftValue === rightValue) {
    return 0;
  }

  if (leftValue === null || leftValue === undefined || leftValue === "") {
    return 1;
  }

  if (rightValue === null || rightValue === undefined || rightValue === "") {
    return -1;
  }

  if (rightValue < leftValue) {
    return -1;
  }

  if (rightValue > leftValue) {
    return 1;
  }

  return 0;
};

const getComparator = (order, orderBy) =>
  order === "desc"
    ? (left, right) => descendingComparator(left, right, orderBy)
    : (left, right) => -descendingComparator(left, right, orderBy);

// Generic table component with sorting, pagination, and missing-value highlighting.
const DataTable = ({
  columns,
  rows,
  defaultOrderBy,
  initialRowsPerPage = 8,
  highlightMissingValues = true,
}) => {
  const [order, setOrder] = useState("asc");
  const [orderBy, setOrderBy] = useState(defaultOrderBy || columns[0]?.id);
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(initialRowsPerPage);

  useEffect(() => {
    setPage(0);
  }, [rows.length]);

  const handleRequestSort = (columnId) => {
    const isAscending = orderBy === columnId && order === "asc";
    setOrder(isAscending ? "desc" : "asc");
    setOrderBy(columnId);
  };

  const sortedRows = [...rows].sort(getComparator(order, orderBy));
  const paginatedRows = sortedRows.slice(page * rowsPerPage, page * rowsPerPage + rowsPerPage);

  return (
    <Paper sx={{ overflow: "hidden" }}>
      <TableContainer sx={{ maxHeight: 520 }}>
        <Table stickyHeader>
          <TableHead>
            <TableRow>
              {columns.map((column) => (
                <TableCell
                  key={column.id}
                  sortDirection={orderBy === column.id ? order : false}
                  sx={{ minWidth: column.minWidth, whiteSpace: "nowrap" }}
                >
                  {column.sortable === false ? (
                    column.label
                  ) : (
                    <TableSortLabel
                      active={orderBy === column.id}
                      direction={orderBy === column.id ? order : "asc"}
                      onClick={() => handleRequestSort(column.id)}
                      IconComponent={ArrowDownwardRoundedIcon}
                    >
                      {column.label}
                    </TableSortLabel>
                  )}
                </TableCell>
              ))}
            </TableRow>
          </TableHead>

          <TableBody>
            {paginatedRows.length > 0 ? (
              paginatedRows.map((row) => (
                <TableRow hover key={row.id || JSON.stringify(row)}>
                  {columns.map((column) => {
                    const value = row[column.id];
                    // Only flag missing if the column doesn't have a custom renderer —
                    // action/computed columns have no raw value on the row and use render() instead.
                    const missing = highlightMissingValues && !column.render && isMissingValue(value);

                    return (
                      <TableCell
                        key={`${row.id || row[column.id]}-${column.id}`}
                        sx={{
                          backgroundColor: missing ? "rgba(249, 115, 22, 0.08)" : "inherit",
                        }}
                      >
                        {missing ? (
                          <Chip
                            icon={<RemoveRoundedIcon />}
                            label="Missing"
                            size="small"
                            color="warning"
                            variant="outlined"
                          />
                        ) : column.render ? (
                          column.render(value, row)
                        ) : (
                          value
                        )}
                      </TableCell>
                    );
                  })}
                </TableRow>
              ))
            ) : (
              <TableRow>
                <TableCell colSpan={columns.length}>
                  <Box sx={{ py: 5, textAlign: "center" }}>
                    <Typography variant="subtitle1">No records to display.</Typography>
                    <Typography variant="body2" color="text.secondary" sx={{ mt: 0.7 }}>
                      Adjust your filters or upload a new dataset to continue.
                    </Typography>
                  </Box>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </TableContainer>

      <TablePagination
        component="div"
        rowsPerPageOptions={[5, 8, 10, 15]}
        count={rows.length}
        rowsPerPage={rowsPerPage}
        page={page}
        onPageChange={(_, nextPage) => setPage(nextPage)}
        onRowsPerPageChange={(event) => {
          setRowsPerPage(Number(event.target.value));
          setPage(0);
        }}
      />
    </Paper>
  );
};

export default DataTable;
