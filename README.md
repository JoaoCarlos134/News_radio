# Podcast Diário de Economia e Geopolítica

Pipeline automatizado que roda de madrugada, transforma notícias de economia e
geopolítica em um episódio de podcast em diálogo entre duas vozes (Maria e Pedro),
gera o áudio localmente e publica num feed RSS privado para ouvir no app Podcasts
do iOS.

As decisões de arquitetura e as restrições do projeto estão em [CLAUDE.md](CLAUDE.md).

---

## As 5 etapas

| # | Etapa | Onde roda | Custo | Status |
|---|-------|-----------|-------|--------|
| 1 | Coleta RSS | qualquer máquina | R$0 | **implementada** |
| 2 | Resumo/triagem (Ollama) | **exige GPU** | R$0 | **implementada** |
| 3 | Síntese do roteiro (API paga) | qualquer máquina | ~R$0,50/dia | **implementada** |
| 4 | Áudio (Kokoro TTS) | **exige GPU/modelo** | R$0 | **implementada** |
| 5 | Publicação do feed | qualquer máquina | R$0 | esqueleto |

Cada etapa lê o JSON da anterior e escreve o próprio em `data/`, então dá para
rodar e inspecionar cada uma isoladamente.

---

## ⚠️ Duas máquinas, ambas de desenvolvimento

O código é desenvolvido nas **duas** máquinas — o repositório é sincronizado por
Git e qualquer uma delas pode escrever código, rodar os testes e commitar. A
diferença é só o que cada uma **consegue executar**, e qual delas roda o pipeline
de verdade de madrugada.

| | Máquina sem GPU | Máquina com RTX 4070 |
|---|---|---|
| Hardware | sem GPU | RTX 4070, 12 GB VRAM |
| Desenvolvimento | sim | sim |
| Roda o pipeline em produção (madrugada) | não | **sim** |
| Instala | `requirements-dev.txt` | `requirements.txt` + `requirements-dev.txt` |
| Etapas que consegue executar | 1, 3 e os testes | todas |
| Ollama / Kokoro | não | sim |

