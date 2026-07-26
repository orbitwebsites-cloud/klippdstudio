import axios from "axios";

// Production is served separately from the API. Keep an explicit fallback so a
// missing build variable cannot send API requests to the static Vercel site.
const BACKEND_URL = (process.env.REACT_APP_BACKEND_URL || "https://api.klippdstudio.com").replace(/\/$/, "");
export const API = `${BACKEND_URL}/api`;

// Stable per-browser identity used to isolate each visitor's projects and
// clips on the shared backend. Without this the server groups everyone into one
// bucket, so unrelated users would see each other's uploads. Persisted in
// localStorage so it survives reloads; regenerated only if storage is cleared.
const CLIENT_ID_KEY = "klippd_client_id";

const randomClientId = () => {
    try {
        if (window.crypto?.randomUUID) return window.crypto.randomUUID();
    } catch { /* fall through to manual id */ }
    return `c-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
};

export const getClientId = () => {
    try {
        let id = window.localStorage.getItem(CLIENT_ID_KEY);
        if (!id) {
            id = randomClientId();
            window.localStorage.setItem(CLIENT_ID_KEY, id);
        }
        return id;
    } catch {
        // Storage blocked (e.g. private mode): fall back to a per-session id so
        // requests are still scoped, even if not stable across reloads.
        if (!window.__klippdClientId) window.__klippdClientId = randomClientId();
        return window.__klippdClientId;
    }
};

// Real accounts are provided by Clerk. When a publishable key is configured the
// app runs in authenticated mode: requests carry the Clerk session JWT and data
// is scoped to the Clerk user id. Without a key we keep the anonymous
// per-browser isolation mode so local dev and self-hosting work unchanged.
export const clerkEnabled = () => Boolean(process.env.REACT_APP_CLERK_PUBLISHABLE_KEY);

// ClerkProvider exposes the active session on window.Clerk. klipApi is not a
// React component, so we read the token from there rather than via a hook.
const clerkSessionToken = async () => {
    try {
        if (window.Clerk?.session) return await window.Clerk.session.getToken();
    } catch { /* not signed in yet / Clerk still loading */ }
    return null;
};

const api = axios.create({ baseURL: API, timeout: 60000 });

// Attach identity to every API request: the Clerk session JWT when signed in,
// otherwise the anonymous per-browser client id. Media/download URLs are loaded
// as <video src> etc. and cannot carry headers, so those helpers append a
// credential to the query string instead (see withMediaCredential).
api.interceptors.request.use(async (config) => {
    config.headers = config.headers || {};
    if (clerkEnabled()) {
        const token = await clerkSessionToken();
        if (token) config.headers["Authorization"] = `Bearer ${token}`;
    } else {
        config.headers["X-Klippd-Client"] = getClientId();
    }
    return config;
});

// Cached media token (see refreshMediaToken). Read synchronously by the media
// URL helpers, which are called during render.
let mediaTokenCache = null;
export const refreshMediaToken = async () => {
    const { data } = await api.get("/media-token");
    mediaTokenCache = data?.token || null;
    return mediaTokenCache;
};
export const clearMediaToken = () => { mediaTokenCache = null; };

const withMediaCredential = (url, mediaToken) => {
    const sep = url.includes("?") ? "&" : "?";
    if (clerkEnabled()) {
        const token = mediaToken || mediaTokenCache;
        return token ? `${url}${sep}mt=${encodeURIComponent(token)}` : url;
    }
    return `${url}${sep}client=${encodeURIComponent(getClientId())}`;
};

export const listProjects = () => api.get("/projects").then((r) => {
    if (!Array.isArray(r.data)) {
        throw new Error("The server returned an invalid projects response.");
    }
    return r.data;
});
export const getProject = (id) => api.get(`/projects/${id}`).then((r) => r.data);
export const saveEditOptions = (id, options) =>
    api.put(`/projects/${id}/edit-options`, options).then((r) => r.data);
export const deleteProject = (id) => api.delete(`/projects/${id}`).then((r) => r.data);

// Chunked upload with per-chunk retries + resume support.
// - Chunk size: 4MB (safely under any ingress limit).
// - Retries: each chunk up to 4 times with exponential backoff.
// - Resume: previously-uploaded upload_id (from localStorage keyed by file identity)
//   is checked via /uploads/status to skip already-received chunks.
const CHUNK_SIZE = 4 * 1024 * 1024;
const MAX_CHUNK_RETRIES = 4;

// Storage key so a page reload lets user retry same file without re-uploading chunks
const RESUME_KEY = (file) => `klippd_resume_${file.name}_${file.size}_${file.lastModified}`;

const readResumeId = (key) => {
    try { return window.localStorage.getItem(key); }
    catch { return null; }
};

const writeResumeId = (key, value) => {
    try { window.localStorage.setItem(key, value); }
    catch { /* Upload still works when storage is blocked. */ }
};

const clearResumeId = (key) => {
    try { window.localStorage.removeItem(key); }
    catch { /* Nothing else to clean up. */ }
};

export const apiErrorMessage = (error, fallback = "Something went wrong") => {
    const detail = error?.response?.data?.detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (error?.code === "ECONNABORTED") return "The server took too long to respond. Try again.";
    if (!error?.response && error?.message === "Network Error") {
        return "Cannot reach the Klipped Studio server. Check the backend URL and try again.";
    }
    return error?.message || fallback;
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function uploadChunkWithRetry(uploadId, index, blob, onChunkProgress) {
    let lastErr = null;
    for (let attempt = 0; attempt < MAX_CHUNK_RETRIES; attempt++) {
        try {
            const fd = new FormData();
            fd.append("file", blob, `chunk_${index}`);
            await api.post(`/uploads/chunk/${uploadId}?index=${index}`, fd, {
                timeout: 0,
                onUploadProgress: onChunkProgress,
            });
            return;
        } catch (e) {
            lastErr = e;
            const status = e?.response?.status;
            // 404 (session gone) or 4xx client errors → do not retry
            if (status === 404 || (status >= 400 && status < 500 && status !== 408 && status !== 429)) {
                throw e;
            }
            const backoff = Math.min(8000, 500 * 2 ** attempt) + Math.random() * 400;
            await sleep(backoff);
        }
    }
    throw lastErr;
}

export const uploadVideo = async (file, onProgress) => {
    const total_chunks = Math.max(1, Math.ceil(file.size / CHUNK_SIZE));

    // Try to resume an existing session for this exact file
    const resumeKey = RESUME_KEY(file);
    let upload_id = null;
    let received = new Set();
    const cached = readResumeId(resumeKey);
    if (cached) {
        try {
            const { data: st } = await api.get(`/uploads/status/${cached}`);
            if (st?.total_chunks === total_chunks && st?.size === file.size) {
                upload_id = cached;
                received = new Set(st.received_chunks || []);
            }
        } catch {
            // Session expired/gone — fall through to fresh init
            clearResumeId(resumeKey);
        }
    }

    // Fresh init if no resumable session
    if (!upload_id) {
        const { data: init } = await api.post("/uploads/init", {
            filename: file.name,
            size: file.size,
            total_chunks,
        });
        upload_id = init.upload_id;
        writeResumeId(resumeKey, upload_id);
    }

    // Upload each chunk not yet received
    let uploadedBytes = [...received].reduce((total, index) => {
        if (!Number.isInteger(index) || index < 0 || index >= total_chunks) return total;
        const start = index * CHUNK_SIZE;
        return total + Math.max(0, Math.min(CHUNK_SIZE, file.size - start));
    }, 0);
    if (onProgress) onProgress(Math.min(99, Math.round((uploadedBytes / file.size) * 100)));

    for (let i = 0; i < total_chunks; i++) {
        if (received.has(i)) continue;
        const start = i * CHUNK_SIZE;
        const end = Math.min(file.size, start + CHUNK_SIZE);
        const chunkSize = end - start;
        const chunk = file.slice(start, end);

        await uploadChunkWithRetry(upload_id, i, chunk, (evt) => {
            if (onProgress && file.size) {
                const currentChunkLoaded = evt.loaded || 0;
                const pct = Math.round(((uploadedBytes + currentChunkLoaded) / file.size) * 100);
                onProgress(Math.min(99, pct));
            }
        });

        uploadedBytes += chunkSize;
        received.add(i);
        if (onProgress) onProgress(Math.min(99, Math.round((uploadedBytes / file.size) * 100)));
    }

    // Finalize
    const { data: project } = await api.post(`/uploads/finalize/${upload_id}`);
    clearResumeId(resumeKey);
    if (onProgress) onProgress(100);
    return project;
};

export const analyzeProject = (id, options = {}) =>
    api.post(`/projects/${id}/analyze`, options).then((r) => r.data);
export const getTrainingDashboard = () => api.get("/training/dashboard").then((r) => r.data);
export const createTrainingReference = (body) => api.post("/training/references", body).then((r) => r.data);
export const createTrainingProfile = (body) => api.post("/training/profiles", body).then((r) => r.data);
export const activateTrainingProfile = (id) => api.post(`/training/profiles/${id}/activate`).then((r) => r.data);
export const brollSearch = (pid, query) =>
    api.get(`/projects/${pid}/broll_search`, { params: { query } }).then((r) => r.data);
export const getAssetPackStatus = (niche) =>
    api.get("/asset-packs/status", { params: { niche } }).then((r) => r.data);
export const resolveAssetPack = (niche) =>
    api.post("/asset-packs/resolve", { niche }, { params: { niche }, timeout: 0 }).then((r) => r.data);
export const uploadCustomBroll = (pid, file, onProgress, rightsAttested = false) => {
    if (!rightsAttested) return Promise.reject(new Error("Asset rights confirmation is required"));
    const fd = new FormData();
    fd.append("file", file);
    fd.append("rights_status", "user_owned_attested");
    fd.append("rights_attestation", "I own or have commercial rights to this asset");
    fd.append("license_id", "user-attestation-v1");
    return api
        .post(`/projects/${pid}/broll_upload`, fd, {
            timeout: 0,
            onUploadProgress: (evt) => {
                if (onProgress && evt.total)
                    onProgress(Math.round((evt.loaded * 100) / evt.total));
            },
        })
        .then((r) => r.data);
};
export const extractViralClips = (pid) =>
    api.post(`/projects/${pid}/viral_clips`).then((r) => r.data);
export const renderProject = (id, opts) =>
    api.post(`/projects/${id}/render`, opts).then((r) => r.data);

// Premium editing surfaces are intentionally kept behind their own API helpers.
// Deployments that do not expose them yet return 404/501; the components hide
// themselves without affecting the rest of the editor.
export const isFeatureUnavailable = (error) =>
    error?.response?.status === 404 || error?.response?.status === 501;

export const getCreatorProfiles = () =>
    api.get("/creator-profiles").then((r) => r.data);
export const createCreatorProfile = (payload) =>
    api.post("/creator-profiles", payload).then((r) => r.data);
export const analyzeCreatorProfile = (payload) =>
    api.post("/creator-profiles/analyze", payload, { timeout: 0 }).then((r) => r.data);

export const getEditChatHistory = (projectId) =>
    api.get(`/projects/${projectId}/edit-chat/history`).then((r) => r.data);
export const previewEditChat = (projectId, payload) =>
    api.post(`/projects/${projectId}/edit-chat/preview`, payload, { timeout: 0 }).then((r) => r.data);
export const applyEditChatPreview = (projectId, previewId) =>
    api.post(`/projects/${projectId}/edit-chat/apply`, { preview_id: previewId }, { timeout: 0 }).then((r) => r.data);
export const undoEditChat = (projectId) =>
    api.post(`/projects/${projectId}/edit-chat/undo`).then((r) => r.data);
export const redoEditChat = (projectId) =>
    api.post(`/projects/${projectId}/edit-chat/redo`).then((r) => r.data);

export const getHealth = () => api.get("/health").then((r) => r.data);
export const getSubscription = () => api.get("/subscription").then((r) => r.data);
export const createCheckout = (plan) => api.post("/billing/checkout", { plan }).then((r) => r.data);
export const createBillingPortal = () => api.post("/billing/portal").then((r) => r.data);

// The optional `mediaToken` argument lets callers pass the token from a hook so
// the URL updates (and the media element reloads) as soon as the token is ready.
export const mediaOriginal = (id, mediaToken) => withMediaCredential(`${API}/media/original/${id}`, mediaToken);
export const mediaOutput = (id, mediaToken) => withMediaCredential(`${API}/media/output/${id}`, mediaToken);
export const mediaClip = (id, label, mediaToken) => withMediaCredential(`${API}/media/clip/${id}/${encodeURIComponent(label)}`, mediaToken);
export const mediaThumbnail = () => null;
export const downloadUrl = (id, clipLabel, mediaToken) =>
    clipLabel
        ? withMediaCredential(`${API}/projects/${id}/download?clip=${encodeURIComponent(clipLabel)}`, mediaToken)
        : withMediaCredential(`${API}/projects/${id}/download`, mediaToken);

export default api;
