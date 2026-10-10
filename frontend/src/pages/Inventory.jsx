import AddRoundedIcon from "@mui/icons-material/AddRounded";
import RemoveRoundedIcon from "@mui/icons-material/Remove";
import DeleteRoundedIcon from "@mui/icons-material/DeleteRounded";
import EditRoundedIcon from "@mui/icons-material/EditRounded";
import SearchRoundedIcon from "@mui/icons-material/SearchRounded";
import InventoryRoundedIcon from "@mui/icons-material/InventoryRounded";
import ShoppingBagRoundedIcon from "@mui/icons-material/ShoppingBagRounded";
import AttachMoneyRoundedIcon from "@mui/icons-material/AttachMoneyRounded";
import WarningRoundedIcon from "@mui/icons-material/WarningRounded";
import CategoryRoundedIcon from "@mui/icons-material/CategoryRounded";
import SyncRoundedIcon from "@mui/icons-material/SyncRounded";
import AddCircleOutlineRoundedIcon from "@mui/icons-material/AddCircleOutlineRounded";
import RemoveCircleOutlineRoundedIcon from "@mui/icons-material/RemoveCircleOutlineRounded";
import {
  alpha,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  Grid,
  IconButton,
  InputAdornment,
  InputLabel,
  MenuItem,
  Paper,
  Select,
  Snackbar,
  Alert,
  Stack,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import { useDeferredValue, useState, useEffect, useCallback } from "react";
import DataTable from "../components/DataTable";
import ErrorState from "../components/ErrorState";
import LoadingState from "../components/LoadingState";
import NoDatasetState from "../components/NoDatasetState";
import PageHeader from "../components/PageHeader";
import {
  getInventoryData,
  addInventoryItem,
  updateInventoryItem,
  deleteInventoryItem,
  syncInventoryDatasets,
} from "../services/api";
import { formatCurrency } from "../utils/formatters";

const emptyForm = {
  id: "",
  itemName: "",
  category: "",
  quantity: 0,
  price: 0.0,
  supplier: "",
};

const Inventory = () => {
  const theme = useTheme();

  // Inventory list state
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Filters state
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search);
  const [categoryFilter, setCategoryFilter] = useState("All");
  const [statusFilter, setStatusFilter] = useState("All");

  // Form Dialog state
  const [openDialog, setOpenDialog] = useState(false);
  const [dialogMode, setDialogMode] = useState("add"); // "add" | "edit"
  const [formValues, setFormValues] = useState(emptyForm);
  const [dialogError, setDialogError] = useState("");


  // Toast notification state
  const [toastOpen, setToastOpen] = useState(false);
  const [toastMessage, setToastMessage] = useState("");
  const [toastSeverity, setToastSeverity] = useState("success"); // "success" | "error" | "warning"
  const [isSyncing, setIsSyncing] = useState(false);
  // Track which item IDs are currently being qty-adjusted (to disable buttons while saving)
  const [adjustingIds, setAdjustingIds] = useState(new Set());

  // Fetch data
  const loadData = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await getInventoryData();
      setItems(data.items || []);
    } catch (err) {
      setError(err?.response?.data?.detail || err?.message || "Failed to load inventory data.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Show Toast helper
  const showToast = (message, severity = "success") => {
    setToastMessage(message);
    setToastSeverity(severity);
    setToastOpen(true);
  };

  // Quick +/- quantity adjustment
  const handleAdjustQuantity = async (item, delta) => {
    const newQty = Math.max(0, (item.quantity || 0) + delta);
    setAdjustingIds((prev) => new Set(prev).add(item.id));
    try {
      await updateInventoryItem(item.id, { quantity: newQty });
      // Optimistically update local state for instant feedback
      setItems((prev) =>
        prev.map((i) =>
          i.id === item.id
            ? {
                ...i,
                quantity: newQty,
                status:
                  newQty <= 0
                    ? "Out of Stock"
                    : newQty <= 10
                    ? "Low Stock"
                    : "In Stock",
              }
            : i
        )
      );
    } catch (err) {
      showToast(err?.response?.data?.detail || "Failed to update quantity.", "error");
    } finally {
      setAdjustingIds((prev) => {
        const next = new Set(prev);
        next.delete(item.id);
        return next;
      });
    }
  };

  // Delete Action
  const handleDeleteItem = async (itemId) => {
    if (!window.confirm(`Are you sure you want to delete item with SKU/Serial: ${itemId}?`)) {
      return;
    }
    try {
      await deleteInventoryItem(itemId);
      showToast(`Item ${itemId} deleted successfully.`);
      loadData();
    } catch (err) {
      showToast(err?.response?.data?.detail || err?.message || "Failed to delete item.", "error");
    }
  };

  // Open Form Dialog
  const handleOpenAddDialog = () => {
    setDialogMode("add");
    setFormValues(emptyForm);
    setDialogError("");
    setOpenDialog(true);
  };

  const handleOpenEditDialog = (item) => {
    setDialogMode("edit");
    setFormValues({
      id: item.id,
      itemName: item.itemName,
      category: item.category,
      quantity: item.quantity,
      price: item.price,
      supplier: item.supplier,
    });
    setDialogError("");
    setOpenDialog(true);
  };

  // Submit Form Action
  const handleFormSubmit = async () => {
    const { id, itemName, category, quantity, price, supplier } = formValues;

    if (!id.trim()) {
      setDialogError("Serial Code / SKU is required.");
      return;
    }
    if (!itemName.trim()) {
      setDialogError("Item Name is required.");
      return;
    }

    try {
      if (dialogMode === "add") {
        await addInventoryItem({
          id: id.trim(),
          itemName: itemName.trim(),
          category: category.trim() || "General",
          quantity: parseInt(quantity) || 0,
          price: parseFloat(price) || 0.0,
          supplier: supplier.trim() || "Unknown",
        });
        showToast("Item added successfully.");
      } else {
        await updateInventoryItem(id, {
          itemName: itemName.trim(),
          category: category.trim() || "General",
          quantity: parseInt(quantity) || 0,
          price: parseFloat(price) || 0.0,
          supplier: supplier.trim() || "Unknown",
        });
        showToast(`Item ${id} updated successfully.`);
      }
      setOpenDialog(false);
      loadData();
    } catch (err) {
      setDialogError(err?.response?.data?.detail || err?.message || "Save operation failed.");
    }
  };

  const handleSyncDatasets = async () => {
    setIsSyncing(true);
    try {
      const res = await syncInventoryDatasets();
      showToast(res.message || "Datasets synced successfully.");
      loadData();
    } catch (err) {
      showToast(err?.response?.data?.detail || err?.message || "Failed to sync datasets.", "error");
    } finally {
      setIsSyncing(false);
    }
  };

  // Calculate aggregates
  const totalItems = items.length;
  const totalStock = items.reduce((sum, item) => sum + (item.quantity || 0), 0);
  const totalValue = items.reduce((sum, item) => sum + (item.quantity || 0) * (item.price || 0.0), 0);
  const lowStockCount = items.filter((item) => item.quantity <= 10).length;

  // Extract unique categories for filtering
  const categories = ["All", ...new Set(items.map((item) => item.category).filter(Boolean))];

  // Filtering rows logic
  const filteredRows = items.filter((item) => {
    const matchesCategory = categoryFilter === "All" || item.category === categoryFilter;
    const matchesStatus = statusFilter === "All" || item.status === statusFilter;

    const haystack = `${item.id} ${item.itemName} ${item.category} ${item.supplier}`.toLowerCase();
    const matchesSearch = haystack.includes(deferredSearch.trim().toLowerCase());

    return matchesCategory && matchesStatus && matchesSearch;
  });

  const columns = [
    { id: "id", label: "Serial/SKU", minWidth: 100 },
    { id: "itemName", label: "Item Name", minWidth: 180 },
    { id: "category", label: "Category", minWidth: 120 },
    {
      id: "quantity",
      label: "Stock Qty",
      minWidth: 140,
      render: (value, row) => (
        <Stack direction="row" alignItems="center" spacing={0.5}>
          <IconButton
            size="small"
            disabled={adjustingIds.has(row.id) || value <= 0}
            onClick={() => handleAdjustQuantity(row, -1)}
            sx={{ color: "error.main", p: 0.3 }}
          >
            <RemoveCircleOutlineRoundedIcon fontSize="small" />
          </IconButton>
          <Typography variant="body2" sx={{ fontWeight: 700, minWidth: 28, textAlign: "center" }}>
            {value}
          </Typography>
          <IconButton
            size="small"
            disabled={adjustingIds.has(row.id)}
            onClick={() => handleAdjustQuantity(row, +1)}
            sx={{ color: "success.main", p: 0.3 }}
          >
            <AddCircleOutlineRoundedIcon fontSize="small" />
          </IconButton>
        </Stack>
      ),
    },
    {
      id: "price",
      label: "Unit Price",
      minWidth: 100,
      render: (value) => formatCurrency(value),
    },
    { id: "supplier", label: "Supplier", minWidth: 120 },
    {
      id: "status",
      label: "Status",
      minWidth: 120,
      render: (value) => (
        <Chip
          label={value}
          size="small"
          color={
            value === "Out of Stock"
              ? "error"
              : value === "Low Stock"
              ? "warning"
              : "success"
          }
          variant="outlined"
          sx={{ fontWeight: 800 }}
        />
      ),
    },
    {
      id: "actions",
      label: "Actions",
      minWidth: 110,
      sortable: false,
      render: (_, row) => (
        <Stack direction="row" spacing={0.5}>
          <IconButton size="small" onClick={() => handleOpenEditDialog(row)} color="primary">
            <EditRoundedIcon fontSize="small" />
          </IconButton>
          <IconButton size="small" onClick={() => handleDeleteItem(row.id)} color="error">
            <DeleteRoundedIcon fontSize="small" />
          </IconButton>
        </Stack>
      ),
    },
  ];

  if (loading && items.length === 0) {
    return (
      <LoadingState
        title="Loading Inventory..."
        description="Retrieving store inventory catalog and counting stock levels."
      />
    );
  }

  if (error && items.length === 0) {
    return <ErrorState message={error} onRetry={loadData} />;
  }

  if (!loading && items.length === 0 && search === "" && categoryFilter === "All" && statusFilter === "All") {
    return (
      <NoDatasetState
        message="Your inventory catalog is currently empty. Click '+ Add Item' to register your first item."
        buttonText="Add First Item"
      />
    );
  }

  return (
    <Box>
      <PageHeader
        eyebrow="Operations Management"
        title="Real-Time Store Inventory Catalog"
        subtitle="Manage available stock, track serial codes, import store inventory listings, and view warning levels."
        chipLabel={`${totalItems} items registered`}
      />

      {/* KPI Cards Grid */}
      <Grid container spacing={3} sx={{ mb: 4 }}>
        <Grid item xs={12} sm={6} md={3}>
          <Paper sx={{ p: 2.25, display: "flex", alignItems: "center", gap: 2 }}>
            <Box
              sx={{
                width: 48,
                height: 48,
                borderRadius: "14px",
                display: "grid",
                placeItems: "center",
                backgroundColor: alpha(theme.palette.primary.main, 0.12),
                color: "primary.main",
              }}
            >
              <InventoryRoundedIcon />
            </Box>
            <Box>
              <Typography variant="body2" color="text.secondary">
                Total Products
              </Typography>
              <Typography variant="h5" sx={{ fontWeight: 800, mt: 0.25 }}>
                {totalItems}
              </Typography>
            </Box>
          </Paper>
        </Grid>

        <Grid item xs={12} sm={6} md={3}>
          <Paper sx={{ p: 2.25, display: "flex", alignItems: "center", gap: 2 }}>
            <Box
              sx={{
                width: 48,
                height: 48,
                borderRadius: "14px",
                display: "grid",
                placeItems: "center",
                backgroundColor: alpha(theme.palette.secondary.main, 0.12),
                color: "secondary.main",
              }}
            >
              <ShoppingBagRoundedIcon />
            </Box>
            <Box>
              <Typography variant="body2" color="text.secondary">
                Total Stock Qty
              </Typography>
              <Typography variant="h5" sx={{ fontWeight: 800, mt: 0.25 }}>
                {totalStock}
              </Typography>
            </Box>
          </Paper>
        </Grid>

        <Grid item xs={12} sm={6} md={3}>
          <Paper sx={{ p: 2.25, display: "flex", alignItems: "center", gap: 2 }}>
            <Box
              sx={{
                width: 48,
                height: 48,
                borderRadius: "14px",
                display: "grid",
                placeItems: "center",
                backgroundColor: alpha(theme.palette.success.main, 0.12),
                color: "success.main",
              }}
            >
              <AttachMoneyRoundedIcon />
            </Box>
            <Box>
              <Typography variant="body2" color="text.secondary">
                Total Value
              </Typography>
              <Typography variant="h5" sx={{ fontWeight: 800, mt: 0.25 }}>
                {formatCurrency(totalValue)}
              </Typography>
            </Box>
          </Paper>
        </Grid>

        <Grid item xs={12} sm={6} md={3}>
          <Paper sx={{ p: 2.25, display: "flex", alignItems: "center", gap: 2 }}>
            <Box
              sx={{
                width: 48,
                height: 48,
                borderRadius: "14px",
                display: "grid",
                placeItems: "center",
                backgroundColor: alpha(theme.palette.warning.main, 0.12),
                color: "warning.main",
              }}
            >
              <WarningRoundedIcon />
            </Box>
            <Box>
              <Typography variant="body2" color="text.secondary">
                Low / Out of Stock
              </Typography>
              <Typography variant="h5" sx={{ fontWeight: 800, mt: 0.25 }}>
                {lowStockCount}
              </Typography>
            </Box>
          </Paper>
        </Grid>
      </Grid>

      {/* Control Actions & Import */}
      <Grid container spacing={3} sx={{ mb: 3 }}>
        <Grid item xs={12} md={7}>
          <Paper sx={{ p: 2.5, height: "100%", display: "flex", flexWrap: "wrap", gap: 2, alignItems: "center" }}>
            <TextField
              size="small"
              placeholder="Search SKU, name, brand..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <SearchRoundedIcon fontSize="small" color="disabled" />
                  </InputAdornment>
                ),
              }}
              sx={{ minWidth: 240, flexGrow: 1 }}
            />

            <FormControl size="small" sx={{ minWidth: 150 }}>
              <InputLabel id="category-filter-label">Category</InputLabel>
              <Select
                labelId="category-filter-label"
                label="Category"
                value={categoryFilter}
                onChange={(e) => setCategoryFilter(e.target.value)}
              >
                {categories.map((cat) => (
                  <MenuItem key={cat} value={cat}>
                    {cat}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>

            <FormControl size="small" sx={{ minWidth: 150 }}>
              <InputLabel id="status-filter-label">Stock Status</InputLabel>
              <Select
                labelId="status-filter-label"
                label="Stock Status"
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
              >
                <MenuItem value="All">All Statuses</MenuItem>
                <MenuItem value="In Stock">In Stock</MenuItem>
                <MenuItem value="Low Stock">Low Stock</MenuItem>
                <MenuItem value="Out of Stock">Out of Stock</MenuItem>
              </Select>
            </FormControl>
          </Paper>
        </Grid>

        <Grid item xs={12} md={5}>
          <Paper sx={{ p: 2.5, height: "100%", display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 2 }}>
            <Button
              variant="outlined"
              onClick={handleSyncDatasets}
              startIcon={<SyncRoundedIcon />}
              disabled={isSyncing}
              sx={{ borderRadius: "10px" }}
            >
              {isSyncing ? "Syncing..." : "Sync Datasets"}
            </Button>
            <Button
              variant="contained"
              onClick={handleOpenAddDialog}
              startIcon={<AddRoundedIcon />}
              sx={{ borderRadius: "10px" }}
            >
              Add Item
            </Button>
          </Paper>
        </Grid>
      </Grid>

      {/* Data Table */}
      <DataTable
        columns={columns}
        rows={filteredRows}
        defaultOrderBy="id"
        initialRowsPerPage={8}
      />

      {/* CRUD dialog popup */}
      <Dialog open={openDialog} onClose={() => setOpenDialog(false)} maxWidth="sm" fullWidth>
        <DialogTitle sx={{ fontWeight: 800 }}>
          {dialogMode === "add" ? "Register New Inventory Item" : `Edit Inventory Item: ${formValues.id}`}
        </DialogTitle>
        <DialogContent dividers>
          <Stack spacing={2} sx={{ mt: 1 }}>
            {dialogError && (
              <Alert severity="error" variant="outlined" sx={{ borderRadius: "10px" }}>
                {dialogError}
              </Alert>
            )}

            <TextField
              label="Serial Code / SKU"
              disabled={dialogMode === "edit"}
              value={formValues.id}
              onChange={(e) => setFormValues({ ...formValues, id: e.target.value })}
              placeholder="e.g. SKU-1005"
              fullWidth
              size="small"
              required
            />

            <TextField
              label="Item Name"
              value={formValues.itemName}
              onChange={(e) => setFormValues({ ...formValues, itemName: e.target.value })}
              placeholder="e.g. Wireless Mouse Model X"
              fullWidth
              size="small"
              required
            />

            <Grid container spacing={2}>
              <Grid item xs={6}>
                <TextField
                  label="Category"
                  value={formValues.category}
                  onChange={(e) => setFormValues({ ...formValues, category: e.target.value })}
                  placeholder="e.g. Electronics"
                  fullWidth
                  size="small"
                />
              </Grid>
              <Grid item xs={6}>
                <TextField
                  label="Supplier / Vendor"
                  value={formValues.supplier}
                  onChange={(e) => setFormValues({ ...formValues, supplier: e.target.value })}
                  placeholder="e.g. Logitech"
                  fullWidth
                  size="small"
                />
              </Grid>
            </Grid>

            <Grid container spacing={2}>
              <Grid item xs={6}>
                <TextField
                  label="Stock Quantity"
                  type="number"
                  value={formValues.quantity}
                  onChange={(e) => setFormValues({ ...formValues, quantity: Math.max(0, parseInt(e.target.value) || 0) })}
                  fullWidth
                  size="small"
                />
              </Grid>
              <Grid item xs={6}>
                <TextField
                  label="Unit Price ($)"
                  type="number"
                  value={formValues.price}
                  onChange={(e) => setFormValues({ ...formValues, price: Math.max(0, parseFloat(e.target.value) || 0.0) })}
                  inputProps={{ step: "0.01" }}
                  fullWidth
                  size="small"
                />
              </Grid>
            </Grid>
          </Stack>
        </DialogContent>
        <DialogActions sx={{ p: 2.5 }}>
          <Button onClick={() => setOpenDialog(false)} color="inherit">
            Cancel
          </Button>
          <Button onClick={handleFormSubmit} variant="contained" color="primary">
            {dialogMode === "add" ? "Register Item" : "Save Changes"}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Snackbar alerts */}
      <Snackbar
        open={toastOpen}
        autoHideDuration={6000}
        onClose={() => setToastOpen(false)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
      >
        <Alert
          onClose={() => setToastOpen(false)}
          severity={toastSeverity}
          variant="filled"
          sx={{ width: "100%", borderRadius: "10px", fontWeight: 700 }}
        >
          {toastMessage}
        </Alert>
      </Snackbar>
    </Box>
  );
};

export default Inventory;
