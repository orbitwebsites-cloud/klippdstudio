import { API } from "@/lib/klipApi";
import { FolderOpen, Server, Settings as SettingsIcon } from "lucide-react";

export default function Settings() {
    return (
        <main className="min-h-[calc(100vh-72px)] px-4 sm:px-6 md:px-16 py-8 md:py-12">
            <div className="max-w-5xl mx-auto">
                <div className="font-mono text-xs tracking-widest text-[#ccff00] mb-4">WORKSPACE CONTROL</div>
                <h1 className="font-heading text-5xl md:text-7xl leading-none tracking-wider">SETTINGS</h1>
                <div className="grid md:grid-cols-2 gap-4 mt-8">
                    <section className="panel p-6">
                        <Server className="w-6 h-6 text-[#ccff00]" />
                        <h2 className="font-heading text-3xl mt-4 tracking-wider">SERVER STATUS</h2>
                        <p className="text-white/55 text-sm leading-6 mt-3">
                            Klipped connects to a backend service for uploads, analysis, rendering, billing, and asset storage.
                        </p>
                        <div className="mt-4 border border-white/10 bg-black px-3 py-3 font-mono text-xs text-white/70 break-all">
                            {API}
                        </div>
                    </section>
                    <section className="panel p-6">
                        <FolderOpen className="w-6 h-6 text-[#ccff00]" />
                        <h2 className="font-heading text-3xl mt-4 tracking-wider">WORKSPACE MODE</h2>
                        <p className="text-white/55 text-sm leading-6 mt-3">
                            This beta workspace is optimized for one editing team. Multi-user account isolation is not enabled in this build.
                        </p>
                    </section>
                    <section className="panel p-6 md:col-span-2">
                        <SettingsIcon className="w-6 h-6 text-[#ccff00]" />
                        <h2 className="font-heading text-3xl mt-4 tracking-wider">WORKSPACE FEATURES</h2>
                        <p className="text-white/55 text-sm leading-6 mt-3">
                            Projects, Library, Training Lab, Billing, and Settings are available from the main navigation on desktop and mobile.
                        </p>
                    </section>
                </div>
            </div>
        </main>
    );
}
