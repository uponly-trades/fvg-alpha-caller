export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const streams = url.searchParams.get("streams");

    if (!streams) {
      return new Response("Missing ?streams= parameter", { status: 400 });
    }

    const binanceUrl = `wss://fstream.binance.com/stream?streams=${streams}`;

    // Connect upstream to Binance
    const resp = await fetch(binanceUrl, {
      headers: {
        Upgrade: "websocket",
        Connection: "Upgrade",
      }
    });

    if (resp.status !== 101) {
      return new Response(`Upstream error: ${resp.status}`, { status: 502 });
    }

    const upstream = resp.webSocket;
    upstream.accept();

    // Client WebSocket pair
    const [client, server] = Object.values(new WebSocketPair());
    server.accept();

    // Relay upstream -> client
    upstream.addEventListener("message", (msg) => {
      try { server.send(msg.data); } catch {}
    });
    upstream.addEventListener("close", () => {
      try { server.close(); } catch {}
    });
    upstream.addEventListener("error", () => {
      try { server.close(); } catch {}
    });

    // Relay client -> upstream
    server.addEventListener("message", (msg) => {
      try { upstream.send(msg.data); } catch {}
    });
    server.addEventListener("close", () => {
      try { upstream.close(); } catch {}
    });
    server.addEventListener("error", () => {
      try { upstream.close(); } catch {}
    });

    return new Response(null, {
      status: 101,
      webSocket: client,
    });
  }
};
