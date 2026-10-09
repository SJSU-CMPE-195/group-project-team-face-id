import React, { useRef, useState } from "react";
import {
  Bluetooth,
  CheckCircle2,
  Download,
  FileJson,
  Monitor,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import Btn from "../components/Btn.jsx";
import Card from "../components/Card.jsx";
import Input from "../components/Input.jsx";
import usePcClient from "./usePcClient.js";

const PIN_PATTERN = /^\d{6}$/;
const MAX_CARD_FILE_BYTES = 1024 * 1024;

function thisPcUrl() {
  const url = new URL("/", window.location.origin);
  url.protocol = "http:";
  url.port = "5057";
  return url.toString();
}

function defaultClientName() {
  const platform = navigator.userAgentData?.platform || navigator.platform || "PC";
  return `BASS ${platform} client`.slice(0, 100);
}

function recoveryFileName(disposition) {
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition)?.[1];
  if (encoded) {
    try {
      return decodeURIComponent(encoded);
    } catch {
      return "bass-recovery.json";
    }
  }
  const quoted = /filename="([^"]+)"/i.exec(disposition)?.[1];
  return quoted || "bass-recovery.json";
}

function saveBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

export default function PcClientGate({ children }) {
  const client = usePcClient();
  const phase = client.status?.phase;

  if (phase === "ready" && client.status?.connected) {
    if (client.status.recovery_download_available) {
      return <RecoveryCard client={client} />;
    }
    return children;
  }

  return (
    <ClientPage>
      {client.loading ? (
        <StatusCard message="Checking PC Bluetooth support…" />
      ) : !client.status ? (
        <UnavailableCard client={client} />
      ) : phase === "dependency_missing" ? (
        <DependencyCard client={client} />
      ) : phase === "needs_card" ? (
        <CardImport client={client} />
      ) : ["needs_scan", "needs_connection", "ready_disconnected"].includes(
          phase,
        ) || (phase === "commission_retry" && !client.status.connected) ? (
        <DeviceScan client={client} />
      ) : phase === "needs_commission" ? (
        <CommissionCard client={client} />
      ) : phase === "commission_retry" ? (
        <CommissionRetryCard client={client} />
      ) : (
        <UnavailableCard client={client} />
      )}
    </ClientPage>
  );
}

function ClientPage({ children }) {
  return (
    <div className="app-shell flex min-h-screen items-center justify-center overflow-y-auto px-4 py-8 text-slate-100">
      <div className="w-full max-w-xl">
        <div className="mb-5 flex items-center gap-3 px-1">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-violet-600 to-fuchsia-600 shadow-lg shadow-violet-900/35">
            <ShieldCheck className="h-5 w-5 text-white" />
          </div>
          <div>
            <h1 className="text-lg font-semibold">BASS PC client</h1>
            <p className="text-sm text-slate-400">
              Connect this browser to a Pi through the local Bluetooth gateway.
            </p>
          </div>
        </div>
        {children}
      </div>
    </div>
  );
}

function StatusCard({ message }) {
  return (
    <Card>
      <div className="flex items-center gap-3 text-sm text-slate-300" role="status">
        <RefreshCw className="h-4 w-4 animate-spin text-violet-300" />
        {message}
      </div>
    </Card>
  );
}

function UnavailableCard({ client }) {
  return (
    <Card>
      <h2 className="font-semibold text-slate-100">PC Bluetooth service unavailable</h2>
      <ClientError client={client} fallback="Could not reach the local PC Bluetooth service." />
      <Btn
        className="mt-5 w-full"
        disabled={client.busy}
        onClick={() => void client.refresh().catch(() => {})}
      >
        Retry
      </Btn>
      <ThisPcLink />
    </Card>
  );
}

function DependencyCard({ client }) {
  const message =
    client.status?.dependency?.error ||
    "Bluetooth support is unavailable. Install the PC BLE requirements and restart BASS.";
  return (
    <Card>
      <div className="flex items-center gap-2">
        <Bluetooth className="h-5 w-5 text-amber-300" />
        <h2 className="font-semibold text-slate-100">Bluetooth setup required</h2>
      </div>
      <p className="mt-3 text-sm leading-relaxed text-amber-100">{message}</p>
      <Btn
        variant="secondary"
        className="mt-5 w-full"
        disabled={client.busy}
        onClick={() => void client.refresh().catch(() => {})}
      >
        Check again
      </Btn>
      <ThisPcLink />
    </Card>
  );
}

