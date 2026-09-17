const PREFIX = "/local/wireless";
const PAIRING_PATH = "/local/pairing-qr";
const TARGET = "http://127.0.0.1:5056";
const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);
const LOCAL_PEERS = new Set(["127.0.0.1", "::1", "::ffff:127.0.0.1"]);

function isLocalRequest(request) {
  if (!LOCAL_PEERS.has(request.socket.remoteAddress)) return false;
  try {
    const page = new URL(`http://${request.headers.host}`);
    if (!LOCAL_HOSTS.has(page.hostname)) return false;
    if (Number(page.port || 80) !== request.socket.localPort) return false;
    if (page.username || page.password) return false;
    const origin = request.headers.origin;
    if (origin && origin !== page.origin) return false;
    const referer = request.headers.referer;
    if (referer && new URL(referer).origin !== page.origin) return false;
    const site = request.headers["sec-fetch-site"];
    return !site || site === "same-origin" || site === "none";
  } catch {
    return false;
  }
}

function reply(response, status, error) {
  for (const name of response.getHeaderNames()) {
    if (name.startsWith("access-control-")) response.removeHeader(name);
  }
  response.writeHead(status, {
    "Content-Type": "application/json",
    "Cache-Control": "no-store",
  });
  response.end(JSON.stringify({ error }));
}

export default function localWirelessProxy() {
  return {
    name: "local-wireless-proxy",
    apply: (_config, { command, isPreview }) =>
      command === "serve" && !isPreview,
    config: () => ({
      server: {
        proxy: {
          [`^(?:${PREFIX}/api/|${PAIRING_PATH}(?:\\?|$))`]: {
            target: TARGET,
            changeOrigin: true,
            proxyTimeout: 15000,
            configure(proxy) {
              proxy.on("proxyRes", (upstream, _request, response) => {
                for (const name of Object.keys(upstream.headers)) {
                  if (name.startsWith("access-control-")) {
                    delete upstream.headers[name];
                  }
                }
                for (const name of response.getHeaderNames()) {
                  if (name.startsWith("access-control-")) {
                    response.removeHeader(name);
                  }
                }
                upstream.headers["cache-control"] = "no-store";
              });
              proxy.on("error", (_error, _request, response) => {
                if (!response.headersSent) {
                  reply(
                    response,
                    502,
                    "Start the local BASS wireless host on port 5056.",
                  );
                }
              });
            },
          },
        },
      },
    }),
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        const localApi = request.url.startsWith(`${PREFIX}/api/`);
        const pairingQr = request.url.split("?", 1)[0] === PAIRING_PATH;
        if (!localApi && !pairingQr) return next();
        if (!isLocalRequest(request)) {
          return reply(
            response,
            403,
            "Open this dashboard through localhost on the host computer.",
          );
        }
        // The backend bridge uses its loaded identity after both local guards.
        request.headers.origin = TARGET;
        if (request.headers.referer) request.headers.referer = `${TARGET}/`;
        delete request.headers.authorization;
        next();
      });
    },
  };
}
