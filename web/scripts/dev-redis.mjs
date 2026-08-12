/**
 * Minimal in-memory stand-in for Upstash Redis' REST API, for local
 * development and end-to-end testing without provisioning a real database.
 *
 *   node scripts/dev-redis.mjs [port]
 *
 * Then point the app at it:
 *   UPSTASH_REDIS_REST_URL=http://localhost:8790
 *   UPSTASH_REDIS_REST_TOKEN=devtoken
 *
 * Implements only the commands `lib/jobs.ts` uses. State is lost on restart —
 * this is a test fixture, never a production store.
 */

import { createServer } from "node:http";

const PORT = Number(process.argv[2] ?? 8790);
const TOKEN = process.env.DEV_REDIS_TOKEN ?? "devtoken";

/** @type {Map<string, unknown>} */
const strings = new Map();
/** @type {Map<string, string[]>} */
const lists = new Map();
/** @type {Map<string, Map<string, number>>} */
const zsets = new Map();

const list = (k) => {
  if (!lists.has(k)) lists.set(k, []);
  return lists.get(k);
};
const zset = (k) => {
  if (!zsets.has(k)) zsets.set(k, new Map());
  return zsets.get(k);
};

function execute(cmd) {
  const [rawName, ...args] = cmd;
  const name = String(rawName).toUpperCase();

  switch (name) {
    case "SET": {
      strings.set(String(args[0]), args[1]);
      return "OK"; // EX/PX args are accepted and ignored — no TTL in dev.
    }
    case "GET": {
      const v = strings.get(String(args[0]));
      return v === undefined ? null : v;
    }
    case "DEL": {
      let n = 0;
      for (const k of args) {
        const key = String(k);
        if (strings.delete(key)) n++;
        if (lists.delete(key)) n++;
        if (zsets.delete(key)) n++;
      }
      return n;
    }
    case "LPUSH": {
      const l = list(String(args[0]));
      for (const v of args.slice(1)) l.unshift(v);
      return l.length;
    }
    case "RPUSH": {
      const l = list(String(args[0]));
      for (const v of args.slice(1)) l.push(v);
      return l.length;
    }
    case "LPOP": {
      const l = list(String(args[0]));
      return l.length ? l.shift() : null;
    }
    case "RPOP": {
      const l = list(String(args[0]));
      return l.length ? l.pop() : null;
    }
    case "LLEN":
      return list(String(args[0])).length;
    case "LRANGE": {
      const l = list(String(args[0]));
      const start = Number(args[1]);
      const stop = Number(args[2]);
      return l.slice(start, stop === -1 ? undefined : stop + 1);
    }
    case "ZADD": {
      const z = zset(String(args[0]));
      // Ignore flag args (NX/XX/GT/CH); pairs are score, member.
      const rest = args.slice(1).filter((a) => {
        const s = String(a).toUpperCase();
        return !["NX", "XX", "GT", "LT", "CH", "INCR"].includes(s);
      });
      let added = 0;
      for (let i = 0; i + 1 < rest.length; i += 2) {
        const member = String(rest[i + 1]);
        if (!z.has(member)) added++;
        z.set(member, Number(rest[i]));
      }
      return added;
    }
    case "ZRANGE": {
      const z = zset(String(args[0]));
      const rev = args.some((a) => String(a).toUpperCase() === "REV");
      let members = [...z.entries()].sort((a, b) =>
        rev ? b[1] - a[1] : a[1] - b[1]
      );
      const start = Number(args[1]);
      const stop = Number(args[2]);
      members = members.slice(start, stop === -1 ? undefined : stop + 1);
      return members.map(([m]) => m);
    }
    case "PING":
      return "PONG";
    default:
      throw new Error(`dev-redis: unsupported command ${name}`);
  }
}

const server = createServer(async (req, res) => {
  const auth = req.headers.authorization ?? "";
  if (auth !== `Bearer ${TOKEN}`) {
    res.writeHead(401, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: "unauthorized" }));
    return;
  }

  let raw = "";
  for await (const chunk of req) raw += chunk;

  let payload;
  try {
    payload = JSON.parse(raw || "[]");
  } catch {
    res.writeHead(400, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: "bad json" }));
    return;
  }

  const isPipeline = (req.url ?? "").includes("pipeline") || Array.isArray(payload[0]);

  try {
    const body = isPipeline
      ? payload.map((cmd) => {
          try {
            return { result: execute(cmd) };
          } catch (e) {
            return { error: String(e.message ?? e) };
          }
        })
      : { result: execute(payload) };
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify(body));
  } catch (e) {
    res.writeHead(400, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: String(e.message ?? e) }));
  }
});

server.listen(PORT, () => {
  console.log(`dev-redis listening on http://localhost:${PORT} (token: ${TOKEN})`);
});
