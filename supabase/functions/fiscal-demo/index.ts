/** Entrada produtiva do único executor fiscal, ainda restrito a DEMO. */
import { createHandler } from "./handler.ts";

const url = Deno.env.get("SUPABASE_URL") || "";
if (new URL(url).protocol !== "https:") throw new Error("O executor exige HTTPS.");
Deno.serve(createHandler({ url, anon: Deno.env.get("SUPABASE_ANON_KEY") || "",
  executor: Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "" }));
