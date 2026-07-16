import { lazy, Suspense, useEffect, useState } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Toaster } from "sonner";
import { Show, SignInButton, SignUpButton, useAuth } from "@clerk/react";
import TopBar from "@/components/TopBar";
import Landing from "@/pages/Landing";
import { getHealth } from "@/lib/klipApi";
import { setAuthTokenProvider } from "@/lib/klipApi";
import "@/App.css";

const Editor = lazy(() => import("@/pages/Editor"));
const TrainingLab = lazy(() => import("@/pages/TrainingLab"));
const Pricing = lazy(() => import("@/pages/Pricing"));
const Library = lazy(() => import("@/pages/Library"));
const Settings = lazy(() => import("@/pages/Settings"));

function RouteLoading() {
    return <div className="min-h-[calc(100vh-72px)]" aria-busy="true" />;
}

function AuthenticatedApp({ backendOnline }) {
    return (
        <>
            <TopBar backendOnline={backendOnline} authEnabled />
            <Routes>
                <Route path="/" element={<Landing backendOnline={backendOnline} />} />
                <Route path="/project/:id" element={<Suspense fallback={<RouteLoading />}><Editor /></Suspense>} />
                <Route path="/training" element={<Suspense fallback={<RouteLoading />}><TrainingLab /></Suspense>} />
                <Route path="/library" element={<Suspense fallback={<RouteLoading />}><Library /></Suspense>} />
                <Route path="/pricing" element={<Suspense fallback={<RouteLoading />}><Pricing /></Suspense>} />
                <Route path="/settings" element={<Suspense fallback={<RouteLoading />}><Settings /></Suspense>} />
                <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
        </>
    );
}

function ClerkSessionBridge() {
    const { getToken } = useAuth();

    useEffect(() => {
        setAuthTokenProvider(getToken);
        return () => setAuthTokenProvider(null);
    }, [getToken]);

    return null;
}

function App({ authEnabled = false }) {
    const [backendOnline, setBackendOnline] = useState(null);

    const refreshBackend = () => {
        getHealth()
            .then(() => setBackendOnline(true))
            .catch(() => setBackendOnline(false));
    };

    useEffect(() => { refreshBackend(); }, []);

    return (
        <BrowserRouter>
            {authEnabled ? (
                <>
                    <ClerkSessionBridge />
                    <Show when="signed-in"><AuthenticatedApp backendOnline={backendOnline} /></Show>
                    <Show when="signed-out">
                        <TopBar backendOnline={backendOnline} authEnabled />
                        <main className="min-h-[calc(100vh-72px)] flex items-center justify-center px-6">
                            <section className="max-w-xl border border-white/10 bg-black p-8 text-center">
                                <div className="font-mono text-xs tracking-widest text-[#ccff00]">KLIPPED STUDIO / PRIVATE WORKSPACE</div>
                                <h1 className="mt-4 font-heading text-5xl tracking-wider">SIGN IN TO EDIT.</h1>
                                <p className="mt-4 text-white/55">Create an account or sign in to access your projects and editing workspace.</p>
                                <div className="mt-7 flex justify-center gap-3">
                                    <SignInButton mode="modal"><button type="button" className="btn-ghost min-h-11 px-5">SIGN IN</button></SignInButton>
                                    <SignUpButton mode="modal"><button type="button" className="btn-brand min-h-11 px-5">CREATE ACCOUNT</button></SignUpButton>
                                </div>
                            </section>
                        </main>
                    </Show>
                </>
            ) : (
                <>
                    <TopBar backendOnline={backendOnline} />
                    <Routes><Route path="*" element={<Landing backendOnline={backendOnline} />} /></Routes>
                </>
            )}
            <Toaster
                theme="dark"
                position="bottom-right"
                toastOptions={{
                    style: {
                        background: "#0a0a0a",
                        border: "1px solid rgba(255,255,255,0.15)",
                        color: "#fff",
                        borderRadius: 0,
                        fontFamily: "'Outfit', sans-serif",
                    },
                }}
            />
        </BrowserRouter>
    );
}

export default App;
