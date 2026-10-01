"use client";

import QRCode from "qrcode";
import { useEffect, useState } from "react";

/** The authenticator setup QR code, drawn in the browser so the secret is never sent anywhere else. */
export function TotpQr({ uri }: { uri: string }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    QRCode.toDataURL(uri, { margin: 1, width: 200, errorCorrectionLevel: "M", color: { dark: "#15213a", light: "#ffffff" } })
      .then((url) => { if (live) setSrc(url); })
      .catch(() => { if (live) setSrc(null); });
    return () => { live = false; };
  }, [uri]);
  if (!src) return null;
  // eslint-disable-next-line @next/next/no-img-element -- a data: URL generated on this page
  return <img src={src} width={200} height={200} alt="QR code to add OneLedger to your authenticator app" className="rounded-lg border border-rule bg-white p-1" />;
}
