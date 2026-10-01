import React from "react";
import ReactDOM from "react-dom/client";
import axios from "axios";
import App from "./App.jsx";
import { installBackendCredentials } from "./api/apiAuth";
// Inter, served from this origin — no request to Google Fonts on page load.
import "@fontsource/inter/300.css";
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/700.css";
import "./index.css"; // Basic global styles

// Before the first render: axios refusals become Settings → API key advice,
// and, only when the build names a backend on another origin, requests to it
// carry this browser's sign-in cookie.
installBackendCredentials({ axios });

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
