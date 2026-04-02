import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Cloud,
  Database,
  Eye,
  EyeOff,
  LayoutDashboard,
  Pencil,
  Plus,
  Route,
  Save,
  ServerCog,
  Trash2,
  X,
} from "lucide-react";
import { api } from "./api";
import Card from "./components/Card";
import Sidebar from "./components/Sidebar";

const INITIAL_PROVIDER_FORM = {
  name: "",
  baseUrl: "",
  apiKey: "",
  models: "",
  plan: "",
  authMode: "auto",
  local: false,
};

const INITIAL_MODEL_FORM = {
  providerId: "",
  id: "",
  name: "",
  description: "",
  maxTokens: "",
  contextWindow: "",
  inputPrice: "",
  outputPrice: "",
  cacheReadPrice: "",
  cacheWritePrice: "",
};

function MetricCard({ icon: Icon, label, value, hint }) {
  return (
    <div className="rounded-3xl bg-white/90 p-5 ring-1 ring-black/[0.04] shadow-[0_10px_36px_-20px_rgba(15,23,42,0.22)]">
      <div className="flex items-center justify-between">
        <div className="text-sm font-medium text-slate-500">{label}</div>
        <div className="rounded-2xl bg-slate-100 p-2 text-slate-700">
          <Icon className="h-4 w-4" />
        </div>
      </div>
      <div className="mt-4 text-3xl font-semibold tracking-tight text-slate-900">{value}</div>
      <div className="mt-2 text-sm text-slate-500">{hint}</div>
    </div>
  );
}

function TextField({ label, value, onChange, placeholder, type = "text", disabled = false, rightAdornment = null }) {
  return (
    <label className="block">
      <div className="mb-2 text-sm font-medium text-slate-700">{label}</div>
      <div className="relative">
        <input
          type={type}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder}
          disabled={disabled}
          className={`w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-indigo-300 focus:ring-4 focus:ring-indigo-100 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-400 ${
            rightAdornment ? "pr-12" : ""
          }`}
        />
        {rightAdornment ? <div className="absolute inset-y-0 right-3 flex items-center">{rightAdornment}</div> : null}
      </div>
    </label>
  );
}

function SelectField({ label, value, options, onChange, disabled = false }) {
  return (
    <label className="block">
      <div className="mb-2 text-sm font-medium text-slate-700">{label}</div>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
        className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-indigo-300 focus:ring-4 focus:ring-indigo-100 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-400"
      >
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </label>
  );
}

function formatTimestamp(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function formatTokenUsage(item) {
  const input = Number(item?.input_tokens ?? item?.prompt_tokens ?? 0);
  const output = Number(item?.output_tokens ?? item?.completion_tokens ?? 0);
  return `${input} / ${output}`;
}

function formatCost(value) {
  const n = Number(value ?? 0);
  if (!Number.isFinite(n) || n === 0) return "0";
  if (Math.abs(n) >= 1000) return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return n.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 6 });
}

function LoadingRow({ text }) {
  return <div className="rounded-2xl border border-dashed border-slate-200 px-4 py-8 text-center text-sm text-slate-500">{text}</div>;
}