function CardImport({ client }) {
  const [cardText, setCardText] = useState("");
  const [fileName, setFileName] = useState("");
  const [localError, setLocalError] = useState("");
  const fileRef = useRef(null);

  async function loadFile(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    setLocalError("");
    client.clearError();
    if (file.size > MAX_CARD_FILE_BYTES) {
      setLocalError("The selected card file is too large.");
      event.target.value = "";
      return;
    }
    try {
      setCardText(await file.text());
      setFileName(file.name);
    } catch {
      setLocalError("The selected card file could not be read.");
    }
  }

  async function submit(event) {
    event.preventDefault();
    const card = cardText.trim();
    if (!card) {
      setLocalError("Paste a card or choose a card export file.");
      return;
    }
    setLocalError("");
    await client.stageCard(card).catch(() => {});
  }

  return (
    <Card>
      <div className="flex items-center gap-2">
        <FileJson className="h-5 w-5 text-violet-300" />
        <h2 className="font-semibold text-slate-100">Connect to a Pi</h2>
      </div>
      <p className="mt-2 text-sm leading-relaxed text-slate-400">
        Paste a current BASS activation, invitation, or recovery card as JSON, or
        import a newly exported JSON or HTML card file. A QR image by itself cannot
        be imported here.
      </p>
      <form className="mt-5 space-y-4" onSubmit={submit}>
        <div>
          <label htmlFor="pc-client-card" className="mb-1 block text-sm font-medium">
            Card data
          </label>
          <textarea
            id="pc-client-card"
            className="min-h-32 w-full resize-y rounded-xl border border-white/10 bg-black/20 px-3 py-2 font-mono text-xs text-slate-200 outline-none transition focus:border-violet-400/60 focus:ring-2 focus:ring-violet-400/20 disabled:opacity-50"
            value={cardText}
            onChange={(event) => {
              setCardText(event.target.value);
              setFileName("");
              setLocalError("");
              client.clearError();
            }}
            disabled={client.busy}
            spellCheck={false}
          />
        </div>
        <input
          ref={fileRef}
          type="file"
          className="sr-only"
          accept=".json,.html,application/json,text/plain,text/html"
          onChange={(event) => void loadFile(event)}
          disabled={client.busy}
        />
        <Btn
          type="button"
          variant="secondary"
          className="w-full"
          disabled={client.busy}
          onClick={() => fileRef.current?.click()}
        >
          {fileName ? `Selected: ${fileName}` : "Choose card file"}
        </Btn>
        <ClientError client={client} message={localError} />
        <Btn type="submit" className="w-full" disabled={client.busy}>
          {client.busy ? "Checking card…" : "Use this card"}
        </Btn>
      </form>
      <ThisPcLink />
    </Card>
  );
}

