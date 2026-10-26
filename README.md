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

## Deploy com custo zero

**Opção A – GitHub Actions (recomendado).** Workflow pronto em `.github/workflows/email-cleaner.yml`.
- Publique o repo (privado serve: 2.000 min/mês grátis; o job leva ~1 min).
- Settings → Secrets: `MS_CLIENT_ID`, `MS_TENANT`, `MS_REFRESH_TOKEN`.
- Roda a cada 1h (`cron: "1 * * * *"`, UTC); dispara também manualmente em *Run workflow*.
- `.github/workflows/keepalive.yml` faz um commit vazio mensal: sem atividade por 60 dias o GitHub desativa o `schedule`.
- Cron do GitHub é *best-effort* (atrasos em pico). Para disparo exato, chame `workflow_dispatch` via API (`cron-job.org` + PAT) ou use a Opção B/C.
- Commit do `config/rules.yaml` **não** é feito (está no `.gitignore`) — remova a linha para versioná-lo.

**Opção B – Cloudflare Worker (cron triggers, 100% grátis).** Cron exato que dispara o workflow da Opção A via `workflow_dispatch` (sem duplicar a lógica de regras).
Arquivos: [`wrangler.toml`](wrangler.toml:1) e [`worker/index.js`](worker/index.js:1).

1. Crie um PAT fine-grained (GitHub → Settings → Developer settings → Personal access tokens) com permissão **Actions: Read and write** apenas neste repo.
2. Cloudflare → *Workers & Pages* → **Create** → **Worker** → *Connect to Git* (não use **Pages**: `Pages` não tem cron trigger e falha com `Could not detect a directory containing static files`).
3. Build command: `npx wrangler deploy` · Deploy command: `npx wrangler deploy` · Root directory: `/` (o `wrangler.toml` na raiz já aponta `main = "worker/index.js"`).
4. Settings → *Variables and Secrets*: `GITHUB_REPO` = `usuario/repo` (var) e `GITHUB_TOKEN` = PAT (secret).
5. Confirme em *Triggers* → Cron `1 * * * *`; teste com `curl https://<worker>.workers.dev/trigger`.
6. Se o deploy reclamar de Pages, apague o projeto Pages e repita no fluxo *Worker*.

Observação: requer `workflow_dispatch` no workflow (já presente) e consome os mesmos minutos de Actions; o ganho é o disparo pontual. Para rodar sem GitHub Actions, seria preciso reimplementar o motor de regras em JS (Graph `fetch` + refresh token em *Secret*).

**Opção C – PC ligado (Task Scheduler).** `python -m email_cleaner --apply` agendado; custo zero mas depende da máquina ligada.

**Opção D – Azure Function (Consumption).** 1M execuções grátis/mês, timer trigger de 15 min; exige storage account (custo ~centavos) e `azd`.

## Manutenção do token

O refresh token de conta pessoal expira após ~90 dias sem uso. Se aparecer erro 401:
rode `python scripts/login.py` de novo e atualize o secret `MS_REFRESH_TOKEN`.
Para rotação automática, adicione ao workflow um passo usando `gh secret set MS_REFRESH_TOKEN`
com um PAT (o `refresh_token` novo é impresso com `--print-refresh-token`).

## Segurança

- Nunca commite `.env` nem o refresh token.
- Teste sempre em dry-run antes de `--apply`.
- Comece com `max_actions_per_run` baixo (ex.: 20) e `min_age_days: 1`.
