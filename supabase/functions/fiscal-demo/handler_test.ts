/** Testes adversariais do executor com transporte inspecionável e sem rede. */
import { createHandler } from "./handler.ts";

const config = { url: "https://projeto-ficticio.supabase.co", anon: "chave-publica-ficticia", executor: "segredo-exclusivo-ficticio" };
const id = "10000000-0000-4000-8000-000000000001";
const token = "Bearer jwt-individual-ficticio";
const body = { request_id: id, action: "execute_demo" };
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
function check(condition: unknown, message: string) {
  /** Não inclui corpos ou segredos na mensagem de falha. */
  if (!condition) throw new Error(message);
}
function request(value: unknown = body, headers: Record<string, string> = {}) {
  /** Monta requisição fictícia com os mesmos campos permitidos em produção. */
  return new Request("https://executor.invalid/", { method: "POST", headers: {
    Authorization: token, "Content-Type": "application/json", ...headers,
  }, body: JSON.stringify(value) });
}

Deno.test("nega anônimo, método, tipo, corpo excessivo e campos forjados antes da rede", async () => {
  let calls = 0;
  const handle = createHandler(config, (() => { calls++; throw new Error("Rede proibida"); }) as typeof fetch);
  for (const [input, expected] of [
    [new Request("https://executor.invalid/"), 405],
    [request(body, { Authorization: "" }), 401],
    [request(body, { "Content-Type": "text/plain" }), 415],
    [request({ ...body, organization_id: id }), 400],
    [request({ ...body, approved: true }), 400],
    [request({ ...body, action: "execute_real" }), 400],
    [request({ ...body, request_id: "../segredo" }), 400],
    [request({ ...body, padding: "a".repeat(5000) }), 413],
    [new Request("https://executor.invalid/", { method: "POST", headers: { Authorization: token, "Content-Type": "application/json" }, body: "{" }), 400],
  ] as [Request, number][]) {
    check((await handle(input)).status === expected, "Recusa HTTP inesperada");
  }
  check(calls === 0, "Input inválido atingiu transporte");
});

for (const [label, identity, expected] of [
  ["JWT expirado", json({}, 401), 401], ["identidade anônima", json({ id, is_anonymous: true }), 401],
  ["identidade incompleta", json({}), 401],
] as [string, Response, number][]) {
  Deno.test(label + " não chega ao banco fiscal", async () => {
    let calls = 0;
    const handle = createHandler(config, (() => { calls++; return Promise.resolve(identity); }) as typeof fetch);
    check((await handle(request())).status === expected, "Identidade inválida aceita");
    check(calls === 1, "Identidade inválida chegou a uma operação fiscal");
  });
}

Deno.test("permissão negada, outra organização e solicitação inexistente não usam chave administrativa", async () => {
  for (const denied of [401, 403, 404, 409]) {
    const paths: string[] = [];
    const handle = createHandler(config, (async (url: string | URL | Request, options?: { headers?: Record<string, string> }) => {
      paths.push(String(url));
      const headers = options?.headers as Record<string, string>;
      check(headers.Authorization === token, "Credencial privilegiada usada na leitura");
      return paths.length === 1 ? json({ id }) : json({}, denied);
    }) as typeof fetch);
    check((await handle(request())).status >= 400, "Falha de autorização ignorada");
    check(paths.length === 2, "Operação negada tentou concluir");
  }
});

Deno.test("fluxo válido usa JWT individual antes do finish restrito e nunca consulta ERP", async () => {
  const calls: { path: string; body: unknown }[] = [];
  const handle = createHandler(config, (async (url: string | URL | Request, options?: { headers?: Record<string, string>; redirect?: string; body?: string; method?: string }) => {
    const path = new URL(String(url)).pathname;
    check(String(url).startsWith(config.url + "/"), "Destino externo indevido");
    check(options?.redirect === "error", "Redirect não bloqueado");
    const headers = options?.headers as Record<string, string>;
    calls.push({ path, body: options?.body ? JSON.parse(String(options.body)) : null });
    if (path === "/auth/v1/user") { check(options?.method === "GET", "Auth não consultado"); return json({ id }); }
    if (path.endsWith("fiscal_begin")) { check(headers.Authorization === token, "Begin sem identidade individual"); return json({ claim: id }); }
    check(path.endsWith("fiscal_worker_finish"), "RPC fora da lista permitida");
    check(headers.Authorization === `Bearer ${config.executor}`, "Finish sem credencial do executor");
    return json({ id, state: "authorized", secret: "não-repassar" });
  }) as typeof fetch);
  const result = await handle(request());
  const text = await result.text();
  check(result.status === 200 && calls.length === 3, "Fluxo DEMO incompleto");
  check(!text.includes("não-repassar") && !text.includes(config.executor), "Resposta vazou campo interno");
  check(JSON.parse(text).real_enabled === false, "Modo real habilitado");
});

Deno.test("concessão ocupada ou já concluída não chama finish", async () => {
  for (const state of ["processing", "authorized", "blocked", "needs_reconciliation"]) {
    let calls = 0;
    const handle = createHandler(config, (async () => ++calls === 1 ? json({ id }) : json({ state })) as typeof fetch);
    check((await handle(request())).status === 200 && calls === 2, "Execução repetida indevida");
  }
});

Deno.test("timeout após início não é repetido nem expõe erro", async () => {
  let calls = 0;
  const handle = createHandler(config, (async () => {
    calls++;
    if (calls === 1) return json({ id });
    if (calls === 2) return json({ claim: id });
    throw new Error(config.executor);
  }) as typeof fetch);
  const result = await handle(request());
  check(result.status === 503 && calls === 3, "Timeout repetiu transição");
  check(!(await result.text()).includes(config.executor), "Erro sensível exposto");
});