function DeviceScan({ client }) {
  const phase = client.status.phase;
  const hasCard = phase !== "ready_disconnected";
  const canForget = phase === "ready_disconnected" && client.status.configured;
  const [showForget, setShowForget] = useState(false);
  const [forgetText, setForgetText] = useState("");

  async function forgetSavedPairing() {
    if (forgetText !== "FORGET") return;
    await client.forget(forgetText).catch(() => {});
    setForgetText("");
  }

  return (
    <Card>
      <div className="flex items-center gap-2">
        <Bluetooth className="h-5 w-5 text-violet-300" />
        <h2 className="font-semibold text-slate-100">
          {hasCard ? "Find the Pi from this card" : "Reconnect to the saved Pi"}
        </h2>
      </div>
      <p className="mt-2 text-sm leading-relaxed text-slate-400">
        Scan nearby BASS devices, then select the matching Pi. The PC client never
        chooses a discovered device automatically.
      </p>
      {canForget ? (
        <div className="mt-4 rounded-xl border border-white/10 bg-white/5 p-3 text-sm leading-relaxed text-slate-300">
          Your pairing is saved on this PC. After a connection timeout, scan and
          reconnect without importing its JSON card again.
        </div>
      ) : null}
      {client.status.device ? (
        <div className="mt-4 rounded-xl border border-white/10 bg-white/5 p-3 text-sm">
          <div className="text-xs text-slate-500">Expected Pi</div>
          <div className="mt-1 font-medium text-slate-200">
            {client.status.device.name || client.status.device.device_id}
          </div>
        </div>
      ) : null}
      <Btn
        className="mt-5 w-full"
        disabled={client.busy}
        onClick={() => void client.scan().catch(() => {})}
      >
        {client.busy
          ? "Scanning…"
          : canForget
            ? "Scan to reconnect"
            : "Scan for Pi"}
      </Btn>
      {client.devices.length ? (
        <div className="mt-4 space-y-2" aria-label="Discovered BASS devices">
          {client.devices.map((device) => (
            <button
              key={device.candidate_id}
              type="button"
              className="flex w-full items-center justify-between rounded-xl border border-white/10 bg-white/5 p-3 text-left transition hover:border-violet-400/40 hover:bg-violet-400/10 disabled:opacity-50"
              disabled={client.busy}
              onClick={() => void client.connect(device.candidate_id).catch(() => {})}
            >
              <span>
                <span className="block text-sm font-medium text-slate-100">
                  {device.name || "BASS Pi"}
                </span>
                <span className="mt-0.5 block text-xs text-slate-500">
                  Signal {Number.isFinite(device.rssi) ? `${device.rssi} dBm` : "unknown"}
                </span>
              </span>
              <span className="text-xs font-semibold text-violet-300">
                {canForget ? "Reconnect" : "Connect"}
              </span>
            </button>
          ))}
        </div>
      ) : client.scanComplete ? (
        <p className="mt-4 text-sm text-amber-200" role="status">
          No BASS Pi was found. Check that Bluetooth and the Pi are on, then scan
          again.
        </p>
      ) : null}
      <ClientError client={client} />
      {canForget ? (
        showForget ? (
          <div className="mt-5 rounded-xl border border-rose-400/20 bg-rose-400/10 p-4">
            <p className="text-sm leading-relaxed text-rose-100">
              Forget this saved pairing only when its credential was revoked, its
              account PIN changed, or you need to import a new invitation. Type
              FORGET to continue.
            </p>
            <Input
              className="mt-3"
              value={forgetText}
              onChange={(event) => setForgetText(event.target.value)}
              autoComplete="off"
              aria-label="Type FORGET to remove the saved pairing"
              disabled={client.busy}
            />
            <div className="mt-3 flex gap-2">
              <Btn
                variant="secondary"
                className="flex-1"
                disabled={client.busy}
                onClick={() => {
                  setShowForget(false);
                  setForgetText("");
                }}
              >
                Keep pairing
              </Btn>
              <Btn
                variant="danger"
                className="flex-1"
                disabled={client.busy || forgetText !== "FORGET"}
                onClick={() => void forgetSavedPairing()}
              >
                Forget pairing
              </Btn>
            </div>
          </div>
        ) : (
          <button
            type="button"
            className="mt-4 w-full text-center text-sm text-rose-300 hover:text-rose-200"
            disabled={client.busy}
            onClick={() => {
              client.clearError();
              setShowForget(true);
            }}
          >
            Forget saved Pi pairing
          </button>
        )
      ) : null}
      <ThisPcLink />
    </Card>
  );
}

