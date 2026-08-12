import { KEYS, JOB_TTL_SECONDS, redis } from "./redis";
import type {
  CatalogVideo,
  Job,
  JobInput,
  JobProgress,
  JobResult,
  JobStatus,
  WorkerHeartbeat,
  WorkerSummary,
} from "./types";

const LOG_TAIL_MAX = 60;

function newId(): string {
  // Sortable-ish id: base36 timestamp + random suffix.
  return (
    Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10)
  );
}

function emptyProgress(): JobProgress {
  return { stage: "Queued", percent: null, log: [], updatedAt: Date.now() };
}

export async function createJob(
  input: JobInput,
  targetWorkerId: string | null = null
): Promise<Job> {
  const r = redis();
  const now = Date.now();
  const job: Job = {
    id: newId(),
    kind: input.kind,
    status: "queued",
    targetWorkerId,
    claimedBy: null,
    input,
    progress: emptyProgress(),
    result: null,
    error: null,
    errorDetails: null,
    createdAt: now,
    startedAt: null,
    finishedAt: null,
    uploadRequested: false,
  };

  await r.set(KEYS.job(job.id), job, { ex: JOB_TTL_SECONDS });
  // Search jobs jump the queue — they are seconds, not minutes, and the user
  // is staring at a spinner waiting for them.
  if (input.kind === "search") {
    await r.rpush(KEYS.queue, job.id);
  } else {
    await r.lpush(KEYS.queue, job.id);
  }
  await r.zadd(KEYS.jobIndex, { score: now, member: job.id });
  return job;
}

export async function getJob(id: string): Promise<Job | null> {
  const job = await redis().get<Job>(KEYS.job(id));
  return job ?? null;
}

async function putJob(job: Job): Promise<void> {
  await redis().set(KEYS.job(job.id), job, { ex: JOB_TTL_SECONDS });
}

/**
 * Pop the next job this worker is allowed to run, and mark it claimed.
 *
 * With several machines polling one queue, a job queued from laptop B could
 * otherwise be grabbed by laptop A. Jobs carrying a `targetWorkerId` are only
 * handed to that worker; anything another worker pops is pushed back so its
 * owner can take it. Untargeted jobs go to whoever asks first.
 */
export async function claimNextJob(workerId?: string): Promise<Job | null> {
  const r = redis();
  // Jobs belonging to other workers, put back after we stop looking. Requeuing
  // inline would just hand us the same job again on the next iteration.
  const notMine: string[] = [];
  let claimed: Job | null = null;

  try {
    for (let attempt = 0; attempt < 25; attempt++) {
      const id = await r.rpop<string>(KEYS.queue);
      if (!id) break;

      const job = await getJob(id);
      // Job expired or was cancelled while queued — drop it.
      if (!job) continue;
      if (job.status === "cancelled") continue;

      if (job.targetWorkerId && job.targetWorkerId !== workerId) {
        notMine.push(id);
        continue;
      }

      job.status = "claimed";
      job.claimedBy = workerId ?? null;
      job.startedAt = Date.now();
      job.progress = {
        stage: "Claimed by worker",
        percent: null,
        log: [],
        updatedAt: Date.now(),
      };
      await putJob(job);
      claimed = job;
      break;
    }
  } finally {
    // Put them back where they were, not at the back of the line.
    //
    // `rpop` takes from the tail, so the tail is the front of the queue.
    // Restoring with `lpush` would bury another worker's job behind every
    // pending render. Pushing the skipped ids back with `rpush` in reverse pop
    // order leaves the one we popped first sitting at the tail again — first
    // out when its own worker asks. Safe to do after the loop: we have already
    // stopped popping, so we cannot immediately re-take them.
    for (let i = notMine.length - 1; i >= 0; i--) {
      await r.rpush(KEYS.queue, notMine[i]);
    }
  }

  return claimed;
}

export interface ProgressPatch {
  stage?: string;
  percent?: number | null;
  logLines?: string[];
  status?: Extract<JobStatus, "running">;
}

export async function updateProgress(
  id: string,
  patch: ProgressPatch
): Promise<Job | null> {
  const job = await getJob(id);
  if (!job) return null;

  if (patch.stage !== undefined) job.progress.stage = patch.stage;
  if (patch.percent !== undefined) job.progress.percent = patch.percent;
  if (patch.logLines?.length) {
    job.progress.log = [...job.progress.log, ...patch.logLines].slice(
      -LOG_TAIL_MAX
    );
  }
  job.progress.updatedAt = Date.now();
  if (job.status === "claimed") job.status = "running";
  await putJob(job);
  return job;
}

