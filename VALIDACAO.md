# Validação — Driver Downloader 2

## Nova identidade

Execução local em Windows 11 x64 em 18/11/2026: **54 testes passaram** com
OpenSSL disponível no PATH. O build PyInstaller (Python 3.13.17) iniciou o EXE
com `--smoke-test`; o instalador Inno Setup 6.7.3 compilou e foi verificado
como PE. O teste de instalação silenciosa não foi executado localmente porque
há uma instalação anterior no mesmo `AppId`; será executado no runner isolado.
OAuth e downloads reais com conta Google não foram executados.

Os nomes do aplicativo e dos artefatos mudaram; o `AppId` do instalador, sua
pasta de instalação legada e `%LOCALAPPDATA%\DriveBaixador` permanecem os mesmos
para conservar atualizações, preferências, histórico e credenciais OAuth.
O build Windows verifica o recurso PNG incluído no pacote e usa o ICO oficial
no executável e instalador. Em máquinas com instalação anterior, o teste local
de instalação é omitido; o runner limpo de Actions o executa.

## Histórico de validação anterior

Execução local em 11/11/2026: Python 3.12.14, PySide6/Qt 6.12.0,
Requests 2.34.2, Linux x64. **51 testes passaram e 1 foi pulado** (DPAPI exclusivo
do Windows). O teste de abertura da GUI pelo código-fonte também terminou com
status 0; análise estática Ruff F sem erros.

## O que foi efetivamente executado

- Servidor HTTP local real, com downloads de arquivos individuais, múltiplos,
  vazios, nomes com acentos, subpastas e diretórios vazios.
- Transferência real de **256 MiB** para disco, com rastreamento das alocações
  Python confirmando pico inferior a 20 MiB durante a operação. Esse teste não
  representa uma medição de desempenho com o Google nem substitui um teste de
  vídeo de vários gigabytes na conexão do usuário.
- Pausa real: interrupção da conexão, manutenção do parcial e retomada usando
  `Range`/`If-Range`, inclusive após fechar e reiniciar o gerenciador.
- Conexão muito lenta, um byte a cada 50 ms, com pausa concluída em menos de
  dois segundos nos testes locais HTTP e HTTPS com verificação TLS habilitada.
- Queda de conexão seguida de recuperação automática; respostas que ignoram
  Range ou retornam uma faixa inválida não são concatenadas ao parcial.
- Cancelamento preservando a versão completa anterior no destino; continuação
  posterior; verificação de MD5 e SHA-256; corrupção local do mesmo tamanho;
  reaproveitamento de documentos exportados concluídos; alteração da versão
  remota e isolamento dos parciais de filas diferentes.
- Falta de espaço simulada, permissão negada simulada, URLs inválidas,
  resource keys, nomes duplicados/incompatíveis, permissões `canDownload`,
  paginação da API, documentos não exportáveis e renovação após HTTP 401.
- Renderização efetiva da interface Qt, navegação, controles conectados ao
  gerenciador, preferências, limite de velocidade após reiniciar e percentual
  abaixo de 100% enquanto a integridade ainda está sendo conferida.
- Scripts de distribuição e blocos do workflow analisados pelo parser real do
  PowerShell 7.4.6. Verificador de PE testa a rejeição de arquivos falsos,
  arquitetura errada e dependências ausentes.

## Windows e distribuição

O ambiente local é Linux. A compilação Windows é executada separadamente pelo
workflow `windows-release.yml`, no GitHub Actions. Os resultados definitivos
dessa execução estão no relatório `windows-build-report.json` e no XML de testes
da execução correspondente. A publicação falha se os testes, a geração do PE,
a presença de DLLs, a abertura do EXE ou a instalação temporária não passarem.

Uma pré-release identifica uma compilação gerada e validada automaticamente;
não significa que todos os cenários com contas Google foram exercitados.

## Ainda depende de uma conta Google real

Não foram usados tokens nem arquivos da conta do usuário. OAuth interativo,
vídeos reais dos clientes, pastas públicas/privadas com restrições específicas,
cotas reais, políticas corporativas e desempenho da rede do usuário exigem
validação com a conta autorizada. Os testes da API usaram dados sintéticos.

No primeiro uso, valide uma pasta pequena, um vídeo grande e pausa/continuação.
Nenhum teste ou build precisa de senha, refresh token ou chave privada no código
ou nos segredos do repositório.
