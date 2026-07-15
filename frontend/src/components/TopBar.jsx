import { Link } from "react-router-dom";
import { Sparkles, WifiOff, BrainCircuit } from "lucide-react";

export default function TopBar({ backendOnline }) {
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
                    <Link to="/training" className="hidden sm:flex items-center gap-2 text-xs font-mono tracking-wider text-white/60 hover:text-[#ccff00]">
                        <BrainCircuit className="w-4 h-4" /> TRAINING LAB
                    </Link>
                    <Link to="/pricing" className="hidden sm:flex text-xs font-mono tracking-wider text-white/60 hover:text-[#ccff00]">
                        PRICING
                    </Link>
                    {backendOnline === false && (
                        <div className="hidden sm:flex items-center gap-1.5 text-[#ff5a5a] text-[10px] font-mono tracking-wider">
                            <WifiOff className="w-3.5 h-3.5" /> SERVER OFFLINE
                        </div>
                    )}
                </div>
            </div>
        </header>
    );
}
