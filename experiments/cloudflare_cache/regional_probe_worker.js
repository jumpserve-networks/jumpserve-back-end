const TARGET_ORIGIN =
  "https://cf-cache-local-probe.jumpserve-cache-study-20260826.workers.dev";
const PROBE_REGION = "__PROBE_REGION__";
const PROBE_SLUG = "__PROBE_SLUG__";
const MAX_OBJECTS_PER_CLASS = 20;

function jsonResponse(value, status = 200) {
  return new Response(JSON.stringify(value, null, 2), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

function roundMilliseconds(value) {
  return Math.round(value * 1000) / 1000;
}

function parseExperiment(url) {
  const run = url.searchParams.get("run") || "";
  const count = Number(url.searchParams.get("count") || "20");

  if (!/^[a-z0-9-]{1,18}$/.test(run)) {
    throw new Error("run must contain 1-18 lowercase letters, digits, or hyphens");
  }
  if (!Number.isInteger(count) || count < 1 || count > MAX_OBJECTS_PER_CLASS) {
    throw new Error(`count must be an integer from 1 to ${MAX_OBJECTS_PER_CLASS}`);
  }

  return { run, count };
}

function objectId(run, treatment, index) {
  return `${run}-${PROBE_SLUG}-${treatment[0]}-${String(index).padStart(2, "0")}`;
}

function hashSeed(value) {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function shuffled(values, seedText) {
  const output = [...values];
  let state = hashSeed(seedText) || 1;
  const random = () => {
    state ^= state << 13;
    state ^= state >>> 17;
    state ^= state << 5;
    return (state >>> 0) / 4294967296;
  };

  for (let index = output.length - 1; index > 0; index -= 1) {
    const swapIndex = Math.floor(random() * (index + 1));
    [output[index], output[swapIndex]] = [output[swapIndex], output[index]];
  }
  return output;
}

async function probeObject(descriptor, sequence) {
  const url = `${TARGET_ORIGIN}/object/${descriptor.objectId}.bin`;
  const started = performance.now();

  try {
    const response = await fetch(url, {
      method: "GET",
      headers: { "accept-encoding": "identity" },
    });
    const headersAt = performance.now();
    const reader = response.body?.getReader();
    let firstBodyAt = null;
    let bytes = 0;

    if (reader) {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        if (firstBodyAt === null) firstBodyAt = performance.now();
        bytes += value?.byteLength || 0;
      }
    }

    const completed = performance.now();
    return {
      sequence,
      object_id: descriptor.objectId,
      treatment: descriptor.treatment,
      ok: response.ok,
      http_status: response.status,
      cf_cache_status: response.headers.get("cf-cache-status"),
      age_seconds: response.headers.get("age"),
      cf_ray: response.headers.get("cf-ray"),
      headers_ms: roundMilliseconds(headersAt - started),
      first_body_ms: roundMilliseconds((firstBodyAt ?? completed) - started),
      total_ms: roundMilliseconds(completed - started),
      bytes,
    };
  } catch (error) {
    return {
      sequence,
      object_id: descriptor.objectId,
      treatment: descriptor.treatment,
      ok: false,
      error: error instanceof Error ? error.message : String(error),
      total_ms: roundMilliseconds(performance.now() - started),
    };
  }
}

async function runSequential(descriptors) {
  const results = [];
  for (let index = 0; index < descriptors.length; index += 1) {
    results.push(await probeObject(descriptors[index], index + 1));
  }
  return results;
}

function requestContext(request) {
  return {
    configured_region: PROBE_REGION,
    probe_slug: PROBE_SLUG,
    cf_placement: request.headers.get("cf-placement"),
    request_cf: {
      colo: request.cf?.colo ?? null,
      city: request.cf?.city ?? null,
      region: request.cf?.region ?? null,
      country: request.cf?.country ?? null,
    },
  };
}

export default {
  async fetch(request) {
    if (request.method !== "GET") {
      return jsonResponse({ error: "Method not allowed" }, 405);
    }

    const url = new URL(request.url);
    const context = requestContext(request);

    if (url.pathname === "/info") {
      return jsonResponse({ ...context, target_origin: TARGET_ORIGIN });
    }

    let experiment;
    try {
      experiment = parseExperiment(url);
    } catch (error) {
      return jsonResponse(
        { ...context, error: error instanceof Error ? error.message : String(error) },
        400,
      );
    }

    if (url.pathname === "/warm") {
      const descriptors = Array.from({ length: experiment.count }, (_, index) => ({
        objectId: objectId(experiment.run, "hot", index + 1),
        treatment: "hot",
      }));
      return jsonResponse({
        ...context,
        phase: "warm",
        run: experiment.run,
        results: await runSequential(descriptors),
      });
    }

    if (url.pathname === "/measure") {
      const descriptors = [];
      for (const treatment of ["hot", "cold"]) {
        for (let index = 1; index <= experiment.count; index += 1) {
          descriptors.push({
            objectId: objectId(experiment.run, treatment, index),
            treatment,
          });
        }
      }

      return jsonResponse({
        ...context,
        phase: "measure",
        run: experiment.run,
        results: await runSequential(
          shuffled(descriptors, `${experiment.run}-${PROBE_SLUG}`),
        ),
      });
    }

    return jsonResponse(
      {
        ...context,
        error: "Use /info, /warm?run=NAME, or /measure?run=NAME",
      },
      404,
    );
  },
};
