import React from "react";
import ReactDOM from "react-dom/client";
import axios from "axios";
import App from "./App.jsx";
import { installApiKeyTransport } from "./api/apiKey";
// Inter, served from this origin — no request to Google Fonts on page load.
import "@fontsource/inter/300.css";
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/700.css";
import "./index.css"; // Basic global styles

// Before the first render, so the first request already carries this
// install's API key when the browser has one (Settings → API key).
installApiKeyTransport({ axios });

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
