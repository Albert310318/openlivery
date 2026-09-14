"use client";

// Reusable confirmation dialog. Replaces the browser's native
// confirm() for destructive actions so they match the rest of the app's
// modals, with a busy spinner and an inline error while the action runs.
// The parent mounts it only while a target is set, so each open is a fresh
// instance and its busy/error state does not leak across opens.

import { ReactNode, useState } from "react";
import { LoaderCircle } from "lucide-react";
import { Alert, Modal } from "@/components/ui";
import { messageFrom } from "@/lib/api";

export function ConfirmModal({
  title,
  message,
  confirmLabel,
  cancelLabel,
  confirmIcon,
  danger = true,
  onConfirm,
  onClose,
}: {
  title: string;
  message?: ReactNode;
  confirmLabel: string;
  cancelLabel: string;
  confirmIcon?: ReactNode;
  danger?: boolean;
  onConfirm: () => Promise<void> | void;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleConfirm() {
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
      onClose();
    } catch (err) {
      // Keep the modal open so the user can read the error and retry.
      setError(messageFrom(err));
      setBusy(false);
    }
  }

  return (
    <Modal open title={title} onClose={() => { if (!busy) onClose(); }}>
      <div className="modal-form">
        {message && <p className="modal-copy">{message}</p>}
        {error && <Alert>{error}</Alert>}
        <div className="modal-actions">
          <button type="button" className="button" onClick={onClose} disabled={busy}>{cancelLabel}</button>
          <button type="button" className={danger ? "button danger" : "button primary"} onClick={handleConfirm} disabled={busy}>
            {busy ? <LoaderCircle className="spin" size={16} /> : confirmIcon} {confirmLabel}
          </button>
        </div>
      </div>
    </Modal>
  );
}
