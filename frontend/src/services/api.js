import axios from "axios";
import { API_BASE_URL } from "../config/env.js";
import {
  getDatasetId,
  getProductDatasetId,
  saveDatasetContext,
  saveProductDatasetContext,
  saveProductUploadMeta,
  saveUploadMeta,
} from "../utils/storage";

const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 15000,
});

export const setupApiInterceptors = (getToken) => {
  api.interceptors.request.use(async (config) => {
    const token = await getToken();
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  });
};

const getRequestConfigWithDataset = (params = {}) => {
  const datasetId = getDatasetId();

  return {
    params: {
      ...(datasetId ? { datasetId } : {}),
      ...params,
    },
  };
};

const getRequestConfigWithProductDataset = (params = {}) => {
  const datasetId = getProductDatasetId();
  return {
    params: {
      ...(datasetId ? { datasetId } : {}),
      ...params,
    },
  };
};

export const uploadDataset = async (file) => {
  saveUploadMeta(file);

  const formData = new FormData();
  formData.append("file", file);

  const { data } = await api.post("/upload", formData, {
    headers: {
      "Content-Type": "multipart/form-data",
    },
    timeout: 180000,
  });

  saveDatasetContext({
    datasetId: data?.datasetId,
    uploadMeta: data?.uploadMeta,
  });

  return data;
};

export const uploadProductDataset = async (file) => {
  saveProductUploadMeta(file);

  const formData = new FormData();
  formData.append("file", file);

  const { data } = await api.post("/upload-product", formData, {
    headers: {
      "Content-Type": "multipart/form-data",
    },
    timeout: 300000,
  });

  saveProductDatasetContext({
    datasetId: data?.datasetId,
    uploadMeta: data?.uploadMeta,
  });

  return data;
};

export const getPreviewData = async () => {
  const { data } = await api.get("/preview", getRequestConfigWithDataset());
  return data;
};

export const getDashboardData = async () => {
  const { data } = await api.get("/dashboard", getRequestConfigWithDataset());
  return data;
};

export const getAnalysisResults = async ({ clusterBy = "default", clusterCount = 0 } = {}) => {
  const { data } = await api.get(
    "/analyze",
    {
      ...getRequestConfigWithDataset({
        clusterBy,
        clusterCount,
      }),
      timeout: 300000,
    }
  );
  return data;
};

export const getClusteringOutput = async ({ clusterBy = "default", clusterCount = 0 } = {}) => {
  const { data } = await api.get(
    "/results",
    {
      ...getRequestConfigWithDataset({
        clusterBy,
        clusterCount,
      }),
      timeout: 300000,
    }
  );
  return data;
};

export const getInsightsData = async () => {
  const { data } = await api.get("/insights", getRequestConfigWithDataset());
  return data;
};

export const getProductAnalysisData = async ({
  forecastPeriods = 12,
  frequency = "auto",
  clusterBy = "default",
  clusterCount = 6,
  forceRefresh = false,
} = {}) => {
  const requestConfig = getRequestConfigWithProductDataset({
    forecastPeriods,
    frequency,
    clusterBy,
    clusterCount,
    forceRefresh,
  });

  const { data } = await api.get("/product-analysis", {
    ...requestConfig,
    timeout: 300000,
  });
  return data;
};

export const getInventoryData = async () => {
  const { data } = await api.get("/inventory");
  return data;
};

export const addInventoryItem = async (item) => {
  const { data } = await api.post("/inventory", item);
  return data;
};

export const updateInventoryItem = async (itemId, item) => {
  const { data } = await api.put(`/inventory/${itemId}`, item);
  return data;
};

export const deleteInventoryItem = async (itemId) => {
  const { data } = await api.delete(`/inventory/${itemId}`);
  return data;
};

export const uploadInventoryDataset = async (file) => {
  const formData = new FormData();
  formData.append("file", file);

  const { data } = await api.post("/inventory/upload", formData, {
    headers: {
      "Content-Type": "multipart/form-data",
    },
    timeout: 300000,
  });
  return data;
};

export const syncInventoryDatasets = async () => {
  const { data } = await api.post("/inventory/sync", {}, {
    timeout: 300000,
  });
  return data;
};

export const getOrders = async ({ limit = 100, offset = 0, status } = {}) => {
  const params = { limit, offset };
  if (status && status !== "All") {
    params.status = status;
  }
  const { data } = await api.get("/api/v1/orders", { params });
  return data;
};

export const getOrderDetail = async (orderId) => {
  const { data } = await api.get(`/api/v1/orders/${orderId}`);
  return data;
};
