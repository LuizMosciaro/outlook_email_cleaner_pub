# email_cleaner

Apaga **permanentemente** (bypassa a Lixeira) e-mails que casam com regras, via Microsoft Graph.

## 1. Criar o app no Entra ID

1. <https://entra.microsoft.com> → **App registrations** → *New registration*.
2. Supported account types: **Accounts in any organizational directory and personal Microsoft accounts**.
3. *Authentication* → **Allow public client flows: Yes** (necessário para device code).
4. *API permissions* → Microsoft Graph → **Delegated** → `Mail.ReadWrite` → *Grant admin consent* (conta pessoal não precisa).
5. Anote o **Application (client) ID**.

## 2. Autenticar uma vez

```bat
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Edite `.env` com `MS_CLIENT_ID` (e `MS_TENANT=consumers` para conta pessoal, ou o tenant ID para corporativa) e rode:

```bat
python scripts/login.py
```

Copie o `MS_REFRESH_TOKEN` impresso para o `.env`.

## 3. Configurar regras

```bat
copy config\rules.example.yaml config\rules.yaml
```

- `folders`: `inbox`, `junkemail`, `deleteditems`, `archive`.
- `allow_senders`: nunca apaga esses remetentes (vence qualquer regra).
- `min_age_days` por regra evita apagar algo recém-chegado.
- `action`: `delete_permanent`, `soft_delete`, `move_to_junk`, `mark_read`.
- `regex`: expressões regulares testadas no assunto + corpo (OR entre padrões).
- `senders` compara com `from`, `sender` e `replyTo` da mensagem (pega spoofing de `from`).
- `sender_longer_than: N` apaga quando algum desses endereços tiver mais de N caracteres.

## 4. Testar e aplicar

```bat
python -m email_cleaner -c config/rules.yaml                       :: dry-run (padrão)
python -m email_cleaner -c config/rules.yaml --apply               :: executa
python -m email_cleaner -c config/rules.yaml --apply --workers 8   :: 8 threads
```

`--workers N` (padrão 1) paraleliza as ações; cada thread tem sua própria sessão HTTP.
Mensagens já removidas por outra thread/execução (HTTP 404) são registradas como
`result: already_gone` e contadas em `already_gone`, sem virar erro.

Auditoria automática em `reports/cleaner_<run_number>.log.json` (remetente, assunto, ação).
Use `--run-number N` para fixar o número; `--log-file caminho` sobrescreve o padrão.

## Deploy com GitHub Actions e Cloudflare

Os workflows estão em `.github/workflows/`. O GitHub Actions executa o limpador
a cada 15 minutos (UTC) e também permite execução manual. O Cloudflare Worker
opcional fornece o cron e aciona o mesmo workflow; ele não processa e-mails
diretamente. Os serviços podem ter limites e custos conforme o plano e o uso.

### Configurar a execução do limpador

1. No GitHub, abra *Settings → Secrets and variables → Actions → Secrets* e crie:
   - `MS_CLIENT_ID`: Application (client) ID do app do Entra ID.
   - `MS_TENANT`: `consumers` para conta pessoal ou o tenant ID corporativo.
   - `MS_REFRESH_TOKEN`: refresh token gerado por `python scripts/login.py`.
   - `RULES_YAML`: conteúdo YAML das regras a aplicar. Use como base
     `config/rules.example.yaml`; o workflow o grava em `config/rules.yaml`
     durante a execução. Assim, critérios pessoais não precisam ser commitados
     em um repositório público.
2. Revise as regras e ações com o modo de simulação local antes de salvar
   `RULES_YAML`. O workflow usa `--apply`; `delete_permanent` é irreversível.
3. O workflow `.github/workflows/email-cleaner.yml` roda a cada 15 minutos e
   aceita execução manual em *Actions → email-cleaner → Run workflow*. Os logs
   de auditoria são anexados à execução por 14 dias.
4. `.github/workflows/keepalive.yml` registra atividade mensal no repositório
   para reduzir o risco de o GitHub desativar schedules por inatividade. Ele
   precisa de permissão de escrita de conteúdo no repositório.

### Usar Cloudflare para agendar o workflow

1. Crie um PAT fine-grained do GitHub limitado a este repositório, com
   **Actions: Read and write**. Esse token permitirá ao Worker disparar o
   workflow por `workflow_dispatch`.
2. Crie um API token Cloudflare com permissão **Workers Scripts: Edit** para a
   conta onde o Worker será publicado.
3. Em *Settings → Secrets and variables → Actions → Secrets*, adicione:
   - `CLOUDFLARE_API_TOKEN`: API token do passo 2.
   - `CLOUDFLARE_ACCOUNT_ID`: ID da conta Cloudflare.
4. Faça push para `main`. O workflow
   `.github/workflows/deploy-cloudflare-worker.yml` publica o código definido
   em [`wrangler.toml`](wrangler.toml) e [`worker/index.js`](worker/index.js).
5. No painel Cloudflare, abra o Worker `outlook-email-cleaner` e configure:
   - Variable `GITHUB_REPO`: `usuario/repositorio`.
   - Secret `GITHUB_TOKEN`: PAT criado no passo 1.
   - Secret `TRIGGER_SECRET`: valor aleatório longo para proteger o disparo
     manual por HTTP.
6. Em *Settings → Secrets and variables → Actions → Variables*, defina
   `CLOUDFLARE_CRON_ENABLED` como `true`. Isso faz o workflow ignorar apenas o
   schedule do GitHub, evitando execuções agendadas duplicadas; o disparo
   manual continua habilitado. Se não definir essa variável, o schedule do
   GitHub segue ativo em paralelo.
7. O Worker aciona o workflow a cada 15 minutos (UTC). Para testar o endpoint
   manual, use `POST` com o secret:

   ```sh
   curl -X POST \
     -H "Authorization: Bearer <TRIGGER_SECRET>" \
     https://<worker>.workers.dev/trigger
   ```

O deploy é feito pelo GitHub Actions; não é necessário criar um site Cloudflare
Pages nem configurar um build estático. Os crons podem sofrer atrasos ou
limitações de disponibilidade, e ambos os métodos de agendamento consomem
execuções/minutos do GitHub Actions.

**Execução local agendada:** no Windows, o Task Scheduler pode executar
`python -m email_cleaner --config config/rules.yaml --apply`. Mantenha o PC ligado
e configure as variáveis do `.env`.

## Manutenção do token

O refresh token de conta pessoal expira após ~90 dias sem uso. Se aparecer erro 401:
rode `python scripts/login.py` de novo e atualize o secret `MS_REFRESH_TOKEN`.
Para rotação automática, adicione ao workflow um passo usando `gh secret set MS_REFRESH_TOKEN`
com um PAT (o `refresh_token` novo é impresso com `--print-refresh-token`).

## Segurança

- Nunca commite `.env` nem o refresh token.
- Teste sempre em dry-run antes de `--apply`.
- Comece com `max_actions_per_run` baixo (ex.: 20) e `min_age_days: 1`.
