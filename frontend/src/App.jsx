import { BrowserRouter, Routes, Route } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import AlertDetail from "./pages/AlertDetail";
import GravitationalWaves from "./pages/GravitationalWaves";
import Header from "./components/Header";

export default function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-cosmos-950">
        <Header />
        <main className="max-w-[1600px] mx-auto px-4 py-6">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/alert/:oid" element={<AlertDetail />} />
            <Route path="/gravitational-waves" element={<GravitationalWaves />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