export default function App() {
  const [page, setPage] = useState("home");
  const [health, setHealth] = useState(null);
  const [statics, setStatics] = useState(null);
  const [recent, setRecent] = useState([]);
  const [routing, setRouting] = useState(null);
  const [providers, setProviders] = useState({
    providerCount: 0,
    providers: [],
    bridge: { pendingCount: 0, pending: [], recent: [] },
    openclawConfigPath: "",
    openclawError: null,
    yamlLlmsCount: 0,
  });
  const [feedback, setFeedback] = useState({ items: [], page: 1, page_size: 10, total: 0, total_pages: 0 });
  const [feedbackPage, setFeedbackPage] = useState(1);
  const [memory, setMemory] = useState({ items: [], page: 1, page_size: 10, total: 0, total_pages: 0, enabled: false, available: false });
  const [memoryPage, setMemoryPage] = useState(1);
  const [toast, setToast] = useState(null);
  const [busyKey, setBusyKey] = useState("");
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [providerForm, setProviderForm] = useState(INITIAL_PROVIDER_FORM);
  const [providerDialogMode, setProviderDialogMode] = useState("add");
  const [isProviderDialogOpen, setIsProviderDialogOpen] = useState(false);
  const [showProviderApiKey, setShowProviderApiKey] = useState(false);
  const [modelForm, setModelForm] = useState(INITIAL_MODEL_FORM);
  const [modelDialogMode, setModelDialogMode] = useState("add");
  const [isModelDialogOpen, setIsModelDialogOpen] = useState(false);
  const [expandedProviders, setExpandedProviders] = useState({});

  const availableModels = routing?.availableModels || [];
  /** Merge API list with current selections so selects always list valid `provider/model` ids. */
  const routingModelOptions = useMemo(() => {
    const s = new Set(availableModels);
    if (routing?.localModel) s.add(routing.localModel);
    if (routing?.cloudModel) s.add(routing.cloudModel);
    if (routing?.defaultModel) s.add(routing.defaultModel);
    return [...s].sort((a, b) => a.localeCompare(b));
  }, [availableModels, routing?.localModel, routing?.cloudModel, routing?.defaultModel]);
  const pageTitleMap = {
    home: "Overview",
    routing: "Routing",
    providers: "Providers",
    feedback: "Tasks",
    memory: "Memory",
  };
  const hostLabel =
    typeof window !== "undefined"
      ? `${window.location.hostname || "127.0.0.1"}:${window.location.port || "80"}`
      : "127.0.0.1:8910";

  const refreshHome = useCallback(async () => {
    const [healthData, staticsData, historyData] = await Promise.all([
      api.health(),
      api.statics(),
      api.history(5),
    ]);
    setHealth(healthData);
    setStatics(staticsData);
    setRecent(historyData?.items || []);
  }, []);

  const refreshRouting = useCallback(async () => {
    const data = await api.routingConfig();
    setRouting(data);
  }, []);

  const refreshProviders = useCallback(async () => {
    const data = await api.providers();
    setProviders(
      data || {
        providerCount: 0,
        providers: [],
        bridge: { pendingCount: 0, pending: [], recent: [] },
        openclawConfigPath: "",
        openclawError: null,
        yamlLlmsCount: 0,
      },
    );
  }, []);

  const refreshFeedback = useCallback(async (nextPage = feedbackPage) => {
    const data = await api.feedbackHistory(nextPage, 10);
    setFeedback(data || { items: [], page: nextPage, page_size: 10, total: 0, total_pages: 0 });
  }, [feedbackPage]);

  const refreshMemory = useCallback(async (nextPage = memoryPage) => {
    const data = await api.memoryHistory(nextPage, 10);
    setMemory(data || { items: [], page: nextPage, page_size: 10, total: 0, total_pages: 0, enabled: false, available: false });
  }, [memoryPage]);

  useEffect(() => {
    refreshHome().catch((error) => showToast(error.message, "error"));
    refreshRouting().catch((error) => showToast(error.message, "error"));
    refreshProviders().catch((error) => showToast(error.message, "error"));
  }, [refreshHome, refreshProviders, refreshRouting]);

  useEffect(() => {
    refreshFeedback(feedbackPage).catch((error) => showToast(error.message, "error"));
  }, [feedbackPage, refreshFeedback]);

  useEffect(() => {
    refreshMemory(memoryPage).catch((error) => showToast(error.message, "error"));
  }, [memoryPage, refreshMemory]);

  useEffect(() => {
    const timer = setInterval(() => {
      refreshHome().catch(() => {});
      if (page === "providers") {
        refreshProviders().catch(() => {});
      }
    }, 8000);
    return () => clearInterval(timer);
  }, [page, refreshHome, refreshProviders]);

  const totals = statics?.totals || {};
  const modelCount = useMemo(() => Object.keys(statics?.by_model || {}).length, [statics]);

  function showToast(message, type = "success") {
    setToast({ message, type });
  }

  useEffect(() => {
    if (!toast?.message || typeof window === "undefined") return undefined;
    const timeout = window.setTimeout(() => setToast(null), toast.type === "error" ? 5000 : 2600);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  useEffect(() => {
    if (!isProviderDialogOpen || typeof window === "undefined") return undefined;

    function handleKeyDown(event) {
      if (event.key === "Escape") {
        setIsProviderDialogOpen(false);
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isProviderDialogOpen]);

  useEffect(() => {
    if (!isModelDialogOpen || typeof window === "undefined") return undefined;

    function handleKeyDown(event) {
      if (event.key === "Escape") {
        setIsModelDialogOpen(false);
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isModelDialogOpen]);

  useEffect(() => {
    if (!deleteTarget || typeof window === "undefined") return undefined;

    function handleKeyDown(event) {
      if (event.key === "Escape") {
        setDeleteTarget(null);
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [deleteTarget]);

  async function handleRoutingSave() {
    if (!routing) return;
    setBusyKey("routing");
    setToast(null);
    try {
      const saved = await api.saveRoutingConfig({
        localModel: routing.localModel,
        cloudModel: routing.cloudModel,
        defaultModel: routing.defaultModel,
        strategy: routing.strategy,
        memoryEnabled: Boolean(routing.memoryEnabled),
      });
      setRouting(saved);
      showToast("Runtime and routing config saved.");
      await refreshHome();
      await refreshMemory(1);
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      setBusyKey("");
    }
  }

  async function handleProviderSubmit(event) {
    event.preventDefault();
    setBusyKey("provider");
    setToast(null);
    try {
      if (providerDialogMode === "edit") {
        await api.updateProvider(providerForm.name, {
          baseUrl: providerForm.baseUrl,
          apiKey: providerForm.apiKey,
          plan: providerForm.plan,
          authMode: providerForm.authMode,
          local: providerForm.local,
        });
      } else {
        await api.addProvider({
          ...providerForm,
          models: providerForm.models
            .split(",")
            .map((item) => item.trim())
            .filter(Boolean),
        });
      }
      setProviderForm(INITIAL_PROVIDER_FORM);
      setIsProviderDialogOpen(false);
      setProviderDialogMode("add");
      setShowProviderApiKey(false);
      showToast(providerDialogMode === "edit" ? "已更新 OpenClaw 中的 Provider。" : "已添加 Provider 到 OpenClaw（openclaw.json）。");
      await refreshProviders();
      await refreshRouting();
      await refreshHome();
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      setBusyKey("");
    }
  }

  function openAddProviderDialog() {
    setProviderDialogMode("add");
    setProviderForm(INITIAL_PROVIDER_FORM);
    setShowProviderApiKey(false);
    setIsProviderDialogOpen(true);
  }

  function openEditProviderDialog(provider) {
    setProviderDialogMode("edit");
    setProviderForm({
      name: provider.id || "",
      baseUrl: provider.baseUrl || "",
      apiKey: "",
      models: "",
      plan: provider.description || "",
      authMode: provider.authMode || "auto",
      local: Boolean(provider.local),
    });
    setShowProviderApiKey(false);
    setIsProviderDialogOpen(true);
  }

  function openAddModelDialog(provider) {
    setModelDialogMode("add");
    setModelForm({
      ...INITIAL_MODEL_FORM,
      providerId: provider.id,
    });
    setIsModelDialogOpen(true);
  }

  function openEditModelDialog(provider, model) {
    setModelDialogMode("edit");
    setModelForm({
      providerId: provider.id || "",
      id: model.id || "",
      name: model.name || "",
      description: model.description || "",
      maxTokens: model.maxTokens != null ? String(model.maxTokens) : "",
      contextWindow: model.contextWindow != null ? String(model.contextWindow) : "",
      inputPrice: model.inputPrice != null ? String(model.inputPrice) : "",
      outputPrice: model.outputPrice != null ? String(model.outputPrice) : "",
      cacheReadPrice: model.cacheReadPrice != null ? String(model.cacheReadPrice) : "",
      cacheWritePrice: model.cacheWritePrice != null ? String(model.cacheWritePrice) : "",
    });
    setIsModelDialogOpen(true);
  }

  async function handleModelSubmit(event) {
    event.preventDefault();
    const actionKey = `${modelDialogMode}-model-${modelForm.providerId}-${modelForm.id}`;
    setBusyKey(actionKey);
    setToast(null);
    const payload = {
      id: modelForm.id,
      name: modelForm.name,
      description: modelForm.description,
      maxTokens: modelForm.maxTokens === "" ? null : Number(modelForm.maxTokens),
      contextWindow: modelForm.contextWindow === "" ? null : Number(modelForm.contextWindow),
      inputPrice: modelForm.inputPrice === "" ? null : Number(modelForm.inputPrice),
      outputPrice: modelForm.outputPrice === "" ? null : Number(modelForm.outputPrice),
      cacheReadPrice: modelForm.cacheReadPrice === "" ? null : Number(modelForm.cacheReadPrice),
      cacheWritePrice: modelForm.cacheWritePrice === "" ? null : Number(modelForm.cacheWritePrice),
    };
    try {
      if (modelDialogMode === "edit") {
        await api.updateProviderModel(modelForm.providerId, modelForm.id, payload);
      } else {
        await api.addProviderModel(modelForm.providerId, payload);
      }
      setModelForm(INITIAL_MODEL_FORM);
      setIsModelDialogOpen(false);
      setModelDialogMode("add");
      setExpandedProviders((prev) => ({ ...prev, [modelForm.providerId]: true }));
      showToast(modelDialogMode === "edit" ? "已更新模型。" : "已添加模型到 OpenClaw。");
      await refreshProviders();
      await refreshRouting();
      await refreshHome();
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      setBusyKey("");
    }
  }

  function toggleProviderExpanded(providerId) {
    setExpandedProviders((prev) => ({ ...prev, [providerId]: !prev[providerId] }));
  }

  async function handleFeedback(requestId, feedbackTier) {
    setBusyKey(`feedback-${requestId}`);
    setToast(null);
    try {
      await api.submitFeedback({ requestId, feedbackTier });
      await refreshFeedback(feedbackPage);
      await refreshHome();
      showToast(`Task feedback updated to ${feedbackTier}.`);
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      setBusyKey("");
    }
  }

  async function handleMemoryLabel(itemId, feedbackTier) {
    setBusyKey(`memory-${itemId}`);
    setToast(null);
    try {
      await api.updateMemoryLabel(itemId, { feedbackTier });
      await refreshMemory(memoryPage);
      showToast(`Memory label updated to ${feedbackTier}.`);
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      setBusyKey("");
    }
  }

  async function handleDeleteMemoryItem() {
    if (!deleteTarget?.id) return;
    const itemId = deleteTarget.id;
    setBusyKey(`memory-delete-${itemId}`);
    setToast(null);
    try {
      await api.deleteMemoryItem(itemId);
      setDeleteTarget(null);
      if ((memory.items?.length || 0) === 1 && memoryPage > 1) {
        setMemoryPage((current) => Math.max(current - 1, 1));
      } else {
        await refreshMemory(memoryPage);
      }
      showToast("Memory item deleted.");
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      setBusyKey("");
    }
  }

  async function handleDeleteTarget() {
    if (!deleteTarget) return;

    if (deleteTarget.kind === "memory") {
      await handleDeleteMemoryItem();
      return;
    }

    const actionKey =
      deleteTarget.kind === "provider"
        ? `provider-delete-${deleteTarget.providerId}`
        : `model-delete-${deleteTarget.providerId}-${deleteTarget.modelId}`;
    setBusyKey(actionKey);
    setToast(null);
    try {
      if (deleteTarget.kind === "provider") {
        await api.deleteProvider(deleteTarget.providerId);
        showToast("已从 OpenClaw 删除 Provider。");
      } else if (deleteTarget.kind === "model") {
        await api.deleteProviderModel(deleteTarget.providerId, deleteTarget.modelId);
        showToast("已从 OpenClaw 删除模型。");
      }
      setDeleteTarget(null);
      await refreshProviders();
      await refreshRouting();
      await refreshHome();
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      setBusyKey("");
    }
  }

  return (
    <div className="min-h-screen">
      <Sidebar page={page} setPage={setPage} health={health} hostLabel={hostLabel} connected={health?.status === "ok"} />
      <main className="ml-[240px] min-h-screen px-5 py-6">
        <div className="mx-auto w-full max-w-[min(100%,1600px)]">
          <div className="mb-8 flex items-start justify-between gap-4">
            <h1 className="text-4xl font-semibold tracking-tight text-slate-900">{pageTitleMap[page] || "Dashboard"}</h1>
            {page === "providers" ? (
              <button
                onClick={openAddProviderDialog}
                className="inline-flex items-center gap-2 rounded-2xl bg-slate-900 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-black"
              >
                <Plus className="h-4 w-4" />
                添加 Provider
              </button>
            ) : null}
          </div>
          {toast?.message ? (
            <div className="pointer-events-none fixed right-6 top-6 z-50">
              <div
                className={`max-w-md rounded-2xl px-4 py-3 text-sm font-medium shadow-lg ring-1 ${
                  toast.type === "error"
                    ? "bg-rose-50 text-rose-700 ring-rose-200"
                    : "bg-slate-900 text-white ring-slate-900/10"
                }`}
              >
                {toast.message}
              </div>
            </div>
          ) : null}

          {page === "home" && (
            <div className="space-y-6">
              <div className="grid grid-cols-1 gap-5 xl:grid-cols-4">
                <MetricCard
                  icon={LayoutDashboard}
                  label="Completions"
                  value={totals.completions ?? 0}
                  hint="Total routed requests persisted locally"
                />
                <MetricCard
                  icon={Database}
                  label="Prompt Tokens"
                  value={totals.prompt_tokens ?? 0}
                  hint="Aggregated input volume"
                />
                <MetricCard
                  icon={Cloud}
                  label="Completion Tokens"
                  value={totals.completion_tokens ?? 0}
                  hint="Aggregated output volume"
                />
                <MetricCard
                  icon={Route}
                  label="Models Active"
                  value={modelCount}
                  hint={`Strategy: ${health?.strategy || "-"}`}
                />
              </div>

              <Card title="Recent Tasks" subtitle="Latest 5 routed requests stored by dashboard memory.">
                {recent.length === 0 ? (
                  <LoadingRow text="No task history yet. Send a routed request to populate Home." />
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[1180px] text-left text-sm">
                      <thead className="text-slate-400">
                        <tr>
                          <th className="pb-4 pr-2 font-medium">Time</th>
                          <th className="min-w-[600px] w-[680px] max-w-[680px] pl-2 pr-3 pb-4 font-medium">Prompt</th>
                          <th className="pb-4 font-medium">Tier</th>
                          <th className="pb-4 font-medium">Strategy</th>
                          <th className="pb-4 font-medium">Model</th>
                          <th className="pb-4 font-medium">Input / Output</th>
                          <th className="pb-4 font-medium">Cost</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                        {recent.map((item) => (
                          <tr key={item.request_id} className="align-top">
                            <td className="py-4 pr-2 text-slate-500 whitespace-nowrap">{formatTimestamp(item.created_at)}</td>
                            <td className="min-w-[600px] w-[680px] max-w-[680px] py-4 pl-2 pr-3 text-slate-900 whitespace-normal break-words">
                              {item.prompt_preview || "-"}
                            </td>
                            <td className="py-4 pr-4">
                              <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${item.tier === "LOCAL" ? "bg-emerald-50 text-emerald-700" : "bg-sky-50 text-sky-700"}`}>
                                {item.tier || "-"}
                              </span>
                            </td>
                            <td className="py-4 pr-4 text-slate-500">{item.strategy || "-"}</td>
                            <td className="py-4 text-slate-900">{item.model || "-"}</td>
                            <td className="py-4 pr-4 text-slate-600 whitespace-nowrap">{formatTokenUsage(item)}</td>
                            <td className="py-4 text-slate-900 whitespace-nowrap">{formatCost(item.total_cost)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>
            </div>
          )}

          {page === "routing" && (
            <div className="space-y-6">
              <Card
                title="Current Runtime"
                subtitle="Edit runtime strategy and memory settings, then save them back to the config file."
                right={
                  <button
                    onClick={handleRoutingSave}
                    disabled={!routing || routing.readOnly || busyKey === "routing"}
                    className="inline-flex items-center gap-2 rounded-2xl bg-slate-900 px-4 py-2 text-sm font-medium text-white transition hover:bg-black disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <Save className="h-4 w-4" />
                    Save
                  </button>
                }
              >
                {!routing ? (
                  <LoadingRow text="Loading runtime config..." />
                ) : (
                  <div className="space-y-5">
                    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                      <TextField
                        label="Strategy"
                        value={routing.strategy || ""}
                        placeholder="rules"
                        disabled={routing.readOnly}
                        onChange={(value) => setRouting((prev) => ({ ...prev, strategy: value }))}
                      />
                      <label className="flex items-center justify-between rounded-2xl border border-slate-200 bg-white px-4 py-3">
                        <div>
                          <div className="text-sm font-medium text-slate-700">Memory</div>
                          <div className="mt-1 text-sm text-slate-500">
                            Enable routing memory for the current runtime.
                            {" "}
                            Status: {(routing.memoryAvailable ?? memory.available) ? "available" : "unavailable"}.
                          </div>
                        </div>
                        <input
                          type="checkbox"
                          checked={Boolean(routing.memoryEnabled)}
                          disabled={routing.readOnly}
                          onChange={(event) => setRouting((prev) => ({ ...prev, memoryEnabled: event.target.checked }))}
                          className="h-4 w-4 rounded border-slate-300 text-slate-900 focus:ring-slate-400 disabled:cursor-not-allowed"
                        />
                      </label>
                    </div>
                    <div className="grid grid-cols-1 gap-3 text-sm text-slate-600 sm:grid-cols-2">
                      <div className="flex justify-between rounded-2xl bg-slate-50 px-4 py-3">
                        <span>Models available</span>
                        <span className="font-medium text-slate-900">{availableModels.length}</span>
                      </div>
                      <div className="flex justify-between rounded-2xl bg-slate-50 px-4 py-3">
                        <span>Dashboard path</span>
                        <span className="font-medium text-slate-900">{health?.dashboard?.path || "/dashboard/"}</span>
                      </div>
                    </div>
                    {routing.readOnly ? (
                      <div className="rounded-2xl bg-amber-50 px-4 py-3 text-sm text-amber-700">
                        Current server config is not file-backed, so this page is read-only.
                      </div>
                    ) : null}
                  </div>
                )}
              </Card>

              <Card
                title="Routing"
                subtitle="Configure LOCAL and CLOUD target model ids, then save the config."
                right={
                  <button
                    onClick={handleRoutingSave}
                    disabled={!routing || routing.readOnly || busyKey === "routing"}
                    className="inline-flex items-center gap-2 rounded-2xl bg-slate-900 px-4 py-2 text-sm font-medium text-white transition hover:bg-black disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <Save className="h-4 w-4" />
                    Save
                  </button>
                }
              >
                {!routing ? (
                  <LoadingRow text="Loading routing config..." />
                ) : (
                  <div className="grid grid-cols-1 gap-5 xl:grid-cols-3">
                    <SelectField
                      label="LOCAL model ID"
                      value={routing.localModel}
                      options={availableModels}
                      disabled={routing.readOnly}
                      onChange={(value) => setRouting((prev) => ({ ...prev, localModel: value }))}
                    />
                    <SelectField
                      label="CLOUD model ID"
                      value={routing.cloudModel}
                      options={availableModels}
                      disabled={routing.readOnly}
                      onChange={(value) => setRouting((prev) => ({ ...prev, cloudModel: value }))}
                    />
                    <SelectField
                      label="Default model ID"
                      value={routing.defaultModel}
                      options={availableModels}
                      disabled={routing.readOnly}
                      onChange={(value) => setRouting((prev) => ({ ...prev, defaultModel: value }))}
                    />
                  </div>
                )}
              </Card>
            </div>
          )}

          {page === "providers" && (
            <Card
              title="OpenClaw 模型提供商"
              subtitle={`读取与写入 ~/.openclaw/openclaw.json 的 models.providers${
                providers.openclawConfigPath ? `（当前：${providers.openclawConfigPath}）` : ""
              }。路由页下拉的可用模型会合并此处与 clawchips.yaml 的 llms。`}
            >
              {providers.openclawError ? (
                <div className="mb-4 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
                  无法解析 openclaw.json：{providers.openclawError}
                </div>
              ) : null}
              {providers.yamlLlmsCount > 0 ? (
                <div className="mb-4 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
                  clawchips.yaml 中另有 {providers.yamlLlmsCount} 个 llms 提供商；同名 provider 时以 YAML 为准。
                </div>
              ) : null}
              {providers.providers?.length ? (
                <div className="overflow-x-auto">
                  <div className="min-w-[1240px]">
                    <div className="grid grid-cols-[0.3fr_1fr_2fr_1.5fr_0.7fr_1.2fr] gap-4 border-b border-slate-100 pb-3 text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">
                      <div />
                      <div>Provider</div>
                      <div>Base URL</div>
                      <div>Models</div>
                      <div>Status</div>
                      <div className="text-right">Action</div>
                    </div>
                    <div className="divide-y divide-slate-100">
                      {providers.providers.map((provider) => {
                        const expanded = Boolean(expandedProviders[provider.id]);
                        return (
                          <div key={provider.id} className="py-4">
                            <div className="grid grid-cols-[0.3fr_1fr_2fr_1.5fr_0.7fr_1.2fr] items-start gap-4">
                              <div>
                                <button
                                  type="button"
                                  onClick={() => toggleProviderExpanded(provider.id)}
                                  className="rounded-xl border border-slate-200 bg-white p-2 text-slate-500 transition hover:bg-slate-50 hover:text-slate-700"
                                  aria-label={expanded ? "Collapse provider" : "Expand provider"}
                                >
                                  {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                                </button>
                              </div>
                              <div className="min-w-0">
                                <div className="font-medium text-slate-900">{provider.id}</div>
                                <div className="mt-1 text-xs text-slate-400">{provider.api || "openai-completions"}</div>
                              </div>
                              <div className="break-all text-sm text-slate-500">{provider.baseUrl || "-"}</div>
                              <div className="flex flex-wrap gap-2">
                                {(provider.models || []).length ? (
                                  provider.models.map((model) => (
                                    <span
                                      key={`${provider.id}-${model.id}`}
                                      className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-600"
                                    >
                                      {model.id}
                                    </span>
                                  ))
                                ) : (
                                  <span className="text-sm text-slate-400">No models</span>
                                )}
                              </div>
                              <div>
                                <span
                                  className={`rounded-full px-2.5 py-1 text-xs font-semibold ${
                                    provider.hasApiKey ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"
                                  }`}
                                >
                                  {provider.hasApiKey ? "Key Set" : "No Key"}
                                </span>
                              </div>
                              <div className="flex justify-end gap-2">
                                <button
                                  type="button"
                                  onClick={() => openAddModelDialog(provider)}
                                  className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 transition hover:bg-slate-50"
                                >
                                  ADD MODEL
                                </button>
                                <button
                                  type="button"
                                  onClick={() => openEditProviderDialog(provider)}
                                  className="rounded-xl border border-indigo-200 bg-indigo-50 px-3 py-2 text-xs font-semibold text-indigo-700 transition hover:bg-indigo-100"
                                >
                                  EDIT
                                </button>
                                <button
                                  type="button"
                                  onClick={() =>
                                    setDeleteTarget({
                                      kind: "provider",
                                      providerId: provider.id,
                                      prompt: `${provider.id} (${provider.modelCount || 0} model(s))`,
                                    })
                                  }
                                  disabled={busyKey === `provider-delete-${provider.id}`}
                                  className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-semibold text-rose-700 transition hover:bg-rose-100 disabled:opacity-50"
                                >
                                  DELETE
                                </button>
                              </div>
                            </div>
                            {expanded ? (
                              <div className="mt-4 overflow-x-auto rounded-2xl border border-slate-100 bg-slate-50/70">
                                {(provider.models || []).length ? (
                                  <table className="w-full min-w-[980px] text-left text-sm">
                                    <thead className="text-slate-400">
                                      <tr>
                                        <th className="px-4 py-3 font-medium">Model ID</th>
                                        <th className="px-4 py-3 font-medium">Backend Name</th>
                                        <th className="px-4 py-3 font-medium">Description</th>
                                        <th className="px-4 py-3 font-medium">Max Tokens</th>
                                        <th className="px-4 py-3 font-medium">Context</th>
                                        <th className="px-4 py-3 text-right font-medium">Action</th>
                                      </tr>
                                    </thead>
                                    <tbody className="divide-y divide-slate-100">
                                      {provider.models.map((model) => (
                                        <tr key={`${provider.id}-${model.id}`} className="bg-white/80">
                                          <td className="px-4 py-3 font-medium text-slate-900">{model.id}</td>
                                          <td className="px-4 py-3 text-slate-600">{model.name || "-"}</td>
                                          <td className="px-4 py-3 text-slate-600">{model.description || "-"}</td>
                                          <td className="px-4 py-3 text-slate-600">{model.maxTokens ?? "-"}</td>
                                          <td className="px-4 py-3 text-slate-600">{model.contextWindow ?? "-"}</td>
                                          <td className="px-4 py-3">
                                            <div className="flex justify-end gap-2">
                                              <button
                                                type="button"
                                                onClick={() => openEditModelDialog(provider, model)}
                                                className="rounded-xl border border-indigo-200 bg-indigo-50 px-3 py-2 text-xs font-semibold text-indigo-700 transition hover:bg-indigo-100"
                                              >
                                                EDIT
                                              </button>
                                              <button
                                                type="button"
                                                onClick={() =>
                                                  setDeleteTarget({
                                                    kind: "model",
                                                    providerId: provider.id,
                                                    modelId: model.id,
                                                    prompt: `${provider.id} / ${model.id}`,
                                                  })
                                                }
                                                disabled={busyKey === `model-delete-${provider.id}-${model.id}`}
                                                className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-semibold text-rose-700 transition hover:bg-rose-100 disabled:opacity-50"
                                              >
                                                DELETE
                                              </button>
                                            </div>
                                          </td>
                                        </tr>
                                      ))}
                                    </tbody>
                                  </table>
                                ) : (
                                  <div className="px-4 py-5 text-sm text-slate-400">No models</div>
                                )}
                              </div>
                            ) : null}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </div>
              ) : (
                <LoadingRow
                  text={
                    providers.openclawError
                      ? "修复 openclaw.json 后即可在此管理 Provider。"
                      : "暂无 Provider。点击右上角「Add Provider」写入 OpenClaw，或先在 OpenClaw 网关中配置模型。"
                  }
                />
              )}
            </Card>
          )}

          {isProviderDialogOpen ? (
            <div
              className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 px-4 backdrop-blur-sm"
              onClick={() => setIsProviderDialogOpen(false)}
            >
              <div
                className="w-full max-w-2xl rounded-3xl bg-white shadow-2xl ring-1 ring-black/[0.08]"
                onClick={(event) => event.stopPropagation()}
              >
                <div className="flex items-start justify-between gap-4 border-b border-black/[0.04] px-6 py-5">
                  <div>
                    <h2 className="text-lg font-semibold tracking-tight text-slate-900">
                      {providerDialogMode === "edit" ? "Edit Provider" : "Add Provider"}
                    </h2>
                  </div>
                  <button
                    onClick={() => setIsProviderDialogOpen(false)}
                    className="rounded-2xl p-2 text-slate-400 transition hover:bg-slate-100 hover:text-slate-600"
                    aria-label="Close dialog"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
                <form className="space-y-4 px-6 py-5" onSubmit={handleProviderSubmit}>
                  <TextField
                    label="Provider name"
                    value={providerForm.name}
                    onChange={(value) => setProviderForm((prev) => ({ ...prev, name: value }))}
                    placeholder="openrouter"
                    disabled={providerDialogMode === "edit"}
                  />
                  <TextField
                    label="Base URL"
                    value={providerForm.baseUrl}
                    onChange={(value) => setProviderForm((prev) => ({ ...prev, baseUrl: value }))}
                    placeholder="https://openrouter.ai/api/v1"
                  />
                  <TextField
                    label="API key"
                    value={providerForm.apiKey}
                    onChange={(value) => setProviderForm((prev) => ({ ...prev, apiKey: value }))}
                    placeholder="sk-..."
                    type={showProviderApiKey ? "text" : "password"}
                    rightAdornment={
                      <button
                        type="button"
                        onClick={() => setShowProviderApiKey((prev) => !prev)}
                        className="rounded-lg p-1 text-slate-400 transition hover:bg-slate-100 hover:text-slate-600"
                        aria-label={showProviderApiKey ? "Hide API key" : "Show API key"}
                      >
                        {showProviderApiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                      </button>
                    }
                  />
                  {providerDialogMode === "add" ? (
                    <TextField
                      label="Model IDs"
                      value={providerForm.models}
                      onChange={(value) => setProviderForm((prev) => ({ ...prev, models: value }))}
                      placeholder="gpt-4o-mini, claude-3-7-sonnet"
                    />
                  ) : null}
                  <TextField
                    label="Plan / note"
                    value={providerForm.plan}
                    onChange={(value) => setProviderForm((prev) => ({ ...prev, plan: value }))}
                    placeholder="team-pro"
                  />
                  <TextField
                    label="Auth mode"
                    value={providerForm.authMode}
                    onChange={(value) => setProviderForm((prev) => ({ ...prev, authMode: value }))}
                    placeholder="auto"
                  />
                  <div className="flex items-center justify-end gap-3 pt-2">
                    <button
                      type="button"
                      onClick={() => setIsProviderDialogOpen(false)}
                      className="rounded-2xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
                    >
                      Cancel
                    </button>
                    <button
                      type="submit"
                      disabled={busyKey === "provider"}
                      className="inline-flex items-center gap-2 rounded-2xl bg-slate-900 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-black disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <ServerCog className="h-4 w-4" />
                      {providerDialogMode === "edit" ? "Save Provider" : "Add Provider"}
                    </button>
                  </div>
                </form>
              </div>
            </div>
          ) : null}

          {isModelDialogOpen ? (
            <div
              className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 px-4 backdrop-blur-sm"
              onClick={() => setIsModelDialogOpen(false)}
            >
              <div
                className="w-full max-w-2xl rounded-3xl bg-white shadow-2xl ring-1 ring-black/[0.08]"
                onClick={(event) => event.stopPropagation()}
              >
                <div className="flex items-start justify-between gap-4 border-b border-black/[0.04] px-6 py-5">
                  <div>
                    <h2 className="text-lg font-semibold tracking-tight text-slate-900">
                      {modelDialogMode === "edit" ? "Edit Model" : "Add Model"}
                    </h2>
                    <div className="mt-1 text-sm text-slate-500">Provider: {modelForm.providerId || "-"}</div>
                  </div>
                  <button
                    onClick={() => setIsModelDialogOpen(false)}
                    className="rounded-2xl p-2 text-slate-400 transition hover:bg-slate-100 hover:text-slate-600"
                    aria-label="Close dialog"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
                <form className="space-y-4 px-6 py-5" onSubmit={handleModelSubmit}>
                  <TextField
                    label="Model ID"
                    value={modelForm.id}
                    onChange={(value) => setModelForm((prev) => ({ ...prev, id: value }))}
                    placeholder="gpt-4o-mini"
                    disabled={modelDialogMode === "edit"}
                  />
                  <TextField
                    label="Backend name"
                    value={modelForm.name}
                    onChange={(value) => setModelForm((prev) => ({ ...prev, name: value }))}
                    placeholder="gpt-4o-mini"
                  />
                  <TextField
                    label="Description"
                    value={modelForm.description}
                    onChange={(value) => setModelForm((prev) => ({ ...prev, description: value }))}
                    placeholder="Fast responses, daily chat"
                  />
                  <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                    <TextField
                      label="Max tokens"
                      value={modelForm.maxTokens}
                      onChange={(value) => setModelForm((prev) => ({ ...prev, maxTokens: value }))}
                      placeholder="4096"
                      type="number"
                    />
                    <TextField
                      label="Context window"
                      value={modelForm.contextWindow}
                      onChange={(value) => setModelForm((prev) => ({ ...prev, contextWindow: value }))}
                      placeholder="200000"
                      type="number"
                    />
                  </div>
                  <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                    <TextField
                      label="Input price"
                      value={modelForm.inputPrice}
                      onChange={(value) => setModelForm((prev) => ({ ...prev, inputPrice: value }))}
                      placeholder="0"
                      type="number"
                    />
                    <TextField
                      label="Output price"
                      value={modelForm.outputPrice}
                      onChange={(value) => setModelForm((prev) => ({ ...prev, outputPrice: value }))}
                      placeholder="0"
                      type="number"
                    />
                  </div>
                  <div className="flex items-center justify-end gap-3 pt-2">
                    <button
                      type="button"
                      onClick={() => setIsModelDialogOpen(false)}
                      className="rounded-2xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
                    >
                      Cancel
                    </button>
                    <button
                      type="submit"
                      disabled={busyKey === `${modelDialogMode}-model-${modelForm.providerId}-${modelForm.id}`}
                      className="inline-flex items-center gap-2 rounded-2xl bg-slate-900 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-black disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <ServerCog className="h-4 w-4" />
                      {modelDialogMode === "edit" ? "Save Model" : "Add Model"}
                    </button>
                  </div>
                </form>
              </div>
            </div>
          ) : null}

          {page === "feedback" && (
            <Card title="All Tasks" subtitle="All routed tasks with pagination and LOCAL/CLOUD feedback actions.">
              {feedback.items?.length ? (
                <>
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[1320px] text-left text-sm">
                      <thead className="text-slate-400">
                        <tr>
                          <th className="pb-4 pr-2 font-medium">Time</th>
                          <th className="min-w-[600px] w-[680px] max-w-[680px] pl-2 pr-3 pb-4 font-medium">Prompt</th>
                          <th className="pb-4 font-medium">Model</th>
                          <th className="pb-4 font-medium">Tier</th>
                          <th className="pb-4 font-medium">Input / Output</th>
                          <th className="pb-4 font-medium">Cost</th>
                          <th className="pb-4 font-medium">Feedback</th>
                          <th className="pb-4 text-center font-medium">Actions</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                        {feedback.items.map((item) => (
                          <tr key={item.request_id}>
                            <td className="py-4 pr-2 text-slate-500 whitespace-nowrap">{formatTimestamp(item.created_at)}</td>
                            <td className="min-w-[600px] w-[680px] max-w-[680px] py-4 pl-2 pr-3 text-slate-900 whitespace-normal break-words">
                              {item.prompt_preview || "-"}
                            </td>
                            <td className="py-4 pr-4 text-slate-600">{item.model || "-"}</td>
                            <td className="py-4 pr-4">
                              <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${item.tier === "LOCAL" ? "bg-emerald-50 text-emerald-700" : "bg-sky-50 text-sky-700"}`}>
                                {item.tier || "-"}
                              </span>
                            </td>
                            <td className="py-4 pr-4 text-slate-600 whitespace-nowrap">{formatTokenUsage(item)}</td>
                            <td className="py-4 pr-4 text-slate-900 whitespace-nowrap">{formatCost(item.total_cost)}</td>
                            <td className="py-4 pr-4">
                              <span
                                className={`rounded-full px-2.5 py-1 text-xs font-semibold ${
                                  item.has_feedback
                                    ? item.feedback_tier === "LOCAL"
                                      ? "bg-emerald-50 text-emerald-700"
                                      : "bg-sky-50 text-sky-700"
                                    : "bg-rose-50 text-rose-700"
                                }`}
                              >
                                {item.has_feedback ? item.feedback_tier || "-" : "NONE"}
                              </span>
                            </td>
                            <td className="py-4 text-center">
                              <div className="flex justify-center gap-2">
                                <button
                                  onClick={() => handleFeedback(item.request_id, "LOCAL")}
                                  disabled={busyKey === `feedback-${item.request_id}`}
                                  className="rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs font-semibold text-emerald-700 transition hover:bg-emerald-100 disabled:opacity-50"
                                >
                                  LOCAL
                                </button>
                                <button
                                  onClick={() => handleFeedback(item.request_id, "CLOUD")}
                                  disabled={busyKey === `feedback-${item.request_id}`}
                                  className="rounded-xl border border-sky-200 bg-sky-50 px-3 py-2 text-xs font-semibold text-sky-700 transition hover:bg-sky-100 disabled:opacity-50"
                                >
                                  CLOUD
                                </button>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  <div className="mt-6 flex items-center justify-between">
                    <div className="text-sm text-slate-500">
                      Page {feedback.page || feedbackPage} / {feedback.total_pages || 1} · {feedback.total || 0} task(s)
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => setFeedbackPage((current) => Math.max(current - 1, 1))}
                        disabled={feedbackPage <= 1}
                        className="inline-flex items-center gap-2 rounded-2xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        <ChevronLeft className="h-4 w-4" />
                        Prev
                      </button>
                      <button
                        onClick={() => setFeedbackPage((current) => Math.min(current + 1, feedback.total_pages || current))}
                        disabled={!feedback.total_pages || feedbackPage >= feedback.total_pages}
                        className="inline-flex items-center gap-2 rounded-2xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        Next
                        <ChevronRight className="h-4 w-4" />
                      </button>
                    </div>
                  </div>
                </>
              ) : (
                <LoadingRow text="No task history yet. Routed requests will appear here automatically." />
              )}
            </Card>
          )}

          {page === "memory" && (
            <Card title="Memory Items" subtitle="Memory database records with pagination and LOCAL/CLOUD label actions.">
              {!memory.available ? (
                <LoadingRow text={memory.enabled ? "Memory is enabled but unavailable right now." : "Memory is disabled in the current localrouter config."} />
              ) : memory.items?.length ? (
                <>
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[1120px] text-left text-sm">
                      <thead className="text-slate-400">
                        <tr>
                          <th className="pb-4 pr-2 font-medium">Time</th>
                          <th className="min-w-[600px] w-[680px] max-w-[680px] pl-2 pr-3 pb-4 font-medium">Prompt</th>
                          <th className="pb-4 font-medium">Feedback</th>
                          <th className="pb-4 text-center font-medium">Actions</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                        {memory.items.map((item) => (
                          <tr key={item.id}>
                            <td className="py-4 pr-2 text-slate-500 whitespace-nowrap">{formatTimestamp(item.created_at)}</td>
                            <td className="min-w-[600px] w-[680px] max-w-[680px] py-4 pl-2 pr-3 text-slate-900 whitespace-normal break-words">
                              {item.prompt_preview || item.query || "-"}
                            </td>
                            <td className="py-4 pr-4">
                              <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${item.feedback_tier === "LOCAL" ? "bg-emerald-50 text-emerald-700" : "bg-sky-50 text-sky-700"}`}>
                                {item.feedback_tier || item.tier || "-"}
                              </span>
                            </td>
                            <td className="py-4 text-center">
                              <div className="flex justify-center gap-2">
                                <button
                                  onClick={() => handleMemoryLabel(item.id, "LOCAL")}
                                  disabled={busyKey === `memory-${item.id}`}
                                  className="rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs font-semibold text-emerald-700 transition hover:bg-emerald-100 disabled:opacity-50"
                                >
                                  LOCAL
                                </button>
                                <button
                                  onClick={() => handleMemoryLabel(item.id, "CLOUD")}
                                  disabled={busyKey === `memory-${item.id}`}
                                  className="rounded-xl border border-sky-200 bg-sky-50 px-3 py-2 text-xs font-semibold text-sky-700 transition hover:bg-sky-100 disabled:opacity-50"
                                >
                                  CLOUD
                                </button>
                                <button
                                  onClick={() => setDeleteTarget({ kind: "memory", id: item.id, prompt: item.prompt_preview || item.query || "-" })}
                                  disabled={busyKey === `memory-${item.id}` || busyKey === `memory-delete-${item.id}`}
                                  className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-semibold text-rose-700 transition hover:bg-rose-100 disabled:opacity-50"
                                >
                                  Delete
                                </button>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  <div className="mt-6 flex items-center justify-between">
                    <div className="text-sm text-slate-500">
                      Page {memory.page || memoryPage} / {memory.total_pages || 1} · {memory.total || 0} item(s)
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => setMemoryPage((current) => Math.max(current - 1, 1))}
                        disabled={memoryPage <= 1}
                        className="inline-flex items-center gap-2 rounded-2xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        <ChevronLeft className="h-4 w-4" />
                        Prev
                      </button>
                      <button
                        onClick={() => setMemoryPage((current) => Math.min(current + 1, memory.total_pages || current))}
                        disabled={!memory.total_pages || memoryPage >= memory.total_pages}
                        className="inline-flex items-center gap-2 rounded-2xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        Next
                        <ChevronRight className="h-4 w-4" />
                      </button>
                    </div>
                  </div>
                </>
              ) : (
                <LoadingRow text="No memory items yet." />
              )}
            </Card>
          )}

          {deleteTarget ? (
            <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-900/30 px-4">
              <div className="w-full max-w-lg rounded-3xl bg-white p-6 shadow-2xl ring-1 ring-black/5">
                <div className="text-lg font-semibold text-slate-900">Confirm Delete</div>
                <div className="mt-3 text-sm text-slate-600">
                  {deleteTarget.kind === "memory"
                    ? "Delete this memory item? This action cannot be undone."
                    : deleteTarget.kind === "provider"
                      ? "Delete this provider and all models under it? This action cannot be undone."
                      : "Delete this model? This action cannot be undone."}
                </div>
                <div className="mt-4 rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-700">
                  {deleteTarget.prompt}
                </div>
                <div className="mt-6 flex items-center justify-end gap-3">
                  <button
                    type="button"
                    onClick={() => setDeleteTarget(null)}
                    disabled={
                      busyKey === `memory-delete-${deleteTarget.id}` ||
                      busyKey === `provider-delete-${deleteTarget.providerId}` ||
                      busyKey === `model-delete-${deleteTarget.providerId}-${deleteTarget.modelId}`
                    }
                    className="rounded-2xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 transition hover:bg-slate-50 disabled:opacity-50"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={handleDeleteTarget}
                    disabled={
                      busyKey === `memory-delete-${deleteTarget.id}` ||
                      busyKey === `provider-delete-${deleteTarget.providerId}` ||
                      busyKey === `model-delete-${deleteTarget.providerId}-${deleteTarget.modelId}`
                    }
                    className="rounded-2xl bg-rose-600 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-rose-700 disabled:opacity-50"
                  >
                    Delete
                  </button>
                </div>
              </div>
            </div>
          ) : null}
        </div>
      </main>
    </div>
  );
}
