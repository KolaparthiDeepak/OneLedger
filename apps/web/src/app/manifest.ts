import type { MetadataRoute } from "next";

// Installable on phones and desktops ("Add to Home screen"). No service worker: financial pages and
// data are never cached on the device, so the installed app always shows the live ledger.
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "OneLedger",
    short_name: "OneLedger",
    description: "Your private ledger for every account, card, loan and investment.",
    start_url: "/",
    scope: "/",
    display: "standalone",
    orientation: "portrait",
    background_color: "#f3f4ef",
    theme_color: "#15213a",
    categories: ["finance", "productivity"],
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
      { src: "/icons/maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
    shortcuts: [
      { name: "Add a transaction", url: "/transactions?add=1" },
      { name: "Import a statement", url: "/imports" },
      { name: "Stats", url: "/stats" },
    ],
  };
}
