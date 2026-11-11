# Central de Acessos Flatter

Esta mudança está preparada para revisão; sua presença no repositório não significa que foi aplicada no Supabase nem publicada. Preserve a conta Master e a organização existentes. Não crie outro projeto ou outro Master para instalar a atualização.

## Antes de habilitar

Confirme um backup privado e o acesso administrativo ao projeto existente. Revise e aplique, na ordem, as migrações pendentes da branch web e a migração `central_acessos` em uma janela adequada. A validação em banco descartável não substitui o teste de restauração de um backup real. Configure `SUPABASE_SERVICE_ROLE_KEY` exclusivamente nos Secrets protegidos do Streamlit; a chave é lida pelo backend no momento da operação e não é colocada no estado de sessão. Nunca a envie por chat, URL ou GitHub.

Mantenha cadastro público e login anônimo desativados. Configure no Supabase uma política de senhas compatível com a do aplicativo: 12 a 128 caracteres, incluindo letras, números e símbolos. O Auth pode aplicar restrições adicionais; não reduza a segurança para contornar uma recusa. Não inclua dados reais nas verificações automatizadas.

## Cadastrar e administrar

1. Entre com a conta Master existente e abra **Administração → Usuários e permissões**.
2. Em **Criar conta**, informe nome, e-mail, perfil, exceções de permissão e senha temporária forte. Entregue a senha por um canal privado previamente combinado; o aplicativo não envia convite.
3. A senha temporária permite somente iniciar a troca por uma senha pessoal. A primeira troca é obrigatória e deve ser concluída em até 24 horas. Se vencer, o Master precisa preparar nova senha.
4. Em **Editar acessos**, altere perfil, permissões ou situação. Desativar bloqueia as sessões anteriores no banco do aplicativo; reativar não ressuscita essas sessões. O histórico administrativo pode ser consultado na mesma área por quem possui `audit.view`.

O cadastro ocorre em duas operações de serviços diferentes: criação no Auth e vínculo protegido no banco. Se o vínculo falhar, a identidade Auth pode ficar sem perfil e, portanto, sem acesso aos dados. Confira a falha no painel protegido antes de tentar novamente; não exclua contas existentes automaticamente.

## Recuperar acesso

Para um funcionário sem e-mail, o Master usa **Redefinir acesso**, confirma a identidade da pessoa, informa uma senha temporária e confirma sua própria senha. O backend revalida a sessão Master e a organização; o banco bloqueia o acesso das sessões anteriores antes da alteração no Auth. A nova senha temporária exige troca em 24 horas. Se houver falha entre as etapas, a conta permanece bloqueada até nova tentativa administrativa. O fluxo não permite redefinir o Master nem contas de outra organização.

O corte das sessões é aplicado pelo banco do Gerente usando a data de criação da sessão Auth. Ele impede consultas, inclusive com JWT antigo ou renovado, mas não afirma apagar todas as sessões no próprio Supabase Auth ou em outros aplicativos. O logout comum usa o escopo global do Auth. Cada operação protegida continua verificando identidade, conta ativa e permissões.

Para recuperação automática por e-mail, configure SMTP autorizado, remetente e limites no Supabase. O SMTP padrão pode atender apenas destinatários da equipe do projeto; não presuma entrega para funcionários. No template de recuperação, inclua `{{ .Token }}` para código digitado, em vez de enviar token na URL. Verifique o envio com conta de teste autorizada e somente então configure `AUTH_EMAIL_ENABLED = true`. Nenhum envio real é ativado por esta alteração.

Se o único Master perder o acesso, use o painel protegido do projeto Supabase e o procedimento de recuperação do próprio Auth para a **mesma conta**, depois confirme o login no Gerente. Sem SMTP configurado, o proprietário pode atualizar a senha da conta existente por meio do painel administrativo ou da Auth Admin API em ambiente seguro. Não recrie a organização, não execute novamente o bootstrap e não compartilhe a chave administrativa. A recuperação de emergência exige acesso autorizado à conta Supabase.

## Segurança e limites

As senhas nunca são salvas pelo Gerente. O controle privado compara apenas uma impressão digital do hash já mantido pelo Supabase Auth para confirmar que a senha mudou. Ele não concede permissões por `user_metadata`. A redefinição administrativa tem limite de cinco inícios por Master por hora e a tela usa mensagens genéricas para recuperação por e-mail; mantenha também os limites e a proteção contra abuso do Supabase Auth.

O plano gratuito tem cotas e pode pausar por inatividade. SMTP, domínio ou serviços pagos não estão incluídos nem contratados. A aprovação desta alteração não equivale à auditoria independente ou à validação com funcionários reais.
