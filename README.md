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
| 2 | Resumo/triagem (Ollama) | **exige GPU** | R$0 | esqueleto |
| 3 | Síntese do roteiro (API paga) | qualquer máquina | ~R$0,50/dia | **implementada** |
| 4 | Áudio (Kokoro TTS) | **exige GPU/modelo** | R$0 | esqueleto |
| 5 | Publicação do feed | qualquer máquina | R$0 | esqueleto |

Cada etapa lê o JSON da anterior e escreve o próprio em `data/`, então dá para
rodar e inspecionar cada uma isoladamente.

---

## ⚠️ Dois ambientes diferentes

Este projeto roda em duas máquinas com propósitos distintos. **Não misture os
setups** — o de desenvolvimento não instala nada de GPU de propósito.

| | Máquina de desenvolvimento | PC de destino (produção) |
|---|---|---|
| Hardware | sem GPU | RTX 4070, 12 GB VRAM |
| Para que serve | escrever código, rodar testes, versionar | rodar o pipeline de madrugada |
| Instala | `requirements-dev.txt` | `requirements.txt` |
| Etapas que rodam | 1, 3 e os testes | todas |
| Ollama / Kokoro | **não** | sim |

---

## Setup A — máquina de desenvolvimento (sem GPU)

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
> rodar `summarize` / `audio`. Não vai funcionar sem GPU e não é o objetivo desta
> máquina.

---

## Setup B — PC de destino (RTX 4070)

Este é o setup completo, onde o pipeline realmente roda. Faça na ordem.

### 1. Repositório e dependências

```bash
git clone git@github.com:JoaoCarlos134/News_radio.git
cd News_radio
python -m venv .venv
```

Ative o ambiente e instale **tudo** (inclui as dependências de áudio):

```bash
pip install -r requirements.txt
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

> **Antes de investir na etapa 4:** gere um trecho de teste e **ouça**. O CLAUDE.md
> prevê trocar o Kokoro por Coqui XTTS v2 ou Chatterbox se a qualidade em PT-BR
> não convencer. Não vale otimizar essa etapa antes dessa decisão.

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

| Modelo (`SCRIPT_MODEL`) | Por dia | Por mês | Observação |
|---|---|---|---|
| `claude-sonnet-5` | ~R$0,50 | ~R$15 | **padrão** — melhor análise |
| `claude-haiku-4-5` | ~R$0,16 | ~R$5 | alternativa mais barata |

Estimativa para ~20 mil tokens de entrada e ~2 mil de saída por episódio. Como o
CLAUDE.md define qualidade de conteúdo como prioridade #1 e é na etapa 3 que a
análise acontece, o padrão é o modelo mais forte — ainda com folga grande no
orçamento. Para trocar, basta editar `SCRIPT_MODEL` no `.env`.

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
  stage2_summarize.py  etapa 2 — esqueleto (comentado com o plano)
  stage3_script.py   etapa 3 — IMPLEMENTADA
  stage4_audio.py    etapa 4 — esqueleto
  stage5_publish.py  etapa 5 — esqueleto
  cli.py             interface de linha de comando

tests/               101 testes; rodam sem GPU, sem rede e sem chave de API
data/                saída do pipeline (ignorado pelo Git)
models/              modelos do Kokoro (ignorado pelo Git)
```

Os esqueletos das etapas 2, 4 e 5 não são arquivos vazios: cada um traz no
docstring o plano de implementação, a API a usar e as armadilhas conhecidas.

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