function CommissionCard({ client }) {
  const purpose =
    client.stagedCard?.purpose ||
    client.status.card?.purpose ||
    client.status.pending_commission?.purpose ||
    "";
  const activation = purpose === "activation";
  const recovery = purpose === "recovery";
  const choosesNewPin = activation || recovery;
  const [pin, setPin] = useState("");
  const [confirmPin, setConfirmPin] = useState("");
  const [name, setName] = useState("");
  const [deviceName, setDeviceName] = useState(defaultClientName);
  const [localError, setLocalError] = useState("");

  async function submit(event) {
    event.preventDefault();
    if (!PIN_PATTERN.test(pin)) {
      setLocalError("PIN must be exactly 6 ASCII digits.");
      return;
    }
    if (choosesNewPin && pin !== confirmPin) {
      setLocalError("PINs do not match. Re-enter the new PIN.");
      return;
    }
    if (!deviceName.trim()) {
      setLocalError("Enter a name for this PC client.");
      return;
    }
    if (activation && !name.trim()) {
      setLocalError("Enter the first owner account name.");
      return;
    }
    setLocalError("");
    const input = { pin, device_name: deviceName.trim() };
    if (activation) input.name = name.trim();
    await client.commission(input).catch(() => {});
    setPin("");
    setConfirmPin("");
  }

  return (
    <Card>
      <h2 className="font-semibold text-slate-100">
        {activation
          ? "Activate this Pi"
          : recovery
            ? "Recover owner access"
            : "Pair this PC"}
      </h2>
      <p className="mt-2 text-sm leading-relaxed text-slate-400">
        {activation
          ? "Create the first owner account and choose its new PIN. The activation card can be used once."
          : recovery
            ? "Hold the Pi recovery button for 10 seconds to open its physical recovery window, then choose the owner's new PIN."
            : "Enter the existing PIN for the account named by the invitation card."}
      </p>
      <form className="mt-5 space-y-4" onSubmit={submit}>
        {activation ? (
          <div>
            <label htmlFor="pc-client-owner-name" className="mb-1 block text-sm font-medium">
              First owner name
            </label>
            <Input
              id="pc-client-owner-name"
              autoComplete="username"
              maxLength={100}
              value={name}
              onChange={(event) => setName(event.target.value)}
              disabled={client.busy}
            />
          </div>
        ) : null}
        <div>
          <label htmlFor="pc-client-pin" className="mb-1 block text-sm font-medium">
            {choosesNewPin ? "New 6-digit PIN" : "6-digit PIN"}
          </label>
          <Input
            id="pc-client-pin"
            type="password"
            autoComplete={choosesNewPin ? "new-password" : "current-password"}
            inputMode="numeric"
            pattern="[0-9]{6}"
            maxLength={6}
            value={pin}
            onChange={(event) => setPin(event.target.value)}
            disabled={client.busy}
          />
        </div>
        {choosesNewPin ? (
          <div>
            <label
              htmlFor="pc-client-confirm-pin"
              className="mb-1 block text-sm font-medium"
            >
              Confirm new PIN
            </label>
            <Input
              id="pc-client-confirm-pin"
              type="password"
              autoComplete="new-password"
              inputMode="numeric"
              pattern="[0-9]{6}"
              maxLength={6}
              value={confirmPin}
              onChange={(event) => setConfirmPin(event.target.value)}
              disabled={client.busy}
            />
          </div>
        ) : null}
        <div>
          <label htmlFor="pc-client-name" className="mb-1 block text-sm font-medium">
            Name for this PC
          </label>
          <Input
            id="pc-client-name"
            value={deviceName}
            maxLength={100}
            onChange={(event) => setDeviceName(event.target.value)}
            disabled={client.busy}
          />
        </div>
        <ClientError client={client} message={localError} />
        <Btn type="submit" className="w-full" disabled={client.busy}>
          {client.busy
            ? activation
              ? "Activating…"
              : recovery
                ? "Recovering…"
                : "Pairing…"
            : activation
              ? "Activate Pi"
              : recovery
                ? "Recover owner"
                : "Pair PC"}
        </Btn>
      </form>
    </Card>
  );
}

