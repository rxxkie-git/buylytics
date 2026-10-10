const STORAGE_KEY = "cbpa-upload-context";
const PRODUCT_STORAGE_KEY = "cbpa-product-upload-context";

const getStoredContext = (key = STORAGE_KEY) => {
  if (typeof window === "undefined") {
    return null;
  }

  const rawValue = window.sessionStorage.getItem(key);

  if (!rawValue) {
    return null;
  }

  try {
    return JSON.parse(rawValue);
  } catch {
    return null;
  }
};

const persistContext = (nextContext, key = STORAGE_KEY) => {
  if (typeof window === "undefined") {
    return null;
  }

  window.sessionStorage.setItem(key, JSON.stringify(nextContext));
  return nextContext;
};

export const getUploadContext = () => getStoredContext();

export const getUploadMeta = () => getStoredContext()?.uploadMeta || null;

export const getDatasetId = () => getStoredContext()?.datasetId || null;

export const saveUploadMeta = (file) => {
  if (typeof window === "undefined" || !file) {
    return null;
  }

  const existing = getStoredContext(STORAGE_KEY) || {};
  const uploadMeta = {
    fileName: file.name,
    size: file.size,
    type: file.type || "application/octet-stream",
    uploadedAt: new Date().toISOString(),
  };

  persistContext(
    {
    ...existing,
    uploadMeta,
    },
    STORAGE_KEY
  );

  return uploadMeta;
};

export const saveDatasetContext = ({ datasetId, uploadMeta }) => {
  if (typeof window === "undefined") {
    return null;
  }

  const existing = getStoredContext(STORAGE_KEY) || {};

  return persistContext(
    {
      ...existing,
      datasetId: datasetId || existing.datasetId || null,
      uploadMeta: uploadMeta || existing.uploadMeta || null,
    },
    STORAGE_KEY
  );
};

export const clearUploadContext = () => {
  if (typeof window === "undefined") {
    return;
  }

  window.sessionStorage.removeItem(STORAGE_KEY);
};

export const getProductUploadContext = () => getStoredContext(PRODUCT_STORAGE_KEY);

export const getProductUploadMeta = () => getStoredContext(PRODUCT_STORAGE_KEY)?.uploadMeta || null;

export const getProductDatasetId = () => getStoredContext(PRODUCT_STORAGE_KEY)?.datasetId || null;

export const saveProductUploadMeta = (file) => {
  if (typeof window === "undefined" || !file) {
    return null;
  }

  const existing = getStoredContext(PRODUCT_STORAGE_KEY) || {};
  const uploadMeta = {
    fileName: file.name,
    size: file.size,
    type: file.type || "application/octet-stream",
    uploadedAt: new Date().toISOString(),
  };

  persistContext(
    {
      ...existing,
      uploadMeta,
    },
    PRODUCT_STORAGE_KEY
  );

  return uploadMeta;
};

export const saveProductDatasetContext = ({ datasetId, uploadMeta }) => {
  if (typeof window === "undefined") {
    return null;
  }

  const existing = getStoredContext(PRODUCT_STORAGE_KEY) || {};

  return persistContext(
    {
      ...existing,
      datasetId: datasetId || existing.datasetId || null,
      uploadMeta: uploadMeta || existing.uploadMeta || null,
    },
    PRODUCT_STORAGE_KEY
  );
};

export const clearProductUploadContext = () => {
  if (typeof window === "undefined") {
    return;
  }

  window.sessionStorage.removeItem(PRODUCT_STORAGE_KEY);
};
