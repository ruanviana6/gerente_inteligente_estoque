/** Executor DEMO sob demanda. Não possui adaptador HTTP ou credencial do ERP. */
type Config = { url: string; anon: string; executor: string };

export function createHandler(config: Config, transport: typeof fetch = fetch) {
  /** Permite injeção de transporte apenas em testes, nunca por parâmetros HTTP. */
  return async (request: Request): Promise<Response> => {
    const answer = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
      status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });
    if (request.method !== "POST") return answer({ code: "METHOD_NOT_ALLOWED" }, 405);
    const authorization = request.headers.get("Authorization") || "";
    if (!/^Bearer [A-Za-z0-9_.-]{10,16384}$/.test(authorization)) return answer({ code: "AUTH_REQUIRED" }, 401);
    if (!request.headers.get("Content-Type")?.toLowerCase().startsWith("application/json")) {
      return answer({ code: "JSON_REQUIRED" }, 415);
    }
    if (!config.url || !config.anon || !config.executor) return answer({ code: "CONFIGURATION_PENDING" }, 503);
    try {
      const declared = Number(request.headers.get("Content-Length") || "0");
      if (declared > 4096) return answer({ code: "PAYLOAD_TOO_LARGE" }, 413);
      const reader = request.body?.getReader();
      const chunks: Uint8Array[] = [];
      let size = 0;
      if (reader) {
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          size += value.byteLength;
          if (size > 4096) {
            await reader.cancel();
            return answer({ code: "PAYLOAD_TOO_LARGE" }, 413);
          }
          chunks.push(value);
        }
      }
      const bytes = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
      const raw = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
      const input = JSON.parse(raw);
      if (!input || Array.isArray(input) || Object.keys(input).sort().join(",") !== "action,request_id"
        || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(input.request_id)
        || !["execute_demo", "reconcile_demo"].includes(input.action)) {
        return answer({ code: "INVALID_DEMO_REQUEST" }, 400);
      }
      const call = async (path: string, body: unknown, administrative = false) => {
        // O JWT administrativo só conclui a transição do executor. Leituras e
        // início usam o JWT individual, com RLS e sessão revalidadas no banco.
        const key = administrative ? config.executor : config.anon;
        return await transport(config.url + path, {
          method: body === null ? "GET" : "POST", redirect: "error",
          headers: { apikey: key, Authorization: administrative ? `Bearer ${key}` : authorization,
            "Content-Type": "application/json" },
          body: body === null ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(15000),
        });
      };
      const identity = await call("/auth/v1/user", null);
      if (!identity.ok) return answer({ code: "AUTH_DENIED" }, 401);
      const user = await identity.json();
      if (!user.id || user.is_anonymous === true) return answer({ code: "AUTH_DENIED" }, 401);
      const started = await call("/rest/v1/rpc/fiscal_begin", { p_request: input.request_id, p_action: input.action });
      if (!started.ok) return answer({ code: "REQUEST_DENIED" }, [401, 403].includes(started.status) ? 403 : 409);
      const claim = await started.json();
      if (!claim.claim) return answer({ state: claim.state, busy: claim.busy || false, environment: "demo", real_enabled: false, operation: "register_only", automatic_issuance: false });
      const finished = await call("/rest/v1/rpc/fiscal_worker_finish", { p_request: input.request_id, p_claim: claim.claim }, true);
      if (!finished.ok) return answer({ code: "RESULT_REQUIRES_CONFERENCE", environment: "demo" }, 409);
      const result = await finished.json();
      return answer({ id: result.id, state: result.state, environment: "demo", real_enabled: false, operation: "register_only", automatic_issuance: false });
    } catch (error) {
      // Erros, corpos, headers e tokens nunca são enviados a logs ou respostas.
      return answer({ code: error instanceof SyntaxError ? "INVALID_JSON" : "EXECUTOR_UNAVAILABLE" }, error instanceof SyntaxError ? 400 : 503);
    }
  };
}
