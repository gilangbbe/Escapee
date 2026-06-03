import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server on :5173. The backend (FastAPI) runs on :8000; the WebSocket URL
// is configured in the UI and defaults to ws://localhost:8000/ws/game.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
  },
});
