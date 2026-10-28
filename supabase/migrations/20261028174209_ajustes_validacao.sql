-- Ajustes sugeridos pelos advisors, sem alterar dados ou autorizações vigentes.
create index decisions_author on public.decisions(author_id);
create index inventory_current_batch on public.inventory_current(organization_id,batch_id);
create index role_permissions_permission on public.role_permissions(permission);
create index user_permissions_permission on public.user_permissions(permission);
-- Acesso direto permanece negado. Apenas funções internas autorizadas usam essas tabelas.
create policy no_direct_job_credentials on private.job_credentials
  for all to anon,authenticated using(false) with check(false);
create policy no_direct_report_jobs on public.report_jobs
  for all to anon,authenticated using(false) with check(false);