function CommissionRetryCard({ client }) {
  const [pin, setPin] = useState("");
  const [localError, setLocalError] = useState("");

  async function submit(event) {
    event.preventDefault();
    if (!PIN_PATTERN.test(pin)) {
      setLocalError("Enter the same 6-digit PIN used for the original request.");
      return;
    }
    setLocalError("");
    await client.retryCommission(pin).catch(() => {});
    setPin("");
  }

  const retryError =
    client.errorCode === "request_id_reused"
      ? "That PIN did not match the original request. Retry with the original PIN."
      : "";

  return (
    <Card>
      <h2 className="font-semibold text-amber-100">Setup result needs checking</h2>
      <p className="mt-2 text-sm leading-relaxed text-slate-300">
        The Pi may have completed the request before Bluetooth disconnected. Retry
        sends the exact saved request; it does not create a new credential or repeat
        a completed write.
      </p>
      <form className="mt-5 space-y-4" onSubmit={submit}>
        <div>
          <label htmlFor="pc-client-retry-pin" className="mb-1 block text-sm font-medium">
            Original 6-digit PIN
          </label>
          <Input
            id="pc-client-retry-pin"
            type="password"
            autoComplete="current-password"
            inputMode="numeric"
            pattern="[0-9]{6}"
            maxLength={6}
            value={pin}
            onChange={(event) => setPin(event.target.value)}
            disabled={client.busy}
          />
        </div>
        <ClientError client={client} message={localError || retryError} />
        <Btn type="submit" className="w-full" disabled={client.busy}>
          {client.busy ? "Checking saved request…" : "Retry saved request"}
        </Btn>
      </form>
    </Card>
  );
}

function RecoveryCard({ client }) {
  const [downloaded, setDownloaded] = useState(false);
  const [pin, setPin] = useState("");
  const [localError, setLocalError] = useState("");

  async function download() {
    if (!PIN_PATTERN.test(pin)) {
      setLocalError("Enter the paired account's 6-digit PIN.");
      return;
    }
    setLocalError("");
    try {
      const result = await client.downloadRecoveryCard(pin);
      saveBlob(result.blob, recoveryFileName(result.disposition));
      setPin("");
      setDownloaded(true);
    } catch (error) {
      setLocalError(error.message);
    }
  }

  async function acknowledge() {
    if (!downloaded) return;
    await client.acknowledgeRecoveryCard().catch(() => {});
  }

  return (
    <ClientPage>
      <Card>
        <div className="flex items-center gap-2">
          <CheckCircle2 className="h-5 w-5 text-emerald-300" />
          <h2 className="font-semibold text-slate-100">Pi setup completed</h2>
        </div>
        <p className="mt-2 text-sm leading-relaxed text-slate-300">
          Save the recovery card now. It is the recovery credential for this Pi and
          should be stored somewhere private. Enter the paired account PIN to
          download it.
        </p>
        <div className="mt-5">
          <label htmlFor="pc-client-recovery-pin" className="mb-1 block text-sm font-medium">
            Paired account PIN
          </label>
          <Input
            id="pc-client-recovery-pin"
            type="password"
            autoComplete="current-password"
            inputMode="numeric"
            pattern="[0-9]{6}"
            maxLength={6}
            value={pin}
            onChange={(event) => setPin(event.target.value)}
            disabled={client.busy}
          />
        </div>
        <Btn
          className="mt-3 w-full"
          disabled={client.busy}
          onClick={() => void download()}
        >
          <Download className="mr-2 h-4 w-4" />
          {downloaded ? "Download recovery card again" : "Download recovery card"}
        </Btn>
        <ClientError client={client} message={localError} />
        <Btn
          variant="secondary"
          className="mt-3 w-full"
          disabled={client.busy || !downloaded}
          onClick={() => void acknowledge()}
        >
          I saved the recovery card
        </Btn>
      </Card>
    </ClientPage>
  );
}

function ClientError({ client, message = "", fallback = "" }) {
  const connectionError =
    typeof client.status?.connection_error?.message === "string"
      ? client.status.connection_error.message
      : "";
  const visible = message || client.error || connectionError || fallback;
  if (!visible) return null;
  return (
    <div
      className="mt-4 rounded-xl border border-rose-400/20 bg-rose-400/10 p-3 text-sm text-rose-200"
      role="alert"
    >
      {visible}
    </div>
  );
}

function ThisPcLink() {
  const href = thisPcUrl();
  return (
    <a
      href={href}
      className="mt-4 flex min-h-11 w-full items-center justify-center gap-2 rounded-xl border border-white/10 px-4 text-sm font-semibold text-slate-300 transition hover:bg-white/5 hover:text-white"
    >
      <Monitor className="h-4 w-4" />
      Open this PC dashboard
    </a>
  );
}
