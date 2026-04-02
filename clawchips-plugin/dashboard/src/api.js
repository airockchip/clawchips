const API_ROOT = (import.meta.env.BASE_URL || "/")
  .replace(/\/?dashboard\/?$/i, "")
  .replace(/\/+$/, "");

function apiUrl(path) {
  const p = path.startsWith("/") ? path : `/${path}`;
  return `${API_ROOT}${p}`;
}

async function request(path, options = {}) {
  try {
    const response = await fetch(apiUrl(path), {
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
      ...options,
    });

    if (!response.ok) {
      const text = await response.text();
      if (text) {
        try {
          const j = JSON.parse(text);
          if (j && typeof j.error === "string" && j.error) {
            throw new Error(j.error);
          }
        } catch (e) {
          if (e instanceof SyntaxError) {
            // not JSON
          } else {
            throw e;
          }
        }
      }
      throw new Error(text || `HTTP ${response.status}`);
    }

    const text = await response.text();
    return text ? JSON.parse(text) : null;
  } catch (error) {
    throw new Error(error.message || "Request failed");
  }
}

export const api = {
  health: () => request("/health"),
  statics: () => request("/statics"),
  history: (limit = 5) => request(`/dashboard/api/history?limit=${limit}`),
  feedbackHistory: (page = 1, pageSize = 10) =>
    request(`/dashboard/api/feedback/history?page=${page}&page_size=${pageSize}`),
  memoryHistory: (page = 1, pageSize = 10) =>
    request(`/dashboard/api/memory?page=${page}&page_size=${pageSize}`),
  routingConfig: () => request("/dashboard/api/routing-config"),
  saveRoutingConfig: (payload) =>
    request("/dashboard/api/routing-config", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  providers: () => request("/dashboard/api/providers"),
  addProvider: (payload) =>
    request("/dashboard/api/providers", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateProvider: (providerId, payload) =>
    request(`/dashboard/api/providers/${encodeURIComponent(providerId)}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  deleteProvider: (providerId) =>
    request(`/dashboard/api/providers/${encodeURIComponent(providerId)}`, {
      method: "DELETE",
    }),
  addProviderModel: (providerId, payload) =>
    request(`/dashboard/api/providers/${encodeURIComponent(providerId)}/models`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateProviderModel: (providerId, modelId, payload) =>
    request(
      `/dashboard/api/providers/${encodeURIComponent(providerId)}/models/${encodeURIComponent(modelId)}`,
      {
        method: "PUT",
        body: JSON.stringify(payload),
      },
    ),
  deleteProviderModel: (providerId, modelId) =>
    request(
      `/dashboard/api/providers/${encodeURIComponent(providerId)}/models/${encodeURIComponent(modelId)}`,
      {
        method: "DELETE",
      },
    ),
  submitFeedback: (payload) =>
    request("/dashboard/api/feedback", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateMemoryLabel: (itemId, payload) =>
    request(`/dashboard/api/memory/${itemId}/label`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  deleteMemoryItem: (itemId) =>
    request(`/dashboard/api/memory/${itemId}`, {
      method: "DELETE",
    }),
};
