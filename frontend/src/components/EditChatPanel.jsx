import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowUp, Bot, Check, CornerUpLeft, CornerUpRight, Loader2, Sparkles, X } from "lucide-react";
import { toast } from "sonner";
import {
    apiErrorMessage,
    applyEditChatPreview,
    getEditChatHistory,
    isFeatureUnavailable,
    previewEditChat,
    redoEditChat,
    undoEditChat,
} from "@/lib/klipApi";

const SUGGESTIONS = [
    "Make the first 10 seconds faster",
    "Tighten pauses and remove filler words",
    "Add more impact to the reveal",
    "Use cleaner captions with key words highlighted",
];

const historyMessages = (payload) => Array.isArray(payload) ? payload : payload?.messages || payload?.history || [];
const operationList = (preview) => preview?.operations || preview?.diff?.operations || preview?.changes || [];
const operationText = (operation) => {
    if (typeof operation === "string") return operation;
    const action = operation?.summary || operation?.description || operation?.action || operation?.type || "Edit";
    const range = operation?.time_range || operation?.range || operation?.timestamp;
    return range ? `${action} · ${range}` : action;
};

export default function EditChatPanel({ projectId, creatorProfileId, onApplied }) {
    const [available, setAvailable] = useState(true);
    const [loading, setLoading] = useState(true);
    const [messages, setMessages] = useState([]);
    const [message, setMessage] = useState("");
    const [preview, setPreview] = useState(null);
    const [working, setWorking] = useState(false);
    const [historyWorking, setHistoryWorking] = useState(null);
    const [canUndo, setCanUndo] = useState(false);
    const [canRedo, setCanRedo] = useState(false);
    const [errorMessage, setErrorMessage] = useState("");

    const loadHistory = useCallback(async () => {
        try {
            const data = await getEditChatHistory(projectId);
            setMessages(historyMessages(data));
            setCanUndo(Boolean(data?.can_undo));
            setCanRedo(Boolean(data?.can_redo));
        } catch (error) {
            if (isFeatureUnavailable(error)) setAvailable(false);
            else toast.error(apiErrorMessage(error, "Edit history could not be loaded"));
        } finally {
            setLoading(false);
        }
    }, [projectId]);

    useEffect(() => { loadHistory(); }, [loadHistory]);

    const operations = useMemo(() => operationList(preview), [preview]);

    const requestPreview = async (requestedMessage = message) => {
        const clean = requestedMessage.trim();
        if (!clean || working) return;
        setErrorMessage("");
        setWorking(true);
        setMessages((current) => [...current, { id: `local-${Date.now()}`, role: "user", content: clean }]);
        setMessage("");
        try {
            const data = await previewEditChat(projectId, {
                message: clean,
                ...(creatorProfileId ? { creator_profile_id: creatorProfileId } : {}),
            });
            setPreview(data);
        } catch (error) {
            if (isFeatureUnavailable(error)) setAvailable(false);
            else {
                const detail = apiErrorMessage(error, "Klipped could not preview that edit");
                setErrorMessage(detail);
                toast.error(detail);
            }
        } finally {
            setWorking(false);
        }
    };

    const applyPreview = async () => {
        const previewId = preview?.preview_id || preview?.id;
        if (!previewId) {
            toast.error("This preview is missing its edit ID. Ask Klipped to preview it again.");
            return;
        }
        setWorking(true);
        setErrorMessage("");
        try {
            const result = await applyEditChatPreview(projectId, previewId);
            setPreview(null);
            setCanUndo(result?.can_undo ?? true);
            setCanRedo(Boolean(result?.can_redo));
            await Promise.all([loadHistory(), Promise.resolve(onApplied?.())]);
            toast.success("Edit applied. You can undo it anytime.");
        } catch (error) {
            if (isFeatureUnavailable(error)) setAvailable(false);
            else {
                const detail = apiErrorMessage(error, "The edit could not be applied");
                setErrorMessage(detail);
                toast.error(detail);
            }
        } finally {
            setWorking(false);
        }
    };

    const changeHistory = async (direction) => {
        setHistoryWorking(direction);
        setErrorMessage("");
        try {
            const result = direction === "undo" ? await undoEditChat(projectId) : await redoEditChat(projectId);
            setCanUndo(Boolean(result?.can_undo));
            setCanRedo(Boolean(result?.can_redo));
            await Promise.all([loadHistory(), Promise.resolve(onApplied?.())]);
            toast.success(direction === "undo" ? "Last edit undone" : "Edit restored");
        } catch (error) {
            if (isFeatureUnavailable(error)) setAvailable(false);
            else {
                const detail = apiErrorMessage(error, `Could not ${direction} that edit`);
                setErrorMessage(detail);
                toast.error(detail);
            }
        } finally {
            setHistoryWorking(null);
        }
    };

    if (!available) return null;

    return (
        <section className="panel" data-testid="edit-chat-panel">
            <div className="px-5 py-4 border-b border-white/10 flex items-start justify-between gap-3">
                <div>
                    <div className="font-mono text-[10px] text-[#ccff00] tracking-widest">// PREMIUM EDIT COPILOT</div>
                    <div className="font-display text-2xl tracking-wider flex items-center gap-2 mt-1">
                        <Bot className="w-5 h-5 text-[#ccff00]" /> TELL KLIPPED WHAT TO CHANGE
                    </div>
                    <div className="text-xs text-white/45 mt-1">Every command becomes reviewable timeline operations. Nothing changes until you apply it.</div>
                </div>
                <div className="flex gap-1">
                    <button type="button" className="btn-ghost !p-2" disabled={!canUndo || historyWorking} onClick={() => changeHistory("undo")} aria-label="Undo last edit" data-testid="edit-chat-undo">
                        {historyWorking === "undo" ? <Loader2 className="w-4 h-4 animate-spin" /> : <CornerUpLeft className="w-4 h-4" />}
                    </button>
                    <button type="button" className="btn-ghost !p-2" disabled={!canRedo || historyWorking} onClick={() => changeHistory("redo")} aria-label="Redo edit" data-testid="edit-chat-redo">
                        {historyWorking === "redo" ? <Loader2 className="w-4 h-4 animate-spin" /> : <CornerUpRight className="w-4 h-4" />}
                    </button>
                </div>
            </div>

            <div className="p-5">
                {loading ? (
                    <div className="flex items-center gap-2 py-6 justify-center text-xs font-mono text-white/40">
                        <Loader2 className="w-4 h-4 animate-spin" /> LOADING EDIT MEMORY
                    </div>
                ) : messages.length > 0 ? (
                    <div className="max-h-48 overflow-y-auto space-y-3 mb-4 pr-1" aria-live="polite">
                        {messages.slice(-8).map((item, index) => {
                            const role = item.role || item.author || "assistant";
                            return (
                                <div key={item.id || index} className={`flex ${role === "user" ? "justify-end" : "justify-start"}`}>
                                    <div className={`max-w-[86%] border px-3 py-2 text-sm ${role === "user" ? "border-[#ccff00]/40 bg-[#ccff00]/[0.06]" : "border-white/10 bg-white/[0.03]"}`}>
                                        {item.content || item.message || item.text}
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                ) : (
                    <div className="flex flex-wrap gap-2 mb-4">
                        {SUGGESTIONS.map((suggestion) => (
                            <button key={suggestion} type="button" onClick={() => requestPreview(suggestion)} className="style-pill !text-[10px] !py-2 text-left" disabled={working}>
                                <Sparkles className="w-3 h-3" /> {suggestion}
                            </button>
                        ))}
                    </div>
                )}

                {preview ? (
                    <div className="border border-[#ccff00]/50 bg-[#ccff00]/[0.03] mb-4" data-testid="edit-chat-preview">
                        <div className="px-4 py-3 border-b border-[#ccff00]/20 flex items-center justify-between gap-2">
                            <div>
                                <div className="font-mono text-[10px] text-[#ccff00] tracking-widest">PREVIEW · NOT APPLIED</div>
                                <div className="text-sm text-white/75 mt-1">{preview.summary || preview.message || `${operations.length} timeline operation${operations.length === 1 ? "" : "s"}`}</div>
                            </div>
                            <button type="button" onClick={() => setPreview(null)} className="text-white/40 hover:text-white" aria-label="Cancel preview"><X className="w-4 h-4" /></button>
                        </div>
                        <div className="px-4 py-3 space-y-2">
                            {operations.length > 0 ? operations.map((operation, index) => (
                                <div key={operation?.id || index} className="grid grid-cols-[24px_1fr] gap-2 text-xs text-white/65">
                                    <span className="font-mono text-[#ccff00]">{String(index + 1).padStart(2, "0")}</span>
                                    <span>{operationText(operation)}</span>
                                </div>
                            )) : <div className="text-xs text-white/50">Klipped prepared this change. Apply to update the timeline.</div>}
                        </div>
                        <div className="px-4 py-3 border-t border-white/10 flex justify-end gap-2">
                            <button type="button" className="btn-ghost" onClick={() => setPreview(null)} disabled={working} data-testid="edit-chat-cancel"><X className="w-4 h-4" /> Cancel</button>
                            <button type="button" className="btn-brand" onClick={applyPreview} disabled={working} data-testid="edit-chat-apply">
                                {working ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />} Apply changes
                            </button>
                        </div>
                    </div>
                ) : null}

                {errorMessage ? (
                    <div className="mb-4 border border-[#ff3333]/40 bg-[#ff3333]/[0.05] px-3 py-2 text-xs text-[#ff9999]" role="alert" data-testid="edit-chat-error">
                        {errorMessage}
                    </div>
                ) : null}

                <form className="flex gap-2" onSubmit={(event) => { event.preventDefault(); requestPreview(); }}>
                    <textarea
                        value={message}
                        onChange={(event) => setMessage(event.target.value)}
                        onKeyDown={(event) => {
                            if (event.key === "Enter" && !event.shiftKey) {
                                event.preventDefault();
                                requestPreview();
                            }
                        }}
                        rows={2}
                        placeholder={creatorProfileId ? "Describe the change · selected Creator DNA will guide it" : "Try: Make the first 10 seconds faster"}
                        className="min-w-0 flex-1 resize-none bg-black border border-white/20 px-3 py-2 text-sm text-white placeholder:text-white/30 focus:border-[#ccff00] outline-none"
                        data-testid="edit-chat-input"
                    />
                    <button type="submit" className="btn-brand !px-4" disabled={!message.trim() || working} aria-label="Preview edit" data-testid="edit-chat-send">
                        {working ? <Loader2 className="w-4 h-4 animate-spin" /> : <ArrowUp className="w-4 h-4" />}
                    </button>
                </form>
                <div className="font-mono text-[9px] text-white/30 mt-2 tracking-widest">ENTER TO PREVIEW · SHIFT+ENTER FOR NEW LINE · APPLY, CANCEL, OR UNDO ANY CHANGE</div>
            </div>
        </section>
    );
}
