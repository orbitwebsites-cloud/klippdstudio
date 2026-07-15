import { lazy, Suspense, useEffect, useState } from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { Toaster } from "sonner";
import TopBar from "@/components/TopBar";
import Landing from "@/pages/Landing";
import { getHealth } from "@/lib/klipApi";
import "@/App.css";

const Editor = lazy(() => import("@/pages/Editor"));
const TrainingLab = lazy(() => import("@/pages/TrainingLab"));

function RouteLoading() {
    return <div className="min-h-[calc(100vh-72px)]" aria-busy="true" />;
}

function App() {
    const [backendOnline, setBackendOnline] = useState(null);

    const refreshBackend = () => {
        getHealth()
            .then(() => setBackendOnline(true))
            .catch(() => setBackendOnline(false));
    };

    useEffect(() => { refreshBackend(); }, []);

    return (
        <BrowserRouter>
            <TopBar backendOnline={backendOnline} />
            <Routes>
                <Route path="/" element={
                    <Landing backendOnline={backendOnline} />
                } />
                <Route path="/project/:id" element={
                    <Suspense fallback={<RouteLoading />}><Editor /></Suspense>
                } />
                <Route path="/training" element={
                    <Suspense fallback={<RouteLoading />}><TrainingLab /></Suspense>
                } />
            </Routes>
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
