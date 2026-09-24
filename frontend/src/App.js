import "@/App.css";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Shell } from "@/components/terminal/Shell";
import MarketsPage from "@/pages/MarketsPage";
import WalletClassifierPage from "@/pages/WalletClassifierPage";
import TunePage from "@/pages/TunePage";
import TapePage from "@/pages/TapePage";
import { Toaster } from "@/components/ui/sonner";

function App() {
  return (
    <div className="App dark">
      <BrowserRouter>
        <Routes>
          <Route element={<Shell />}>
            <Route path="/" element={<MarketsPage />} />
            <Route path="/tape" element={<TapePage />} />
            <Route path="/leaderboard" element={<Navigate to="/" replace />} />
            <Route path="/wallet" element={<WalletClassifierPage />} />
            <Route path="/wallet/:address" element={<WalletClassifierPage />} />
            <Route path="/tune" element={<TunePage />} />
          </Route>
        </Routes>
      </BrowserRouter>
      <Toaster theme="dark" position="bottom-right" />
    </div>
  );
}

export default App;
