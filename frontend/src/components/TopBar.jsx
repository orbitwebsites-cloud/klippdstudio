import { Link } from "react-router-dom";
import { useState } from "react";
import { BrainCircuit, CreditCard, FolderOpen, Menu, Settings, Sparkles, WifiOff, X } from "lucide-react";
import { SignInButton, SignUpButton, Show, UserButton } from "@clerk/react";

const NAV_ITEMS = [
    { to: "/library", label: "Library", icon: FolderOpen },
    { to: "/training", label: "Training Lab", icon: BrainCircuit },
    { to: "/pricing", label: "Pricing", icon: CreditCard },
    { to: "/settings", label: "Settings", icon: Settings },
];

export default function TopBar({ backendOnline, authEnabled = false }) {
    const [open, setOpen] = useState(false);

    return (
        <header
            className="w-full border-b border-white/10 bg-[#050505] sticky top-0 z-40"
            data-testid="top-bar"
        >
            <div className="flex items-center justify-between px-4 sm:px-6 py-3 sm:py-4 gap-3">
                <Link
                    to="/"
                    className="flex items-center gap-3 group"
                    data-testid="brand-link"
                >
                    <div className="w-9 h-9 bg-[#ccff00] flex items-center justify-center">
                        <Sparkles className="w-5 h-5 text-black" strokeWidth={3} />
                    </div>
                    <div>
                        <div className="font-heading text-2xl leading-none tracking-wider">
                            KLIPPED
                        </div>
                        <div className="font-mono text-[10px] text-white/50 tracking-widest">
                            STUDIO / BETA
                        </div>
                    </div>
                </Link>
                <div className="flex items-center gap-4">
                    <nav className="hidden md:flex items-center gap-4" aria-label="Primary navigation">
                        {NAV_ITEMS.map((item) => {
                            const Icon = item.icon;
                            return (
                                <Link key={item.to} to={item.to} className="flex items-center gap-2 text-xs font-mono tracking-wider text-white/60 hover:text-[#ccff00]">
                                    <Icon className="w-4 h-4" /> {item.label.toUpperCase()}
                                </Link>
                            );
                        })}
                    </nav>
                    {backendOnline === false && (
                        <div className="hidden sm:flex items-center gap-1.5 text-[#ff5a5a] text-[10px] font-mono tracking-wider">
                            <WifiOff className="w-3.5 h-3.5" /> SERVER OFFLINE
                        </div>
                    )}
                    {authEnabled && <div className="hidden sm:flex items-center gap-2">
                        <Show when="signed-out">
                            <SignInButton mode="modal"><button type="button" className="btn-ghost !px-3 !py-2 text-[10px]">SIGN IN</button></SignInButton>
                            <SignUpButton mode="modal"><button type="button" className="btn-brand !px-3 !py-2 text-[10px]">SIGN UP</button></SignUpButton>
                        </Show>
                        <Show when="signed-in"><UserButton /></Show>
                    </div>}
                    <button
                        type="button"
                        className="md:hidden min-w-11 min-h-11 inline-flex items-center justify-center border border-white/10 text-white/70"
                        aria-label={open ? "Close navigation menu" : "Open navigation menu"}
                        aria-expanded={open}
                        onClick={() => setOpen((value) => !value)}
                    >
                        {open ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
                    </button>
                </div>
            </div>
            {open && (
                <nav className="md:hidden border-t border-white/10 px-4 pb-4 grid gap-2" aria-label="Mobile navigation">
                    {NAV_ITEMS.map((item) => {
                        const Icon = item.icon;
                        return (
                            <Link
                                key={item.to}
                                to={item.to}
                                onClick={() => setOpen(false)}
                                className="min-h-11 flex items-center gap-3 border border-white/10 bg-white/[0.03] px-3 py-2 font-mono text-xs tracking-wider text-white/75"
                            >
                                <Icon className="w-4 h-4 text-[#ccff00]" /> {item.label.toUpperCase()}
                            </Link>
                        );
                    })}
                    {backendOnline === false && (
                        <div className="min-h-11 flex items-center gap-3 border border-[#ff5a5a]/30 bg-[#ff5a5a]/10 px-3 py-2 text-[#ff8a8a] text-xs font-mono tracking-wider">
                            <WifiOff className="w-4 h-4" /> SERVER OFFLINE
                        </div>
                    )}
                </nav>
            )}
        </header>
    );
}
