import { Link } from "react-router-dom";
import { Telescope, Github } from "lucide-react";

export default function Header() {
  return (
    <header className="border-b border-white/10 bg-cosmos-950/80 backdrop-blur-md sticky top-0 z-50">
      <div className="max-w-[1600px] mx-auto px-4 h-14 flex items-center justify-between">
        <Link to="/" className="flex items-center gap-2.5 hover:opacity-80 transition-opacity">
          <Telescope className="w-5 h-5 text-cosmos-400" />
          <span className="font-semibold text-lg tracking-tight">
            Rubin Scout
          </span>
          <span className="text-[10px] font-mono bg-cosmos-800/60 text-cosmos-300 px-1.5 py-0.5 rounded">
            v0.1
          </span>
        </Link>

        <nav className="flex items-center gap-6 text-sm text-white/60">
          <Link to="/" className="hover:text-white transition-colors">
            Dashboard
          </Link>
          <Link to="/gravitational-waves" className="hover:text-white transition-colors">
            GW Events
          </Link>
          <a
            href="http://localhost:8000/docs"
            target="_blank"
            rel="noopener"
            className="hover:text-white transition-colors"
          >
            API Docs
          </a>
          <a
            href="https://github.com/Namrata-Modha/rubin-scout"
            target="_blank"
            rel="noopener"
            className="hover:text-white transition-colors"
          >
            <Github className="w-4 h-4" />
          </a>
        </nav>
      </div>
    </header>
  );
}
