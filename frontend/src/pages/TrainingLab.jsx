import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { BrainCircuit, CheckCircle2, ClipboardCheck, ExternalLink, Gamepad2, Loader2, Plus, RefreshCw, ShieldCheck } from "lucide-react";
import {
    activateTrainingProfile, apiErrorMessage, createTrainingProfile,
    createTrainingReference, getTrainingDashboard,
} from "@/lib/klipApi";

const emptyReference = { title: "", source_url: "", niche: "gaming", game: "Minecraft", rights_status: "research_only", notes: "", principles: "" };
const emptyProfile = { name: "Minecraft Narrative", niche: "gaming", game: "Minecraft", base_profile: "minecraft_narrative", principles: "" };

const lines = (value) => value.split("\n").map((x) => x.trim()).filter(Boolean);

export default function TrainingLab() {
    const [data, setData] = useState({ references: [], profiles: [], stats: {} });
    const [reference, setReference] = useState(emptyReference);
    const [profile, setProfile] = useState(emptyProfile);
    const [selectedRefs, setSelectedRefs] = useState([]);
    const [busyAction, setBusyAction] = useState("");
    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState("");

    const refresh = useCallback(async () => {
        try {
            setLoadError("");
            setData(await getTrainingDashboard());
        }
        catch (e) { setLoadError(apiErrorMessage(e, "Could not load Training Lab")); }
        finally { setLoading(false); }
    }, []);
    useEffect(() => { refresh(); }, [refresh]);

    const addReference = async (event) => {
        event.preventDefault(); setBusyAction("reference");
        try {
            await createTrainingReference({ ...reference, principles: lines(reference.principles) });
            setReference(emptyReference); await refresh(); toast.success("Reference saved for review");
        } catch (e) { toast.error(apiErrorMessage(e, "Could not save reference")); }
        finally { setBusyAction(""); }
    };
    const addProfile = async (event) => {
        event.preventDefault(); setBusyAction("profile");
        try {
            await createTrainingProfile({ ...profile, principles: lines(profile.principles), reference_ids: selectedRefs });
            setProfile(emptyProfile); setSelectedRefs([]); await refresh(); toast.success("Draft profile created");
        } catch (e) { toast.error(apiErrorMessage(e, "Could not create profile")); }
        finally { setBusyAction(""); }
    };
    const activate = async (id) => {
        setBusyAction(`activate-${id}`);
        try { await activateTrainingProfile(id); await refresh(); toast.success("Profile activated"); }
        catch (e) { toast.error(apiErrorMessage(e, "Could not activate profile")); }
        finally { setBusyAction(""); }
    };
    const selectProfile = (id) => {
        try { window.localStorage.setItem("klipped_active_training_profile", id); }
        catch { /* The editor can still be used without persistent selection. */ }
        toast.success("This profile will be used for your next edit");
    };
    const field = (value, setter, key, props = {}) => <input value={value[key]} onChange={(e) => setter({ ...value, [key]: e.target.value })} className="w-full bg-black border border-white/15 px-3 py-2 text-sm outline-none focus:border-[#ccff00]" {...props} />;

    const busy = Boolean(busyAction);

    return <main className="max-w-7xl mx-auto px-4 sm:px-6 py-8 pb-20">
        <section className="border border-[#ccff00]/50 bg-[#0a0a0a] p-6 sm:p-9 mb-8 relative overflow-hidden">
            <div className="absolute right-0 top-0 text-[#ccff00]/10 text-[160px] font-heading leading-none select-none">AI</div>
            <div className="relative max-w-3xl">
                <div className="flex items-center gap-2 text-[#ccff00] font-mono text-xs tracking-[0.2em]"><BrainCircuit className="w-4 h-4" /> TRAINING LAB</div>
                <h1 className="font-display text-5xl sm:text-7xl mt-3 tracking-wide">TEACH THE EDITOR<br />YOUR NICHE.</h1>
                <p className="text-white/65 max-w-2xl mt-4">Turn your best edits and properly usable references into approved editing principles. The AI applies the principles to new footage. It does not copy a creator, steal a timeline, or pretend it trained on public videos.</p>
                <div className="flex flex-wrap gap-3 mt-6 font-mono text-xs">
                    <Stat label="REFERENCES" value={data.stats.references || 0} />
                    <Stat label="ACTIVE PROFILES" value={data.stats.active_profiles || 0} />
                    <Stat label="APPROVED RULES" value={data.stats.approved_principles || 0} />
                </div>
            </div>
        </section>

        {loading && (
            <div className="panel p-8 mb-6 flex items-center gap-3 text-white/60 font-mono text-sm" aria-busy="true">
                <Loader2 className="w-4 h-4 animate-spin text-[#ccff00]" /> Loading Training Lab...
            </div>
        )}
        {loadError && (
            <div className="panel p-6 mb-6 border-[#ff5a5a]/40 bg-[#ff5a5a]/10" role="alert">
                <div className="font-heading text-3xl tracking-wider">TRAINING DATA DID NOT LOAD</div>
                <p className="mt-2 text-sm text-[#ffb3b3]">{loadError}</p>
                <button className="btn-ghost mt-4" onClick={() => { setLoading(true); refresh(); }}>
                    <RefreshCw className="w-4 h-4" /> Try again
                </button>
            </div>
        )}

        <div className="grid lg:grid-cols-2 gap-6 items-start">
            <section className="panel p-5 sm:p-6">
                <div className="flex gap-3 items-center mb-4"><Plus className="text-[#ccff00]" /><div><h2 className="font-display text-3xl">1. ADD A REFERENCE</h2><p className="text-white/45 text-xs">Save your observation, not someone else’s footage.</p></div></div>
                <form onSubmit={addReference} className="space-y-3">
                    {field(reference, setReference, "title", { placeholder: "Reference title", required: true })}
                    {field(reference, setReference, "source_url", { placeholder: "Optional source link (https://...)", type: "url" })}
                    <div className="grid grid-cols-2 gap-3">{field(reference, setReference, "niche", { placeholder: "Niche", required: true })}{field(reference, setReference, "game", { placeholder: "Game / format" })}</div>
                    <select value={reference.rights_status} onChange={(e) => setReference({ ...reference, rights_status: e.target.value })} className="w-full bg-black border border-white/15 px-3 py-2 text-sm">
                        <option value="research_only">Public reference - research only</option><option value="owned">I own this work</option><option value="licensed">I have a license</option>
                    </select>
                    <textarea value={reference.notes} onChange={(e) => setReference({ ...reference, notes: e.target.value })} required minLength="20" placeholder="What happens editorially? Example: the hook shows the final danger first, then quickly explains the challenge." className="w-full min-h-24 bg-black border border-white/15 px-3 py-2 text-sm outline-none focus:border-[#ccff00]" />
                    <textarea value={reference.principles} onChange={(e) => setReference({ ...reference, principles: e.target.value })} placeholder="Optional reusable rules, one per line" className="w-full min-h-20 bg-black border border-white/15 px-3 py-2 text-sm outline-none focus:border-[#ccff00]" />
                    <button disabled={busy} className="btn-brand w-full justify-center">{busyAction === "reference" ? <Loader2 className="w-4 h-4 animate-spin" /> : <ClipboardCheck className="w-4 h-4" />} SAVE REFERENCE</button>
                </form>
            </section>

            <section className="panel p-5 sm:p-6">
                <div className="flex gap-3 items-center mb-4"><Gamepad2 className="text-[#ccff00]" /><div><h2 className="font-display text-3xl">2. BUILD A PROFILE</h2><p className="text-white/45 text-xs">Three or more rules can be activated for future edits.</p></div></div>
                <form onSubmit={addProfile} className="space-y-3">
                    {field(profile, setProfile, "name", { placeholder: "Profile name", required: true })}
                    <div className="grid grid-cols-2 gap-3">{field(profile, setProfile, "niche", { placeholder: "Niche", required: true })}{field(profile, setProfile, "game", { placeholder: "Game / format" })}</div>
                    <select value={profile.base_profile} onChange={(e) => setProfile({ ...profile, base_profile: e.target.value })} className="w-full bg-black border border-white/15 px-3 py-2 text-sm"><option value="minecraft_narrative">Minecraft narrative</option><option value="gaming">General gaming</option><option value="talking_head">Talking head</option><option value="general">General</option></select>
                    <div className="border border-white/10 p-3 max-h-40 overflow-y-auto">
                        <div className="font-mono text-[10px] text-white/45 mb-2 tracking-widest">REFERENCE EVIDENCE (OPTIONAL)</div>
                        {data.references.length ? data.references.map((ref) => <label className="flex gap-2 text-sm py-1 cursor-pointer" key={ref.id}><input type="checkbox" checked={selectedRefs.includes(ref.id)} onChange={() => setSelectedRefs((old) => old.includes(ref.id) ? old.filter((id) => id !== ref.id) : [...old, ref.id])} />{ref.title}</label>) : <span className="text-white/35 text-sm">Add a reference first, or write your own principles below.</span>}
                    </div>
                    <textarea value={profile.principles} onChange={(e) => setProfile({ ...profile, principles: e.target.value })} required placeholder="Approved editing principles, one per line&#10;Open on a real stake within the first 3 seconds.&#10;Show inventory or health changes as proof.&#10;Let the payoff breathe for at least one readable beat." className="w-full min-h-40 bg-black border border-white/15 px-3 py-2 text-sm outline-none focus:border-[#ccff00]" />
                    <button disabled={busy} className="btn-brand w-full justify-center">{busyAction === "profile" ? <Loader2 className="w-4 h-4 animate-spin" /> : <BrainCircuit className="w-4 h-4" />} CREATE DRAFT PROFILE</button>
                </form>
            </section>
        </div>

        <section className="mt-6 panel p-5 sm:p-6"><div className="flex gap-3 items-center"><ShieldCheck className="text-[#ccff00]" /><div><h2 className="font-display text-3xl">3. ACTIVATE FOR EDITS</h2><p className="text-white/45 text-xs">Active profiles are available on the next upload.</p></div></div>
            <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-4 mt-5">{data.profiles.map((item) => <article className="border border-white/15 bg-black p-4" key={item.id}><div className="flex justify-between gap-3"><h3 className="font-display text-2xl">{item.name}</h3><span className={item.status === "active" ? "text-[#ccff00] font-mono text-[10px]" : "text-white/40 font-mono text-[10px]"}>{item.status.toUpperCase()}</span></div><p className="font-mono text-[10px] text-white/45 mt-1">{item.game || item.niche} · {item.reference_count || 0} refs · {item.principles.length} rules</p><ul className="text-sm text-white/65 mt-3 space-y-1">{item.principles.slice(0, 3).map((rule) => <li key={rule}>• {rule}</li>)}</ul><div className="flex gap-2 mt-4">{item.status !== "active" && <button disabled={busy} onClick={() => activate(item.id)} className="btn-ghost text-xs" data-testid={`activate-profile-${item.id}`}><CheckCircle2 className="w-4 h-4" /> ACTIVATE</button>}{item.status === "active" && <button onClick={() => selectProfile(item.id)} className="btn-brand text-xs" data-testid={`use-profile-${item.id}`}>USE NEXT EDIT</button>}</div></article>)}</div>
            {!data.profiles.length && <p className="text-white/40 mt-4">No profiles yet. Build the first one around Minecraft narrative edits.</p>}
        </section>
        <p className="mt-6 text-xs text-white/35 flex gap-2"><ExternalLink className="w-3.5 h-3.5 flex-none" />{data.policy}</p>
    </main>;
}

function Stat({ label, value }) { return <div className="border border-white/20 bg-black/40 px-3 py-2"><span className="text-[#ccff00] font-heading text-xl">{value}</span><span className="ml-2 text-white/50 tracking-widest">{label}</span></div>; }
