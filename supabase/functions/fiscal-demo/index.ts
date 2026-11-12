/** Entrada produtiva do único executor fiscal, ainda restrito a DEMO. */
import { createHandler } from "./handler.ts";

const url = Deno.env.get("SUPABASE_URL") || "";
const parsed = new URL(url);
// HTTP só existe no Supabase descartável do CI, com marcador do processo.
// A implantação publicada não define esse marcador e continua exigindo HTTPS.
const local = Deno.env.get("FLATTER_LOCAL_DISPOSABLE") === "1" &&
  ["kong", "127.0.0.1", "localhost", "host.docker.internal"].includes(parsed.hostname);
if (parsed.protocol !== "https:" && !(local && parsed.protocol === "http:")) {
  throw new Error("O executor exige HTTPS.");
}
Deno.serve(createHandler({ url, anon: Deno.env.get("SUPABASE_ANON_KEY") || "",
  executor: Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "" }));
