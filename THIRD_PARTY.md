# Componentes de terceiros

| Componente | Licença e fontes |
|---|---|
| Python | [PSF](https://docs.python.org/3/license.html) |
| PySide6 / Shiboken / Qt | [LGPLv3, GPLv3 ou comercial, conforme o módulo](https://doc.qt.io/qtforpython-6/licenses.html) |
| Requests | [Apache 2.0](https://github.com/psf/requests/blob/main/LICENSE) |
| Rclone | [MIT](https://github.com/rclone/rclone/blob/master/COPYING) |
| PyInstaller (build) | [GPL com exceção para executáveis gerados](https://pyinstaller.org/en/stable/license.html) |
| Inno Setup (build) | [Licença Inno Setup](https://jrsoftware.org/files/is/license.txt) |
| pytest (desenvolvimento) | MIT |

O código do aplicativo é MIT; isso não substitui as licenças dos componentes.
O modo `--onedir` mantém as DLLs Qt e as extensões PySide separadas do executável.
As bibliotecas podem ser substituídas por versões compatíveis. Não é imposta
restrição adicional à engenharia reversa necessária para depurar modificações
nessas bibliotecas. Textos GPLv3 e LGPLv3 estão em `licenses/`; a licença do
Rclone acompanha `bin/rclone.exe`. Avisos e licenças adicionais distribuídos nos
pacotes Python são preservados pelo PyInstaller quando coletados pelo hook.

`licenses/RCLONE-LICENSE.txt` reproduz o arquivo oficial
[`COPYING` da versão 1.76.0](https://github.com/rclone/rclone/blob/v1.76.0/COPYING).
É usado também quando o ZIP oficial para Windows não contém o texto da licença.

Para redistribuir publicamente, confira as obrigações das licenças e ofereça os
fontes correspondentes dos componentes LGPL utilizados. Fontes oficiais:
[Qt](https://download.qt.io/official_releases/qt/),
[Qt for Python](https://download.qt.io/official_releases/QtForPython/),
[Rclone](https://github.com/rclone/rclone),
[Requests](https://github.com/psf/requests).
