"use client";

import { Fragment, ReactNode } from "react";
import { Copy, ExternalLink, FileText, List, MapPin, Phone, Play, Reply } from "lucide-react";
import { useT } from "@/lib/i18n";

// A variable as Meta writes it: a name in lowercase letters and underscores,
// or a number in the older form.
export const TEMPLATE_VARIABLE = /\{\{([a-z_]+|[0-9]+)\}\}/g;

/** The variables of a text, each once, in order of first appearance. */
export function templateParameters(text: string): string[] {
  const seen: string[] = [];
  for (const match of (text || "").matchAll(TEMPLATE_VARIABLE)) if (!seen.includes(match[1])) seen.push(match[1]);
  return seen;
}

// WhatsApp's own inline formatting: *bold*, _italic_, ~strike~ and ```mono```.
const INLINE = /(```[^`]+```|\*[^*\n]+\*|_[^_\n]+_|~[^~\n]+~)/g;

function inline(text: string, keyPrefix: string): ReactNode[] {
  return text.split(INLINE).filter(Boolean).map((part, index) => {
    const key = `${keyPrefix}-${index}`;
    if (part.startsWith("```") && part.endsWith("```") && part.length > 6) return <code key={key}>{part.slice(3, -3)}</code>;
    if (part.startsWith("*") && part.endsWith("*") && part.length > 2) return <strong key={key}>{part.slice(1, -1)}</strong>;
    if (part.startsWith("_") && part.endsWith("_") && part.length > 2) return <em key={key}>{part.slice(1, -1)}</em>;
    if (part.startsWith("~") && part.endsWith("~") && part.length > 2) return <s key={key}>{part.slice(1, -1)}</s>;
    return <Fragment key={key}>{part}</Fragment>;
  });
}

/** WhatsApp formatting plus variables: a filled one shows its value, an
 * unfilled one stays as a chip so it is obvious what is still missing. */
export function TemplateText({ text, values }: { text: string; values: Record<string, string> }) {
  // Filled values go in before formatting, so *{{price}}* still reads bold.
  const filled = (text || "").replace(TEMPLATE_VARIABLE, (whole, name: string) => values[name] || whole);
  const lines = filled.split("\n");
  return <>{lines.map((line, row) => <Fragment key={row}>
    {row > 0 && <br />}
    {line.split(/(\{\{(?:[a-z_]+|[0-9]+)\}\})/g).filter(Boolean).map((piece, index) => {
      const variable = piece.match(/^\{\{([a-z_]+|[0-9]+)\}\}$/);
      if (!variable) return <Fragment key={index}>{inline(piece, `${row}-${index}`)}</Fragment>;
      const value = values[variable[1]];
      return value ? <Fragment key={index}>{value}</Fragment> : <span key={index} className="wa-variable">{piece}</span>;
    })}
  </Fragment>)}</>;
}

export type PreviewHeader =
  | { format: "NONE" }
  | { format: "TEXT"; text: string }
  | { format: "IMAGE" | "VIDEO" | "DOCUMENT"; url?: string; name?: string }
  | { format: "LOCATION"; name?: string; address?: string };

export type PreviewButton = { type: string; text: string };

const BUTTON_ICONS: Record<string, ReactNode> = {
  QUICK_REPLY: <Reply size={15} />,
  URL: <ExternalLink size={15} />,
  PHONE_NUMBER: <Phone size={15} />,
  COPY_CODE: <Copy size={15} />,
};

/** The message as WhatsApp shows it on a phone: header, body, footer, time
 * and buttons. Values fill the variables; missing ones stay visible. */
export function WhatsAppPreview({ header, body, footer, buttons, values = {}, empty }: {
  header: PreviewHeader;
  body: string;
  footer?: string;
  buttons?: PreviewButton[];
  values?: Record<string, string>;
  /** Shown instead of the bubble while there is nothing to preview yet. */
  empty?: string;
}) {
  const t = useT();
  const time = new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const all = (buttons || []).filter((b) => b.text || b.type === "COPY_CODE");
  // Past three buttons WhatsApp folds the rest behind a list.
  const shown = all.length > 3 ? all.slice(0, 2) : all;
  const label = (b: PreviewButton) => b.type === "COPY_CODE" ? t("portal.templates.preview.copyCode") : b.text;
  const blank = !body.trim() && header.format === "NONE";
  return <div className="wa-preview">
    {blank && empty ? <div className="wa-empty">{empty}</div> : <div className="wa-bubble">
      {header.format === "TEXT" && header.text && <div className="wa-header"><TemplateText text={header.text} values={values} /></div>}
      {header.format === "IMAGE" && (header.url ? <img className="wa-media" src={header.url} alt="" /> : <div className="wa-media wa-media-blank"><ImagePlaceholder /></div>)}
      {header.format === "VIDEO" && (header.url ? <video className="wa-media" src={header.url} muted playsInline /> : <div className="wa-media wa-media-blank"><Play size={30} /></div>)}
      {header.format === "DOCUMENT" && <div className="wa-document"><FileText size={22} /><span>{header.name || t("portal.templates.preview.document")}</span></div>}
      {header.format === "LOCATION" && <div className="wa-location"><div className="wa-map"><MapPin size={22} /></div><div><strong>{header.name || t("portal.templates.preview.location")}</strong><small>{header.address || t("portal.templates.preview.locationHint")}</small></div></div>}
      <div className="wa-body"><TemplateText text={body} values={values} /></div>
      {footer && <div className="wa-footer">{footer}</div>}
      <div className="wa-time">{time}</div>
    </div>}
    {!blank && shown.length > 0 && <div className="wa-buttons">
      {shown.map((b, i) => <div key={i} className="wa-button">{BUTTON_ICONS[b.type]}<span>{label(b)}</span></div>)}
      {all.length > 3 && <div className="wa-button"><List size={15} /><span>{t("portal.templates.preview.seeAll")}</span></div>}
    </div>}
  </div>;
}

function ImagePlaceholder() {
  return <svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden><rect x="3" y="3" width="18" height="18" rx="3" /><circle cx="8.5" cy="8.5" r="1.5" /><path d="m21 15-5-5L5 21" /></svg>;
}
