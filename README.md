# Driver Downloader v2.1.0

Aplicativo local para Windows 11 x64 que baixa arquivos e pastas do Google Drive
diretamente para o SSD, sem criar ZIPs. Interface em português brasileiro,
fila e histórico persistentes, concorrência controlada, progresso individual,
pausa, continuação e cancelamento reais.

## Como obter o aplicativo

1. Entre no GitHub com a conta **Lavylck** e abra
   **[versão estável do Driver Downloader](https://github.com/Lavylck/DriverBaixador/releases/latest)**.
2. Confira a release **Driver Downloader v2.1.0**.
3. Em **Assets**, baixe **DriverDownloader-Setup-x64.exe**.
4. Execute o instalador no Windows 11 e abra **Driver Downloader** pelo menu Iniciar.

O repositório é privado: somente usuários com acesso podem ver a release e seus
arquivos. O link `/releases/latest` aponta para a versão estável, não para
compilações de pré-lançamento.

**Uma release só existe depois que a compilação termina com sucesso.** Para
acompanhar ou baixar os mesmos arquivos antes de abrir Releases, visite
**[Actions](https://github.com/Lavylck/DriverBaixador/actions)**, abra a execução
**Compilar e entregar Driver Downloader para Windows** e, na seção **Artifacts**,
baixe `DriverDownloader-Windows-x64`. O ZIP de artefatos contém o instalador real,
a versão portátil, os hashes e o relatório de verificação.

Se a execução estiver vermelha ou não houver release, não há instalador validado
daquela execução. Abra a etapa que falhou; não confunda o arquivo de código-fonte
gerado automaticamente pelo GitHub com o instalador do aplicativo.

O instalador inclui Python, Qt/PySide e Rclone. Você não precisa instalar Python,
pip ou dependências manualmente. Instala somente para sua conta de usuário,
sem exigir administrador. Não desative o antivírus ou outras proteções do Windows.
Este projeto não inclui certificado de assinatura de código: o Windows pode
exibir informações de editor desconhecido. Em ambiente corporativo, distribua
uma versão assinada conforme a política da organização.

Alternativa: baixe `DriverDownloader-Windows-x64.zip`, extraia a pasta inteira e abra
`DriverDownloader.exe`. Mantenha `_internal`, `bin` e os demais arquivos juntos.
O ZIP serve apenas para distribuir o programa; os downloads do Drive são diretos.

## Uso

1. Em **Início**, cole uma URL de arquivo ou pasta do Google Drive.
2. Escolha a pasta de destino e clique em **Baixar**.
3. Se solicitado, autorize o acesso de leitura no navegador Google.
4. Acompanhe os arquivos em **Downloads**. Use **Pausar**, **Continuar** ou
   **Cancelar**; consulte o resultado e abra o destino em **Histórico**.

Em **Configurações**, escolha o diretório padrão, a quantidade de transferências
simultâneas, o limite de velocidade, o tema e a conta Google. O padrão conservador
de quatro transferências é adequado ao Ryzen 5 7600X e ao SSD NVMe; a GPU não
participa do download. Aumentar a concorrência não elimina cotas da API.

A pasta raiz compartilhada é listada antes de iniciar os arquivos. Subpastas
e nomes representáveis no Windows são preservados. Itens sem permissão,
nomes duplicados ou incompatíveis e tipos Google sem exportação suportada
produzem mensagens de erro em vez de sucesso incompleto.

## Autenticação e segurança

O navegador cuida do login; o aplicativo não solicita senha Google. O Rclone
incluído é utilizado para OAuth com escopo `drive.readonly`. A permissão é de
leitura e não permite alterar ou apagar os arquivos remotos. O primeiro login
pode ser necessário mesmo para pastas compartilhadas publicamente, pois este
aplicativo não inclui uma chave de API pública.

Credenciais persistentes usam DPAPI, vinculadas à conta do Windows, em
`%LOCALAPPDATA%\DriveBaixador`, com permissões restritas. Esse local permite
reaproveitar a autorização segura da versão anterior. O aplicativo não contém tokens ou segredos
privados no código. Configurações temporárias necessárias ao Rclone são locais
e protegidas. Tokens e cabeçalhos de autenticação não devem aparecer nos logs.
Trocar conta remove a credencial local; revogue a autorização nas configurações
da conta Google se desejar retirar a permissão concedida.

Atualizações preservam o mesmo identificador e a pasta de instalação
`%LOCALAPPDATA%\Programs\MeuXodoDrive` para substituir a instalação anterior
sem mover dados. O nome da pasta é legado; o aplicativo, o executável e os atalhos
passam a se chamar **Driver Downloader**. Configurações, histórico, downloads e
credenciais em `%LOCALAPPDATA%\DriveBaixador` não são migrados nem apagados.

O destino sempre é local. Nenhum servidor deste projeto recebe ou guarda os
vídeos. A API do Google é acessada por HTTPS, com verificação TLS habilitada.
O aplicativo não executa `rclone sync`, `delete`, `move` ou `purge`.

## Arquitetura e retomada

A versão 2 usa PySide6 para apresentação, SQLite para estado e histórico e
transferência HTTP em streaming pela API Drive. O Rclone permanece responsável
pela autenticação OAuth. A mudança do motor de transferência foi necessária
para controlar `Range`, validar a posição recebida e implementar pausa e
continuação por byte quando o arquivo permite, em vez de apenas reiniciar a cópia.

Os dados são lidos em blocos e gravados diretamente em um arquivo parcial.
Arquivos grandes não são carregados inteiros na memória. Conexões interrompidas
têm tentativas limitadas e backoff. O programa só promove um parcial ao nome final
depois de verificar tamanho e hash quando fornecidos pelo Google. O SQLite
registra o resultado para que o próximo início reconheça o que já foi concluído.

Se o servidor ignorar o Range, se o arquivo remoto mudar, se o validador não
corresponder ou se uma exportação não suportar continuação, o parcial é reiniciado
de forma segura. O aplicativo não promete retomada por byte para documentos
Google exportados. O estado da fila é restaurado em pausa após reiniciar o
programa, permitindo revisar o destino antes de continuar.

Permissão para listar não garante permissão de baixar: o proprietário pode
desabilitar downloads ou alterar o acesso durante a operação. Cotas diárias,
restrições de organização e erros `403` não podem ser contornados pelo programa.
Documentos nativos do Google precisam de exportação, que acrescenta a extensão
do formato escolhido. Nomes que o Windows não aceita não podem ser preservados
literalmente e são informados ao usuário.

## Compilar a partir do código

Requisitos de build: Windows 11 x64, Python 3.12 x64 e
[Inno Setup 6](https://jrsoftware.org/isinfo.php). No PowerShell, dentro de
`DriverBaixador`:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m pytest -q
.\tools\build-windows.ps1
```

Caso sua política corporativa não permita ativar scripts, chame diretamente
`.venv\Scripts\python.exe` para os comandos Python e use o workflow GitHub
para compilar; não é necessário desativar proteções do sistema.

O script baixa Rclone 1.76.0 do site oficial e valida SHA-256, executa os testes,
gera um pacote PyInstaller `--onedir --windowed`, verifica PE x64 e DLLs Qt,
inicia o executável com `--smoke-test`, compila o instalador, faz uma instalação
silenciosa temporária e testa a abertura do aplicativo instalado. Depois produz:

Em um computador que já tenha a instalação anterior, o teste de instalação
temporária é omitido para não sobrescrevê-la; o instalador ainda é compilado e
validado como PE. O GitHub Actions executa o teste completo em runner isolado.

```text
dist/DriverDownloader/DriverDownloader.exe
dist/DriverDownloader-Setup-x64.exe
dist/DriverDownloader-Windows-x64.zip
dist/SHA256SUMS.txt
dist/windows-build-report.json
```

Use `-PortableOnly` se deseja gerar apenas a versão portátil sem Inno Setup.
Execute com um Python de 64 bits. PyInstaller não compila um executável Windows
utilizável a partir do Linux; o workflow é a alternativa Windows automatizada.

## Compilação automatizada e evidências

O workflow `.github/workflows/windows-release.yml` roda em `windows-latest` após
alterações no código do aplicativo, e também pode ser iniciado manualmente em **Actions →
Run workflow** quando estiver na branch padrão. Ele:

1. Instala Python e dependências e executa os testes no Windows.
2. Obtém Rclone oficial com SHA-256 e compila o executável.
3. Confere os cabeçalhos PE, a presença das DLLs e a inicialização real do EXE.
4. Compila o instalador Inno Setup, confere seu PE, instala em uma pasta
   temporária, abre o aplicativo instalado e desinstala essa cópia de teste.
5. Guarda relatório, hashes e binários como artefatos do Actions.
6. Publica os arquivos em uma pré-release privada com tag única.

Se a instalação do GitHub App não puder criar/alterar workflows, o envio dessa
etapa será bloqueado pela API. Isso deve ser informado ao usuário; não se deve
alegar que o `.exe` foi produzido. Os fontes e scripts de build continuam válidos
para compilação local. O workflow usa apenas o `GITHUB_TOKEN` temporário da própria
execução e não inclui credenciais pessoais no código.

O relatório `windows-build-report.json` diferencia o smoke test do executável de
OAuth e downloads Google reais. Nenhum teste com conta real deve ser declarado
concluído sem ter sido executado. Testes de rede usam um servidor HTTP local com
respostas simuladas, interrupções e suporte a Range; veja o relatório de testes
da execução específica para a cobertura e os resultados efetivos.

## Licenças

Código do aplicativo sob MIT. Consulte `LICENSE`, `THIRD_PARTY.md` e `licenses/`
antes de redistribuir. Os dados locais de configuração e credenciais ficam fora
da pasta de instalação e são preservados em atualizações e na desinstalação.
