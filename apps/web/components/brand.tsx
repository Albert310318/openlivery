"use client";

import { useState } from "react";

type BrandLogoProps = {
  className?: string;
  variant?: "full" | "compact";
};

/**
 * Uses the supplied AYV PNGs when they are present, with a text fallback for
 * local development and deployments that have not received the assets yet.
 */
export function BrandLogo({ className = "", variant = "full" }: BrandLogoProps) {
  const [hasImage, setHasImage] = useState(true);
  const imagePath = "/ayv-logo.png";

  return (
    <span className={`brand-logo brand-logo-${variant} ${className}`.trim()}>
      {hasImage ? (
        <img src={imagePath} alt="" aria-hidden="true" onError={() => setHasImage(false)} />
      ) : variant === "full" ? (
        <>
          <span className="brand-mark" aria-hidden="true">AYV</span>
          <span>Atiende y Vende</span>
        </>
      ) : (
        <span className="brand-mark" aria-hidden="true">AYV</span>
      )}
    </span>
  );
}