Os testes rodam nas duas — é isso que mantém o desenvolvimento possível na
máquina sem GPU. Ao implementar as etapas 2, 4 e 5, mantenha a injeção de
dependência (ver [Desenvolvimento](#desenvolvimento)) para que a suíte continue
verde nos dois lados.

---

## Setup A — máquina sem GPU (só desenvolvimento)

Aqui você escreve código e roda testes. Nada de GPU, nada de modelos baixados.

```bash
git clone git@github.com:JoaoCarlos134/News_radio.git
cd News_radio
python -m venv .venv
```

Ative o ambiente virtual:

```bash
.venv\Scripts\activate
```

Instale as dependências de desenvolvimento:

```bash
pip install -r requirements-dev.txt
```

Rode os testes — todos passam sem GPU, sem rede e sem chave de API:

```bash
python -m pytest
```

Colete notícias de verdade (etapa 1 não precisa de credencial nenhuma):

```bash
python -m podcast.cli collect --dry-run
```

Para exercitar a **etapa 3** daqui, copie o `.env.example` para `.env` e
preencha só o `ANTHROPIC_API_KEY`. O resto pode ficar em branco.

```bash
python -m podcast.cli collect
python -m podcast.cli script --from-raw data/raw/AAAA-MM-DD.json --show
```

`--from-raw` pula a etapa 2 (que exige GPU) e alimenta o roteiro direto com a
coleta bruta. Serve para testar a chamada da API paga aqui; **não é o caminho de
produção** — sem a triagem da etapa 2 a qualidade do roteiro cai.

> **Não tente aqui:** instalar Ollama, baixar modelos de LLM, instalar Kokoro ou
> rodar `summarize` / `audio`. Sem GPU essas etapas não executam — o que não
> impede desenvolvê-las aqui: escreva o código e os testes com o cliente
> injetado, e valide a execução real na máquina com a 4070.

---

## Setup B — máquina com RTX 4070 (desenvolvimento + produção)

Setup completo: desenvolve como a outra máquina **e** roda o pipeline de
madrugada. Faça na ordem.

### 1. Repositório e dependências

> **Use Python 3.13.** O `kokoro-onnx` (etapa 4) ainda declara
> `Requires-Python >=3.10,<3.14`, então num venv de Python 3.14 o
> `pip install -r requirements.txt` falha em `kokoro-onnx`. As etapas 1, 2, 3 e 5
> funcionam no 3.14; a 4 não.
>
> No 3.13 o `pydub` também precisa do backport `audioop-lts` — o módulo
> `audioop` saiu da stdlib no 3.13 (PEP 594). Já está no
> `requirements-audio.txt` com marcador de versão, então o `pip install` resolve
> sozinho; só não estranhe a dependência extra.

```bash
git clone git@github.com:JoaoCarlos134/News_radio.git
cd News_radio
py -3.13 -m venv .venv
```

Ative o ambiente e instale **tudo** — produção (inclui áudio) mais as
ferramentas de desenvolvimento, já que aqui também se escreve código:

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

### 2. ffmpeg (necessário para exportar mp3)

O `pydub` precisa do ffmpeg no PATH. No Windows, com winget:

```bash
winget install Gyan.FFmpeg
```

Confirme que está no PATH (abra um terminal novo depois de instalar):

```bash
ffmpeg -version
```

### 3. Ollama + modelo local (etapa 2)

Baixe e instale o Ollama em <https://ollama.com/download>. Depois puxe o modelo.
O CLAUDE.md sugere Qwen 2.5 14B ou Llama 3.1 8B — ambos cabem nos 12 GB da 4070:

```bash
ollama pull qwen2.5:14b-instruct-q4_K_M
```

Confirme que o serviço responde:

```bash
ollama list
```

Se escolher outro modelo, ajuste `OLLAMA_MODEL` no `.env`.

### 4. Kokoro TTS (etapa 4)

Baixe os dois arquivos do modelo para a pasta `models/` na raiz do repositório
(ela está no `.gitignore` — os arquivos não vão para o Git):

- `kokoro-v1.0.onnx`
- `voices-v1.0.bin`

Ambos estão nas releases do projeto: <https://github.com/thewh1teagle/kokoro-onnx/releases>

As vozes em português brasileiro do Kokoro v1.0 são `pf_dora` (feminina),
`pm_alex` e `pm_santa` (masculinas). Estão configuradas em `KOKORO_VOICE_MARIA`
e `KOKORO_VOICE_PEDRO`.

**A Maria usa `ef_dora`, não `pf_dora`.** É a mesma locutora no pack espanhol do
Kokoro, com um vetor de estilo melhor treinado; como a fonetização continua
`pt-br`, os termos brasileiros saem corretos e o timbre soa melhor. Isso foi
decidido ouvindo seis variantes lado a lado — não troque sem repetir o teste.

### 5. Chave da API paga (etapa 3)

Crie a chave em <https://console.anthropic.com/settings/keys> e coloque no `.env`.

### 6. Configuração

```bash
cp .env.example .env
```

Edite o `.env`. O `.env.example` documenta cada variável. O mínimo a preencher:

| Variável | Para quê |
|---|---|
| `ANTHROPIC_API_KEY` | etapa 3 — chave da API paga |
| `OLLAMA_MODEL` | etapa 2 — só se usar modelo diferente do padrão |
| `PODCAST_BASE_URL` | etapa 5 — URL pública onde o feed vai ficar |
| `PODCAST_AUTHOR`, `PODCAST_EMAIL` | etapa 5 — metadados do feed |

Os caminhos do Kokoro já apontam para `./models/` e são resolvidos a partir da
raiz do repositório — **não use caminhos absolutos** no `.env`.

### 7. Verifique tudo de uma vez

```bash
python -m podcast.cli doctor
```

Esse comando não executa nenhuma etapa: só diz o que ainda falta instalar ou
configurar, etapa por etapa. Rode-o até tudo aparecer como `[ok]`.

---

## Uso

```bash
python -m podcast.cli doctor      # diagnostica o ambiente
python -m podcast.cli sources     # lista as fontes RSS
python -m podcast.cli sources --check   # testa quais feeds respondem agora

python -m podcast.cli collect     # etapa 1
python -m podcast.cli summarize   # etapa 2  (exige GPU)
python -m podcast.cli script      # etapa 3  (exige chave de API)
python -m podcast.cli audio       # etapa 4  (exige Kokoro)
python -m podcast.cli publish     # etapa 5

python -m podcast.cli run         # as 5 em sequência — é isto que o agendador chama
```

Flags úteis:

- `collect --dry-run` — mostra na tela sem gravar nem marcar como visto
- `script --from-raw ARQUIVO` — pula a etapa 2 (teste sem GPU)
- `script --show` — imprime o roteiro gerado
- `-v` — log detalhado

---

## Custo

O orçamento do projeto é R$50/mês. Só a etapa 3 custa dinheiro.

Preço de tabela por milhão de tokens: `claude-sonnet-5` US$3 entrada / US$15
saída (promocional US$2 / US$10 até 31/08/2026); `claude-haiku-4-5` US$1 / US$5.

| Modelo (`SCRIPT_MODEL`) | Por dia | Por mês | Observação |
|---|---|---|---|
| `claude-sonnet-5` | US$0,165 | **~R$27** | **padrão** — melhor análise |
| `claude-sonnet-5` (promocional) | US$0,110 | ~R$18 | até 31/08/2026 |
| `claude-haiku-4-5` | US$0,023 | ~R$4 | testado e rejeitado, ver abaixo |

Números **medidos**, não estimados: um episódio real de 4/ago/2026 consumiu
4145 tokens de entrada e 10141 de saída no Sonnet (o thinking adaptativo é
cobrado como saída e responde por boa parte disso). Conversão a R$5,50/US$ —
**confira o câmbio antes de tratar como firme**.

> **O Haiku custa um sexto e não compensa.** No mesmo digest ele inventou uma
> equivalência aritmética errada ("um vírgula oito por cento de um mês = dois
> dias de trabalho"; são meio dia), variou de 1753 a 2614 palavras entre
> execuções idênticas, e escorregou no português. Num programa que existe para
> explicar economia, número inventado é o defeito que não dá para aceitar. Fica
> como plano B se o orçamento apertar.

> **Não troque para `claude-opus-5`** sem refazer a conta: a US$5/US$25 o
> episódio passa de R$60/mês e estoura o orçamento de R$50.

Para trocar, basta editar `SCRIPT_MODEL` no `.env`.

As etapas 2 e 4 rodam localmente e custam R$0, o que é justamente o motivo de o
diálogo de duas vozes ser viável (dobra o volume de TTS, mas TTS local é grátis).

---

## Automação (madrugada)

No Windows, use o Agendador de Tarefas apontando para o Python do venv:

```
Programa:   C:\caminho\para\News_radio\.venv\Scripts\python.exe
Argumentos: -m podcast.cli run
Iniciar em: C:\caminho\para\News_radio
```

Configure para rodar diariamente por volta das 5h, com "Executar estando o
usuário conectado ou não". O campo "Iniciar em" é obrigatório — sem ele os
caminhos relativos do `.env` não resolvem.

---

## Estrutura

```
podcast/
  config.py          configuração via .env; resolve caminhos a partir da raiz do repo
  models.py          dataclasses trocadas entre etapas (+ serialização JSON)
  sources.py         registro das fontes RSS
  textutils.py       limpeza de HTML, canonicalização de URL, similaridade de título
  stage1_collect.py  etapa 1 — IMPLEMENTADA
  stage2_summarize.py  etapa 2 — IMPLEMENTADA
  stage3_script.py   etapa 3 — IMPLEMENTADA
  stage4_audio.py    etapa 4 — IMPLEMENTADA
  stage5_publish.py  etapa 5 — esqueleto
  cli.py             interface de linha de comando

tests/               195 testes; rodam sem rede e sem chave de API. Os que
                     exigem numpy/pydub/ffmpeg se auto-pulam na máquina sem GPU
data/                saída do pipeline (ignorado pelo Git)
models/              modelos do Kokoro (ignorado pelo Git)
```

O esqueleto da etapa 5 não é um arquivo vazio: traz no docstring o plano de
implementação, a API a usar e as armadilhas conhecidas.

---

## Notas de conteúdo e uso

- **Copyright.** O pipeline usa apenas manchetes e resumos publicados nos próprios
  feeds, e sintetiza em linguagem própria. Nunca reproduz texto de artigo original.
  Isso está codificado no prompt da etapa 3 e no limite de tamanho da etapa 1.
- **Uso pessoal.** O feed é privado por obscuridade da URL — qualquer um com o
  link consegue ouvir. Não submeta a URL a diretórios de podcast.
- **Revisão humana.** O CLAUDE.md prevê revisão leve periódica, não a cada
  episódio. Vale ouvir alguns episódios por semana e ajustar o prompt da etapa 3.

---

## Desenvolvimento

```bash
python -m pytest              # tudo
python -m pytest -v           # verboso
python -m pytest tests/test_stage1_collect.py
```

Toda etapa que fala com um serviço externo (rede, Ollama, API paga) recebe o
cliente por injeção, para que os testes rodem sem esse serviço. Siga esse padrão
ao implementar as etapas 2, 4 e 5 — é o que mantém a suíte executável na máquina
de desenvolvimento.
