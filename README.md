# Email Cleaner

Ferramenta de linha de comando para filtrar mensagens usando regras YAML e a
Microsoft Graph API.

> **Atenção:** o modo padrão é apenas simulação. A opção `--apply` executa as
> ações configuradas; `delete_permanent` pode apagar mensagens sem possibilidade
> de recuperação. Revise as regras e os resultados antes de aplicar.

## Requisitos

- Python 3.10 ou superior
- Uma aplicação registrada no Microsoft Entra ID com autenticação de cliente
  público habilitada e permissão delegada `Mail.ReadWrite` no Microsoft Graph

## Instalação

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
Copy-Item config\rules.example.yaml config\rules.yaml
```

Defina `MS_CLIENT_ID` no arquivo `.env` com o ID da aplicação registrada. Para
uma conta pessoal, mantenha `MS_TENANT=consumers`; para uma conta corporativa,
use o ID do tenant.

## Autenticação

Execute o fluxo de device code:

```powershell
python scripts\login.py
```

Siga as instruções exibidas e salve o `MS_REFRESH_TOKEN` gerado no `.env`. Nunca
publique esse arquivo nem compartilhe o token.

## Configuração e execução

Edite `config/rules.yaml`. O exemplo incluído apenas move mensagens que
correspondam a um remetente de exemplo para o lixo eletrônico.

```powershell
# Simula e registra os candidatos; não altera mensagens
python -m email_cleaner --config config/rules.yaml

# Executa as ações definidas nas regras
python -m email_cleaner --config config/rules.yaml --apply
```

As regras permitem correspondência por remetente, assunto, corpo, cabeçalhos ou
expressão regular. Ações disponíveis: `move_to_junk`, `mark_read`, `soft_delete`
e `delete_permanent`. Use `--help` para ver todas as opções.

Os registros de auditoria são gravados em `reports/` e estão excluídos do Git,
pois podem conter remetentes e assuntos de mensagens.

## Testes

```powershell
pip install pytest pytest-cov
pytest
```

## Segurança e privacidade

- `.env`, regras pessoais e registros de execução são ignorados pelo Git.
- O token de atualização concede acesso à conta: mantenha-o privado e revogue-o
  se houver suspeita de exposição.
- A configuração de exemplo usa apenas valores fictícios; revise sua própria
  configuração com o modo de simulação antes de executar ações.
