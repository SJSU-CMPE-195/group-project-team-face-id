import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  CirclePower,
  Lightbulb,
  Radio,
  RefreshCw,
} from "lucide-react";
import Badge from "../components/Badge.jsx";
import Btn from "../components/Btn.jsx";
import Card from "../components/Card.jsx";
import Switch from "../components/Switch.jsx";
import ActivationCard from "./ActivationCard.jsx";
import DevelopmentResetCard from "./DevelopmentResetCard.jsx";
import useHardwareSimulator from "./useHardwareSimulator.js";
import useMultifunctionButton from "./useMultifunctionButton.js";

const LED_LABELS = {
  off: { label: "Power off", className: "bg-slate-600" },
  unclaimed: { label: "Unclaimed", className: "bg-amber-400" },
  pairing: { label: "Pairing", className: "bg-sky-400" },
  claimed: { label: "Claimed", className: "bg-emerald-400" },
  recovery: { label: "Recovery", className: "bg-fuchsia-400" },
  legacy: { label: "Existing host", className: "bg-violet-400" },
};

function secondsLabel(value) {
  const seconds = Math.max(0, Math.ceil(Number(value) || 0));
  return seconds ? `${seconds}s` : "—";
}

export default function HardwareSimulatorPanel({ embedded = false, onReset }) {
  const simulator = useHardwareSimulator();
  const status = simulator.status;
  const [card, setCard] = useState(null);
  const [cardBusy, setCardBusy] = useState(false);
  const [powerBusy, setPowerBusy] = useState(false);
  const [failedPowerIntent, setFailedPowerIntent] = useState(null);
  const [actionError, setActionError] = useState("");
  const cardRequestRef = useRef(0);
  const cardIdentityRef = useRef("");
  const mountedRef = useRef(true);
  const controlsDisabled =
    !status?.powered || status?.maintenance || simulator.error;
  const button = useMultifunctionButton(simulator, controlsDisabled);

  const cardIdentity = `${status?.generation ?? ""}:${status?.ownership ?? ""}:${status?.powered ?? ""}`;
  cardIdentityRef.current = cardIdentity;
  useEffect(() => {
    cardRequestRef.current += 1;
    setCard(null);
    setCardBusy(false);
  }, [cardIdentity]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      cardRequestRef.current += 1;
    };
  }, []);

  const led = LED_LABELS[status?.led] || LED_LABELS.off;
  const retryPowerTarget =
    typeof status?.failed_power_target === "boolean"
      ? status.failed_power_target
      : failedPowerIntent;
  const ownershipLabel = useMemo(() => {
    if (status?.ownership === "unclaimed") return "Unclaimed";
    if (status?.ownership === "claimed") return "Claimed";
    return "Existing data";
  }, [status?.ownership]);

  async function revealCard() {
    const request = cardRequestRef.current + 1;
    cardRequestRef.current = request;
    const identity = cardIdentityRef.current;
    setCardBusy(true);
    setActionError("");
    try {
      const nextCard = await simulator.readCard();
      if (
        mountedRef.current &&
        cardRequestRef.current === request &&
        cardIdentityRef.current === identity
      ) {
        setCard(nextCard);
      }
    } catch (error) {
      if (mountedRef.current && cardRequestRef.current === request) {
        setActionError(error.message);
      }
    } finally {
      if (mountedRef.current && cardRequestRef.current === request) {
        setCardBusy(false);
      }
    }
  }

  async function changePower(powered) {
    setPowerBusy(true);
    setActionError("");
    setCard(null);
    try {
      await simulator.setPower(powered);
      setFailedPowerIntent(null);
      await simulator.refresh();
    } catch (error) {
      setFailedPowerIntent(powered);
      setActionError(error.message);
    } finally {
      setPowerBusy(false);
    }
  }

  if (simulator.availability === "loading") {
    return <PanelMessage embedded={embedded} text="Checking hardware simulator…" />;
  }

  if (!simulator.enabled) {
    return (
      <PanelMessage embedded={embedded} text="Hardware simulator is not enabled for this host process." />
    );
  }

  if (!simulator.authenticated || !status) {
    return (
      <PanelMessage
        embedded={embedded}
        error={simulator.error}
        text="Hardware controls are unavailable. Refresh the status or restart the PC host with Hardware Simulator enabled."
      />
    );
  }

  return (
    <div
      className={
        embedded
          ? "mx-auto w-full max-w-6xl space-y-4"
          : "mx-auto w-full max-w-6xl space-y-4 px-4 py-6 sm:px-6"
      }
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-violet-300">
            Simulated device inputs
          </p>
          <h1 className="mt-1 text-2xl font-bold text-slate-100">
            Hardware Simulator
          </h1>
          <p className="mt-2 max-w-3xl text-sm leading-relaxed text-slate-400">
            These controls simulate future device inputs. Ownership, PINs and
            authorization are still verified by the real host service.
          </p>
        </div>
        <Btn
          variant="secondary"
          disabled={button.held}
          onClick={() => void simulator.refresh().catch(() => {})}
        >
          <RefreshCw className="mr-2 h-4 w-4" />
          Refresh
        </Btn>
      </div>

      {status.maintenance ? (
        <div
          role="alert"
          className="rounded-xl border border-rose-400/30 bg-rose-400/10 p-4 text-sm text-rose-100"
        >
          The product is in maintenance mode. Product operations stay blocked
          until the pending operation safely completes or recovers.
        </div>
      ) : null}

      {simulator.error || actionError || button.error ? (
        <div
          role="alert"
          className="rounded-xl border border-rose-400/25 bg-rose-400/10 p-4 text-sm text-rose-200"
        >
          {actionError || button.error || simulator.error}
        </div>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="flex items-center gap-2 text-sm font-semibold text-slate-100">
                <Lightbulb className="h-4 w-4 text-amber-300" />
                Device status
              </div>
              <div className="mt-4 flex items-center gap-3">
                <span
                  className={`h-4 w-4 rounded-full shadow-[0_0_16px_currentColor] ${led.className}`}
                  aria-hidden="true"
                />
                <span className="text-sm text-slate-200">{led.label}</span>
              </div>
            </div>
            <Badge variant={status.powered ? "ok" : "warn"}>
              {ownershipLabel}
            </Badge>
          </div>
          <dl className="mt-5 grid grid-cols-2 gap-3 text-sm">
            <StatusValue
              label="Pairing window"
              value={secondsLabel(status.pairing_remaining_seconds)}
            />
            <StatusValue
              label="Recovery window"
              value={secondsLabel(status.recovery_remaining_seconds)}
            />
          </dl>
          <div className="mt-3 rounded-xl border border-white/[0.06] bg-dna-bg p-3 text-xs leading-relaxed text-slate-400">
            Network: current PC HTTPS over the existing LAN. Device hotspot and
            automatic Wi-Fi setup are not simulated or accepted in this phase.
          </div>
          {status.ownership === "legacy" ? (
            <p className="mt-4 text-xs leading-relaxed text-amber-200">
              Existing product data is preserved. Use Development reset only
              when you decide to test the complete first-owner flow.
            </p>
          ) : null}
        </Card>

        <Card>
          <div className="flex items-center justify-between gap-4">
            <div>
              <div className="flex items-center gap-2 text-sm font-semibold text-slate-100">
                <CirclePower className="h-4 w-4 text-violet-300" />
                Power switch
              </div>
              <p className="mt-2 text-xs leading-relaxed text-slate-400">
                Simulates device power only. It does not stop this PC console.
              </p>
            </div>
            <Switch
              checked={!!status.powered}
              disabled={powerBusy || button.held || status.maintenance}
              onChange={(powered) => void changePower(powered)}
              ariaLabel="Simulated device power"
            />
          </div>
          {retryPowerTarget !== null ? (
            <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-400/20 bg-amber-400/10 p-3">
              <span className="text-xs text-amber-100">
                The last power transition did not finish safely.
              </span>
              <Btn
                variant="secondary"
                disabled={powerBusy || button.held}
                onClick={() => void changePower(retryPowerTarget)}
              >
                Retry power {retryPowerTarget ? "on" : "off"}
              </Btn>
            </div>
          ) : null}
        </Card>

        <Card>
          <div className="flex items-center gap-2 text-sm font-semibold text-slate-100">
            <Radio className="h-4 w-4 text-sky-300" />
            Multifunction button
          </div>
          <p className="mt-2 text-xs leading-relaxed text-slate-400">
            Hold 3 seconds for pairing. Hold 10 seconds for recovery. The host
            measures the duration and chooses one action when you release.
          </p>
          <button
            type="button"
            disabled={controlsDisabled || (status.press_active && !button.held)}
            aria-pressed={button.held}
            onPointerDown={(event) => {
              if (event.button !== 0) return;
              event.currentTarget.setPointerCapture?.(event.pointerId);
              button.begin();
            }}
            onPointerUp={button.release}
            onPointerCancel={button.cancel}
            onBlur={button.cancel}
            onLostPointerCapture={() => {
              if (button.held) button.cancel();
            }}
            onContextMenu={(event) => event.preventDefault()}
            onKeyDown={(event) => {
              if ((event.key === " " || event.key === "Enter") && !event.repeat) {
                event.preventDefault();
                button.begin();
              }
            }}
            onKeyUp={(event) => {
              if (event.key === " " || event.key === "Enter") {
                event.preventDefault();
                button.release();
              }
            }}
            className={`mt-5 flex min-h-28 w-full select-none items-center justify-center rounded-2xl border text-base font-semibold transition focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-300 disabled:cursor-not-allowed disabled:opacity-45 ${
              button.held
                ? "border-fuchsia-300 bg-fuchsia-500/25 text-fuchsia-100 shadow-[inset_0_4px_16px_rgba(0,0,0,0.35)]"
                : "border-violet-400/30 bg-violet-500/10 text-violet-100 hover:bg-violet-500/15"
            }`}
          >
            {button.held ? "Holding…" : "Press and hold"}
          </button>
        </Card>

        <ActivationCard
          card={card}
          busy={cardBusy}
          disabled={!status.powered || status.maintenance}
          onReveal={() => void revealCard()}
        />
      </div>

      <DevelopmentResetCard
        buttonHeld={button.held}
        onReset={() => {
          setCard(null);
          setActionError("");
          onReset?.();
        }}
      />
    </div>
  );
}

function StatusValue({ label, value }) {
  return (
    <div className="rounded-xl border border-white/[0.06] bg-dna-bg p-3">
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="mt-1 font-semibold text-slate-100">{value}</dd>
    </div>
  );
}

function PanelMessage({ text, error = "", embedded = false }) {
  const Container = embedded ? "div" : "main";
  return (
    <Container
      className={
        embedded
          ? "flex min-h-80 items-center justify-center py-8 text-slate-100"
          : "app-shell flex min-h-screen items-center justify-center px-4 py-8 text-slate-100"
      }
    >
      <div className="w-full max-w-xl">
        <Card>
          <h1 className="text-lg font-semibold">Hardware Simulator</h1>
          <p className="mt-3 text-sm leading-relaxed text-slate-400">{text}</p>
          {error ? (
            <div className="mt-4 text-sm text-rose-200" role="alert">
              {error}
            </div>
          ) : null}
        </Card>
      </div>
    </Container>
  );
}
