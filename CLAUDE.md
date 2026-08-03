# Podcast Diário de Economia e Geopolítica — Instruções do Projeto

## O que é

Pipeline automatizado que roda de madrugada no PC local (RTX 4070), gera um episódio
diário em formato de diálogo entre duas vozes ("Maria" e "Pedro") cobrindo notícias de
economia e geopolítica, produz o áudio, e entrega via feed RSS privado assinado no app
Podcasts do iOS. Pronto para ouvir de manhã.

Inspirado num projeto que Gustavo Guanabara comentou em vídeo (notícias de IA viradas em
diálogo de chatbot + áudio diário).

## Restrições e decisões já validadas (não reabrir sem motivo novo)

- **Orçamento: até R$50/mês.** Estimativa atual do pipeline: R$3-15/mês, com folga.
- **Prioridade #1 é qualidade do conteúdo**, não velocidade de implementação nem
  simplicidade de manutenção. Prefira a solução mais robusta/precisa mesmo que dê mais
  trabalho de configurar.
- **Já rodou um council de decisão (5 conselheiros com ângulos diferentes) sobre esse
  projeto.** Conclusões principais que moldam este documento:
  - Fontes premium (Reuters/Bloomberg/FT) NÃO são viáveis num pipeline automatizado —
    são pagas, com paywall, e os ToS não liberam scraping/republicação automatizada.
  - RSS de grandes veículos brasileiros é gratuito mas tecnicamente "só para uso pessoal
    não comercial, sem editar o conteúdo" — para uso 100% pessoal (não publicado), o
    risco é baixo, mas o pipeline deve SINTETIZAR/ANALISAR a partir de resumos/manchetes,
    nunca reproduzir texto de artigo inteiro.
  - "Análise profunda" automatizada tem tensão real com "diário e sem intervenção
    humana" — a mitigação escolhida foi: focar em 2-4 temas recorrentes em vez de tentar
    cobrir tudo, e reservar revisão humana leve periódica (não a cada episódio).
  - WhatsApp automático foi descartado como canal de entrega — usar RSS feed privado no
    app Podcasts nativo do iOS é gratuito, robusto, e não depende da API do WhatsApp
    Business.
  - Diálogo de duas vozes (em vez de narração de voz única) foi mantido como decisão do
    usuário, mesmo sabendo que dobra o custo de TTS — mitigado usando TTS local (ver
    abaixo), que torna esse custo irrelevante.

## Arquitetura (5 etapas, pipeline noturno via cron/Task Scheduler)

1. **Coleta (grátis, RSS)**
   Fontes validadas: InfoMoney (economia/política/mundo), Estadão (economia/Brasil/
   internacional), Agência Brasil/EBC, Banco Central do Brasil, IBGE, FGV/IBRE, BBC
   Economia, Investing.com (já usado hoje pelo usuário). Usar `feedparser` em Python.

2. **Resumo/filtragem (LOCAL, Ollama na RTX 4070, custo R$0)**
   Modelo sugerido: Llama 3.1 8B (Q4_K_M) ou Qwen 2.5 14B — cabe nos 12GB de VRAM da
   4070. Função: resumir cada notícia bruta, descartar duplicatas/irrelevantes, extrair
   3-5 temas do dia. NÃO é a etapa de análise profunda — é limpeza e triagem.

3. **Síntese e roteiro (API PAGA barata, ~R$0,10-0,50/dia)**
   Um modelo mais forte (ex: Claude Haiku, GPT-4o-mini, via API — NÃO Claude Pro/
   assinatura, que é produto separado e não deve ser usado aqui) pega os resumos
   filtrados da etapa 2 e escreve o roteiro de diálogo Maria/Pedro. É aqui que a
   qualidade analítica de verdade entra — não delegar essa etapa ao modelo local.
   IMPORTANTE (copyright): o roteiro deve ser síntese/análise em linguagem própria a
   partir dos resumos, nunca reprodução de trechos de artigos originais.

4. **Áudio (LOCAL, biblioteca Python, custo R$0)**
   TTS via Kokoro (`pip install kokoro-onnx`, roda em CPU ou GPU, Apache 2.0, tem vozes
   em Português Brasileiro). Alternativas caso a qualidade em PT-BR não convença:
   Coqui XTTS v2 (melhor para clonagem de voz, mas licença de modelo não-comercial) ou
   Chatterbox (MIT, bateu ElevenLabs em teste cego, mas focado em inglês). Testar Kokoro
   primeiro. Concatenar as falas na ordem certa com `pydub`.

5. **Entrega (grátis)**
   Subir o mp3 gerado para um RSS feed pessoal (ex: GitHub Pages ou Cloudflare R2 free
   tier) e assinar esse feed no app Podcasts nativo do iOS. Sem WhatsApp, sem custo de
   hospedagem.

## Protótipo já validado (referência)

Um roteiro piloto de ~330 palavras já foi escrito manualmente (notícias reais de
3/agosto/2026: Boletim Focus, Ibovespa, Copom, trégua comercial EUA-China) e usado como
teste de formato. Ver se esse roteiro está anexado ao projeto; se não, pode ser
regenerado a partir do padrão de diálogo Maria/Pedro nele usado.

## Ordem de implementação sugerida

1. Script de coleta RSS (etapa 1) — mais simples, sem dependências pesadas
2. Setup do Ollama local + prompt de resumo/filtragem (etapa 2)
3. Integração com API paga para o roteiro final (etapa 3)
4. Setup do Kokoro + concatenação de áudio (etapa 4) — testar qualidade de voz PT-BR
   antes de seguir
5. Publicação do RSS feed pessoal (etapa 5)
6. Automação via cron/Task Scheduler rodando de madrugada

## O que evitar

- Não usar ElevenLabs ou outra API de TTS paga como solução final — só serviu para o
  teste piloto inicial. A solução de produção é TTS local.
- Não tentar consumir Reuters/Bloomberg/FT diretamente — não é viável no orçamento nem
  nos termos de uso.
- Não reproduzir texto de artigos originais no roteiro — sempre sintetizar/parafrasear.
- Não usar WhatsApp Business API para entrega.
