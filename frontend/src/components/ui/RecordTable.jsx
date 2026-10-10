/**
 * Table with client-side sort, filter and pagination.
 *
 * Client-side is the right call for these endpoints and it is worth being explicit
 * about why: `/api/v1/customers`, `/products` and `/inventory` each return the whole
 * tenant's rows in one response, so the data is already in the browser and a server
 * round-trip per sort would be slower and no more correct. Orders is the exception —
 * it pages server-side — so that page passes `disableInternalPagination` and drives
 * the controls itself.
 *
 * Sorting rules that a naive comparator gets wrong and this one does not:
 *
 * - **Null sorts last in both directions.** Reversing the sort should not fill the
 *   first screen with blank rows.
 * - **Numbers compare numerically.** `"9" > "10"` as strings, so a revenue column
 *   sorted lexicographically puts single-digit values at the top.
 * - **A column can declare its own sort value.** A segment column displays a name
 *   but should sort by rank, not alphabetically.
 */

import { useDeferredValue, useMemo, useState } from "react";
import Box from "@mui/material/Box";
import InputAdornment from "@mui/material/InputAdornment";
import Stack from "@mui/material/Stack";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableContainer from "@mui/material/TableContainer";
import TableHead from "@mui/material/TableHead";
import TablePagination from "@mui/material/TablePagination";
import TableRow from "@mui/material/TableRow";
import TableSortLabel from "@mui/material/TableSortLabel";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import SearchRoundedIcon from "@mui/icons-material/SearchRounded";

const compare = (a, b) => {
  // Null and undefined always sink, whichever way the column is sorted.
  const aMissing = a === null || a === undefined || a === "";
  const bMissing = b === null || b === undefined || b === "";

  if (aMissing && bMissing) return 0;
  if (aMissing) return 1;
  if (bMissing) return -1;

  if (typeof a === "number" && typeof b === "number") return a - b;

  // A numeric string from a Numeric column must not compare lexicographically.
  const aNumber = typeof a === "string" ? Number(a) : NaN;
  const bNumber = typeof b === "string" ? Number(b) : NaN;

  if (!Number.isNaN(aNumber) && !Number.isNaN(bNumber) && a.trim() !== "" && b.trim() !== "") {
    return aNumber - bNumber;
  }

  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
};

const RecordTable = ({
  columns,
  rows,
  getRowId = (row, index) => row.id ?? index,
  initialSortBy = null,
  initialSortDirection = "asc",
  searchable = false,
  searchPlaceholder = "Search",
  searchKeys = [],
  rowsPerPageOptions = [25, 50, 100],
  initialRowsPerPage = 25,
  disableInternalPagination = false,
  dense = true,
  maxHeight = 560,
  emptyMessage = "Nothing to show.",
  onRowClick,
  footer = null,
}) => {
  const [sortBy, setSortBy] = useState(initialSortBy);
  const [direction, setDirection] = useState(initialSortDirection);
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(initialRowsPerPage);
  const [query, setQuery] = useState("");

  // Keeps typing responsive on a few thousand rows: the input updates immediately
  // and the filtered list catches up.
  const deferredQuery = useDeferredValue(query);

  const filtered = useMemo(() => {
    const needle = deferredQuery.trim().toLowerCase();
    if (!needle || !searchKeys.length) return rows;

    return rows.filter((row) =>
      searchKeys.some((key) => String(row[key] ?? "").toLowerCase().includes(needle))
    );
  }, [rows, deferredQuery, searchKeys]);

  const sorted = useMemo(() => {
    if (!sortBy) return filtered;

    const column = columns.find((c) => c.id === sortBy);
    const valueOf = column?.sortValue ?? ((row) => row[sortBy]);

    // Copied before sorting: `Array.prototype.sort` mutates, and mutating the
    // array held in the fetch hook's state would make React miss the change.
    return [...filtered].sort((a, b) => {
      const result = compare(valueOf(a), valueOf(b));
      return direction === "asc" ? result : -result;
    });
  }, [filtered, sortBy, direction, columns]);

  const visible = useMemo(() => {
    if (disableInternalPagination) return sorted;
    const start = page * rowsPerPage;
    return sorted.slice(start, start + rowsPerPage);
  }, [sorted, page, rowsPerPage, disableInternalPagination]);

  const toggleSort = (id) => {
    if (sortBy === id) {
      setDirection((current) => (current === "asc" ? "desc" : "asc"));
      return;
    }
    setSortBy(id);
    // A new column starts descending when it is numeric — for revenue or counts,
    // "largest first" is almost always the question being asked.
    setDirection(columns.find((c) => c.id === id)?.numeric ? "desc" : "asc");
  };

  return (
    <Box>
      {searchable ? (
        <Stack direction="row" spacing={2} alignItems="center" sx={{ mb: 2 }}>
          <TextField
            size="small"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setPage(0);
            }}
            placeholder={searchPlaceholder}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <SearchRoundedIcon fontSize="small" />
                </InputAdornment>
              ),
            }}
            sx={{ maxWidth: 320, flex: 1 }}
          />
          {deferredQuery ? (
            <Typography variant="caption" color="text.secondary">
              {filtered.length} of {rows.length}
            </Typography>
          ) : null}
        </Stack>
      ) : null}

      <TableContainer sx={{ maxHeight }}>
        <Table stickyHeader size={dense ? "small" : "medium"}>
          <TableHead>
            <TableRow>
              {columns.map((column) => (
                <TableCell
                  key={column.id}
                  align={column.numeric ? "right" : "left"}
                  sx={{ whiteSpace: "nowrap", width: column.width }}
                >
                  {column.sortable === false ? (
                    column.label
                  ) : (
                    <TableSortLabel
                      active={sortBy === column.id}
                      direction={sortBy === column.id ? direction : "asc"}
                      onClick={() => toggleSort(column.id)}
                    >
                      {column.label}
                    </TableSortLabel>
                  )}
                </TableCell>
              ))}
            </TableRow>
          </TableHead>

          <TableBody>
            {visible.length === 0 ? (
              <TableRow>
                <TableCell colSpan={columns.length} sx={{ py: 5, textAlign: "center" }}>
                  <Typography variant="body2" color="text.secondary">
                    {emptyMessage}
                  </Typography>
                </TableCell>
              </TableRow>
            ) : (
              visible.map((row, index) => (
                <TableRow
                  key={getRowId(row, index)}
                  hover={Boolean(onRowClick)}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                  sx={onRowClick ? { cursor: "pointer" } : undefined}
                >
                  {columns.map((column) => (
                    <TableCell key={column.id} align={column.numeric ? "right" : "left"}>
                      {column.render ? column.render(row) : (row[column.id] ?? "—")}
                    </TableCell>
                  ))}
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </TableContainer>

      {footer}

      {!disableInternalPagination && sorted.length > rowsPerPageOptions[0] ? (
        <TablePagination
          component="div"
          count={sorted.length}
          page={page}
          onPageChange={(_, next) => setPage(next)}
          rowsPerPage={rowsPerPage}
          onRowsPerPageChange={(event) => {
            setRowsPerPage(Number(event.target.value));
            setPage(0);
          }}
          rowsPerPageOptions={rowsPerPageOptions}
        />
      ) : null}
    </Box>
  );
};

export default RecordTable;
