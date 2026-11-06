import React from "react";
import { Routes, Route } from "react-router-dom";
import Sidebar from "./components/layout/Sidebar";
import Topbar from "./components/layout/Topbar";
import { Toaster } from "./components/ui/toaster";
import { TooltipProvider } from "./components/ui/tooltip";
import Dashboard from "./pages/Dashboard";
import Providers from "./pages/Providers";
import Strategies from "./pages/Strategies";
import Logs from "./pages/Logs";
import SettingsPage from "./pages/Settings";
import ForwardingPage from "./pages/Forwarding";
import Endpoints from "./pages/Endpoints";
import Chat from "./pages/Chat";

export default function App() {
  return (
    <TooltipProvider delayDuration={250}>
      <div className="flex h-screen overflow-hidden bg-background">
        <Sidebar />
        <div className="flex min-w-0 flex-1 flex-col">
          <Topbar />
          <main className="min-h-0 flex-1 overflow-y-auto p-6 lg:p-8">
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/chat" element={<Chat />} />
              <Route path="/providers" element={<Providers />} />
              <Route path="/strategies" element={<Strategies />} />
              <Route path="/logs" element={<Logs />} />
              <Route path="/endpoints" element={<Endpoints />} />
              <Route path="/forwarding" element={<ForwardingPage />} />
              <Route path="/settings" element={<SettingsPage />} />
            </Routes>
          </main>
        </div>
        <Toaster />
      </div>
    </TooltipProvider>
  );
}