export async function completeJob(
  id: string,
  result: JobResult
): Promise<Job | null> {
  const job = await getJob(id);
  if (!job) return null;
  job.status = "done";
  job.result = result;
  job.finishedAt = Date.now();
  job.progress.stage = "Complete";
  job.progress.percent = 100;
  job.progress.updatedAt = Date.now();
  await putJob(job);
  return job;
}

export async function failJob(
  id: string,
  error: string,
  errorDetails?: string | null
): Promise<Job | null> {
  const job = await getJob(id);
  if (!job) return null;
  job.status = "error";
  job.error = error;
  job.errorDetails = errorDetails ?? null;
  job.finishedAt = Date.now();
  job.progress.stage = "Failed";
  job.progress.updatedAt = Date.now();
  await putJob(job);
  return job;
}

export async function cancelJob(id: string): Promise<Job | null> {
  const job = await getJob(id);
  if (!job) return null;
  if (job.status === "done" || job.status === "error") return job;
  job.status = "cancelled";
  job.finishedAt = Date.now();
  job.progress.stage = "Cancelled";
  job.progress.updatedAt = Date.now();
  await putJob(job);
  return job;
}

/** Flag an already-rendered job for YouTube upload by the worker. */
export async function requestUpload(id: string): Promise<Job | null> {
  const job = await getJob(id);
  if (!job) return null;
  if (job.status !== "done" || !job.result?.blobUrl) return job;
  job.uploadRequested = true;
  job.status = "queued";
  job.progress = {
    stage: "Queued for upload",
    percent: null,
    log: [],
    updatedAt: Date.now(),
  };
  await putJob(job);
  await redis().rpush(KEYS.queue, job.id);
  return job;
}

export async function listJobs(limit = 20): Promise<Job[]> {
  const r = redis();
  const ids = await r.zrange<string[]>(KEYS.jobIndex, 0, limit - 1, {
    rev: true,
  });
  if (!ids.length) return [];
  const jobs = await Promise.all(ids.map((id) => getJob(id)));
  return jobs.filter((j): j is Job => j !== null);
}

// ---------------------------------------------------------------- catalog

export async function getCatalog(): Promise<{
  videos: CatalogVideo[];
  updatedAt: number | null;
}> {
  const r = redis();
  const [videos, updatedAt] = await Promise.all([
    r.get<CatalogVideo[]>(KEYS.catalog),
    r.get<number>(KEYS.catalogUpdatedAt),
  ]);
  return { videos: videos ?? [], updatedAt: updatedAt ?? null };
}

export async function putCatalog(videos: CatalogVideo[]): Promise<void> {
  const r = redis();
  await r.set(KEYS.catalog, videos);
  await r.set(KEYS.catalogUpdatedAt, Date.now());
}

// ---------------------------------------------------------------- workers

/** A worker counts as online if it polled within this window. */
export const WORKER_ONLINE_WINDOW_MS = 90_000;

export async function recordHeartbeat(beat: WorkerHeartbeat): Promise<void> {
  const r = redis();
  // The per-worker key expires on its own, so a machine that is shut down
  // drops out of the list without anything having to reap it.
  await r.set(KEYS.workerHeartbeat(beat.workerId), beat, { ex: 300 });
  await r.zadd(KEYS.workerIndex, { score: Date.now(), member: beat.workerId });
  // Keep the legacy key fresh too, so the old status endpoint still works.
  await r.set(KEYS.heartbeat, beat, { ex: 300 });
}

export async function listWorkers(): Promise<WorkerSummary[]> {
  const r = redis();
  const ids = await r.zrange<string[]>(KEYS.workerIndex, 0, 49, { rev: true });
  if (!ids.length) return [];

  const beats = await Promise.all(
    ids.map((id) => r.get<WorkerHeartbeat>(KEYS.workerHeartbeat(id)))
  );
  const now = Date.now();
  return beats
    .filter((b): b is WorkerHeartbeat => !!b)
    .map((b) => ({ ...b, online: now - b.at < WORKER_ONLINE_WINDOW_MS }));
}
