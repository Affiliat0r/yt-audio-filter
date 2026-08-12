"use client";

import { useCallback, useEffect, useState } from "react";
import { fetchWorkers, probeLocalWorker } from "@/lib/client";
import type { WorkerSummary } from "@/lib/types";

const ANY = "__any__";

export interface TargetState {
  /** Worker id jobs should be pinned to, or null for "any available". */
  targetWorkerId: string | null;
  /** Worker sharing a machine with this browser, if one is running. */
  localWorkerId: string | null;
  workers: WorkerSummary[];
  probing: boolean;
}

/**
 * Chooses which machine runs the work.
 *
 * Defaults to the worker on the same machine as the browser, so opening the
 * Studio on a second laptop renders on that laptop. Falls back to "any
 * available" when nothing is running locally — otherwise opening the page on a
 * phone could queue jobs nothing will ever claim.
 */
export default function WorkerTarget({
  onChange,
}: {
  onChange: (s: TargetState) => void;
}) {
  const [workers, setWorkers] = useState<WorkerSummary[]>([]);
  const [localWorkerId, setLocalWorkerId] = useState<string | null>(null);
  const [selected, setSelected] = useState<string>(ANY);
  const [probing, setProbing] = useState(true);
  const [touched, setTouched] = useState(false);

  const refresh = useCallback(async () => {
    const [list, local] = await Promise.all([
      fetchWorkers().catch(() => [] as WorkerSummary[]),
      probeLocalWorker(),
    ]);
    setWorkers(list);
    setLocalWorkerId(local?.workerId ?? null);
    setProbing(false);

    // Only auto-select until the user expresses a preference.
    setSelected((cur) => (touched ? cur : local?.workerId ?? ANY));
  }, [touched]);

  useEffect(() => {
    void refresh();
    const h = setInterval(refresh, 30000);
    return () => clearInterval(h);
  }, [refresh]);

  useEffect(() => {
    onChange({
      targetWorkerId: selected === ANY ? null : selected,
      localWorkerId,
      workers,
      probing,
    });
  }, [selected, localWorkerId, workers, probing, onChange]);

  const online = workers.filter((w) => w.online);
  const chosen = workers.find((w) => w.workerId === selected);

  return (
    <div>
      <label className="label">Run the work on</label>
      <select
        className="field"
        value={selected}
        onChange={(e) => {
          setTouched(true);
          setSelected(e.target.value);
        }}
      >
        <option value={ANY}>Any available machine</option>
        {workers.map((w) => (
          <option key={w.workerId} value={w.workerId}>
            {w.hostname}
            {w.workerId === localWorkerId ? " (this machine)" : ""}
            {w.online ? "" : " — offline"}
          </option>
        ))}
      </select>

      <p className="mt-1 text-xs text-ink-400">
        {probing
          ? "Looking for a worker on this machine…"
          : localWorkerId
            ? selected === localWorkerId
              ? "Rendering here. Nothing leaves this machine except the preview."
              : "A worker is running here, but work is pinned elsewhere."
            : online.length
              ? "No worker on this machine — work will run on another one."
              : "No workers online. Jobs will queue until one starts."}
      </p>

      {chosen && !chosen.gpu && chosen.online && (
        <p className="mt-1 text-xs text-amber-300">
          {chosen.hostname} has no GPU. Renders fall back to CPU encoding and
          music removal will be much slower.
        </p>
      )}
    </div>
  );
}
