const ORIGIN_BASE = "https://origin.jumpserve.dev/objects/";

const PASSTHROUGH_HEADERS = [
  "accept-ranges",
  "cache-control",
  "content-length",
  "content-type",
  "etag",
  "last-modified",
];

export default {
  async fetch(request) {
    const requestUrl = new URL(request.url);
    if (request.method !== "GET") {
      return new Response("Method not allowed", {
        status: 405,
        headers: { "Cache-Control": "no-store" },
      });
    }

    const match = /^\/objects\/([a-z0-9](?:[a-z0-9/-]{0,126}[a-z0-9])?\.bin)$/.exec(
      requestUrl.pathname,
    );
    if (!match || match[1].includes("//") || match[1].includes("..")) {
      return new Response("Jumpserve DigitalOcean-origin cache target", {
        headers: { "Cache-Control": "no-store" },
      });
    }

    const originUrl = new URL(match[1], ORIGIN_BASE);
    const originResponse = await fetch(originUrl, {
      method: "GET",
      headers: { "Accept-Encoding": "identity" },
      redirect: "manual",
      cf: {
        cacheEverything: true,
        cacheTtlByStatus: {
          "200-299": 3600,
          "300-399": 0,
          "400-499": 0,
          "500-599": 0,
        },
      },
    });
    if (originResponse.status !== 200) {
      return new Response("Origin fetch failed", {
        status: 502,
        headers: { "Cache-Control": "no-store" },
      });
    }

    const headers = new Headers();
    for (const name of PASSTHROUGH_HEADERS) {
      const value = originResponse.headers.get(name);
      if (value !== null) headers.set(name, value);
    }
    headers.set("Cache-Control", "public, max-age=3600, immutable");

    return new Response(originResponse.body, {
      status: 200,
      headers,
    });
  },
};
