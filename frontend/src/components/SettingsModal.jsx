import { useEffect, useState } from "react";
import { X, Key, ExternalLink, CheckCircle2, XCircle, Loader2 } from "lucide-react";
import { apiErrorMessage, getKeysStatus, saveKeys, testKeys } from "@/lib/klipApi";
import { toast } from "sonner";

const PROVIDERS = [
    {
        id: "groq",
        name: "Groq",
        role: "Transcription + Primary LLM",
        url: "https://console.groq.com/keys",
        placeholder: "gsk_...",
        required: true,
    },
    {
        id: "cerebras",
        name: "Cerebras",
        role: "Fallback LLM",
        url: "https://cloud.cerebras.ai/",
        placeholder: "csk-...",
        required: false,
    },
];

export default function SettingsModal({ open, onClose, onSaved }) {
    const [values, setValues] = useState({ groq: "", cerebras: "" });
    const [status, setStatus] = useState({ groq: false, cerebras: false });
    const [testResults, setTestResults] = useState(null);
    const [testing, setTesting] = useState(false);
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        if (open) {
            getKeysStatus().then(setStatus).catch((error) => {
                toast.error(apiErrorMessage(error, "Could not load key status"));
            });
            setValues({ groq: "", cerebras: "" });
            setTestResults(null);
        }
    }, [open]);

    if (!open) return null;

    const handleSave = async () => {
        setSaving(true);
        const payload = {};
        for (const p of PROVIDERS) {
            if (values[p.id]?.trim()) payload[p.id] = values[p.id].trim();
        }
        if (Object.keys(payload).length === 0) {
            toast.error("Paste at least one key");
            setSaving(false);
            return;
        }
        try {
            await saveKeys(payload);
            const fresh = await getKeysStatus();
            setStatus(fresh);
            onSaved?.();
            toast.success("Keys locked in");
            setValues({ groq: "", cerebras: "" });
        } catch (e) {
            toast.error(apiErrorMessage(e, "Save failed"));
        } finally {
            setSaving(false);
        }
    };

    const handleTest = async () => {
        setTesting(true);
        setTestResults(null);
        try {
            const r = await testKeys();
            setTestResults(r);
            const allOk = Object.values(r).every((v) => !v || v.ok);
            if (allOk) toast.success("All configured keys work!");
            else toast.error("Some keys failed — check details below");
        } catch (e) {
            toast.error(apiErrorMessage(e, "Connection test failed"));
        } finally {
            setTesting(false);
        }
    };

    return (
        <div
            className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4"
            onClick={onClose}
            data-testid="settings-modal-backdrop"
            role="presentation"
        >
            <div
                className="w-full max-w-2xl bg-[#111] border border-white/15 relative"
                onClick={(e) => e.stopPropagation()}
                data-testid="settings-modal"
                role="dialog"
                aria-modal="true"
                aria-labelledby="settings-title"
            >
                <button
                    onClick={onClose}
                    className="absolute top-4 right-4 text-white/50 hover:text-white transition"
                    data-testid="close-settings-btn"
                    aria-label="Close API key settings"
                >
                    <X className="w-5 h-5" />
                </button>

                <div className="p-8 border-b border-white/10">
                    <div className="flex items-center gap-3 mb-2">
                        <Key className="w-6 h-6 text-[#ccff00]" />
                        <h2 id="settings-title" className="font-heading text-3xl tracking-wider">
                            API KEYS
                        </h2>
                    </div>
                    <p className="text-white/60 text-sm">
                        Paste your free-tier keys — we encrypt them before storing.
                        Groq is required and Cerebras provides the fallback model.
                    </p>
                </div>

                <div className="p-5 sm:p-8 space-y-8 max-h-[60vh] overflow-y-auto">
                    {PROVIDERS.map((p) => {
                        const configured = status[p.id];
                        const testResult = testResults?.[p.id];
                        return (
                            <div key={p.id} data-testid={`key-row-${p.id}`}>
                                <div className="flex items-baseline justify-between mb-2">
                                    <div className="flex items-center gap-3">
                                        <span className="font-heading text-xl tracking-wider">
                                            {p.name}
                                        </span>
                                        {p.required && (
                                            <span className="text-[10px] font-mono uppercase tracking-widest bg-[#ff0050] text-white px-1.5 py-0.5">
                                                Required
                                            </span>
                                        )}
                                        {configured ? (
                                            <span className="text-[10px] font-mono uppercase tracking-widest text-[#ccff00] flex items-center gap-1">
                                                <CheckCircle2 className="w-3 h-3" /> Saved
                                            </span>
                                        ) : (
                                            <span className="text-[10px] font-mono uppercase tracking-widest text-white/40">
                                                Not set
                                            </span>
                                        )}
                                    </div>
                                    <a
                                        href={p.url}
                                        target="_blank"
                                        rel="noreferrer"
                                        className="text-[11px] font-mono text-white/60 hover:text-[#ccff00] flex items-center gap-1"
                                    >
                                        Get key <ExternalLink className="w-3 h-3" />
                                    </a>
                                </div>
                                <div className="text-xs text-white/50 mb-3">{p.role}</div>
                                <input
                                    type="password"
                                    className="input-brutal"
                                    placeholder={p.placeholder}
                                    value={values[p.id]}
                                    onChange={(e) =>
                                        setValues((v) => ({ ...v, [p.id]: e.target.value }))
                                    }
                                    data-testid={`key-input-${p.id}`}
                                    aria-label={`${p.name} API key`}
                                    autoComplete="off"
                                />
                                {testResult && (
                                    <div
                                        className={`mt-2 text-xs font-mono ${
                                            testResult.ok
                                                ? "text-[#ccff00]"
                                                : "text-[#ff3333]"
                                        }`}
                                        data-testid={`test-result-${p.id}`}
                                    >
                                        {testResult.ok
                                            ? `✓ Connected${testResult.model ? ` · ${testResult.model}` : ""}`
                                            : `✗ ${testResult.error}`}
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>

                <div className="p-4 sm:p-6 border-t border-white/10 flex flex-col-reverse sm:flex-row items-stretch sm:items-center justify-between gap-3">
                    <button
                        onClick={handleTest}
                        disabled={testing}
                        className="btn-ghost"
                        data-testid="test-keys-btn"
                    >
                        {testing ? (
                            <Loader2 className="w-4 h-4 animate-spin" />
                        ) : (
                            <CheckCircle2 className="w-4 h-4" />
                        )}
                        Test connections
                    </button>
                    <div className="flex gap-3">
                        <button onClick={onClose} className="btn-ghost">
                            Close
                        </button>
                        <button
                            onClick={handleSave}
                            disabled={saving}
                            className="btn-brand"
                            data-testid="save-keys-btn"
                        >
                            {saving ? (
                                <Loader2 className="w-4 h-4 animate-spin" />
                            ) : null}
                            Save keys
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
}
