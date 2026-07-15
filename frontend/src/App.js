import { useEffect, useState } from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { Toaster } from "sonner";
import TopBar from "@/components/TopBar";
import SettingsModal from "@/components/SettingsModal";
import Landing from "@/pages/Landing";
import Editor from "@/pages/Editor";
import TrainingLab from "@/pages/TrainingLab";
import { getKeysStatus } from "@/lib/klipApi";
import "@/App.css";

function App() {
    const [settingsOpen, setSettingsOpen] = useState(false);
    const [keysStatus, setKeysStatus] = useState({
        groq: false, cerebras: false,
    });
    const [backendOnline, setBackendOnline] = useState(null);

    const refreshKeys = () => {
        getKeysStatus()
            .then((status) => {
                setKeysStatus(status);
                setBackendOnline(true);
            })
            .catch(() => setBackendOnline(false));
    };

    useEffect(() => { refreshKeys(); }, []);

    return (
        <BrowserRouter>
            <TopBar keysStatus={keysStatus} backendOnline={backendOnline} onOpenSettings={() => setSettingsOpen(true)} />
            <Routes>
                <Route path="/" element={
                    <Landing keysStatus={keysStatus} backendOnline={backendOnline} onOpenSettings={() => setSettingsOpen(true)} />
                } />
                <Route path="/project/:id" element={<Editor />} />
                <Route path="/training" element={<TrainingLab />} />
            </Routes>
            <SettingsModal
                open={settingsOpen}
                onClose={() => setSettingsOpen(false)}
                onSaved={refreshKeys}
            />
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
