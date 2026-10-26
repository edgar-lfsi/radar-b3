"""Gera o relatorio em Markdown a partir dos dados coletados."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
RAW = ROOT / "data" / "raw"


def brl(x: float) -> str:
    if pd.isna(x):
        return "—"
    if abs(x) >= 1e9:
        return f"R$ {x/1e9:,.2f} bi".replace(",", "X").replace(".", ",").replace("X", ".")
    if abs(x) >= 1e6:
        return f"R$ {x/1e6:,.1f} mi".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {x:,.0f}".replace(",", ".")


def pct(x: float) -> str:
    return "—" if pd.isna(x) else f"{x:+.1f}%".replace(".", ",")


def num(x: float) -> str:
    return "—" if pd.isna(x) else f"{x:,.0f}".replace(",", ".")


def table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


SECTOR_PT = {
    "Finance": "Financeiro",
    "Retail Trade": "Varejo",
    "Consumer Services": "Serviços ao consumidor",
    "Transportation": "Transporte",
    "Utilities": "Energia elétrica e saneamento",
    "Energy Minerals": "Petróleo e gás",
    "Non-Energy Minerals": "Mineração e siderurgia",
    "Process Industries": "Papel, celulose e química",
    "Producer Manufacturing": "Bens de capital",
    "Electronic Technology": "Defesa e eletrônicos",
    "Consumer Non-Durables": "Consumo não durável",
    "Consumer Durables": "Bens de consumo duráveis",
    "Health Services": "Saúde",
    "Health Technology": "Saúde (tecnologia)",
    "Technology Services": "Tecnologia",
    "Commercial Services": "Serviços comerciais",
    "Industrial Services": "Serviços industriais",
    "Distribution Services": "Distribuição",
    "Communications": "Telecom e comunicações",
    "Miscellaneous": "Outros",
}


def sec(name: str) -> str:
    return SECTOR_PT.get(name, name)


live = pd.DataFrame(json.load(open(RAW / "live_brapi.json", encoding="utf-8")))
live = live[~live["stock"].str.endswith("F")].copy()
live["vol_fin"] = live["volume"] * live["close"]
live = live.sort_values("vol_fin", ascending=False).reset_index(drop=True)

live_top = pd.read_csv(OUT / "live_top.csv")
fundos = pd.DataFrame(json.load(open(RAW / "live_fundos.json", encoding="utf-8")))
fundos["vol_fin"] = fundos["volume"] * fundos["close"]
fundos = fundos.set_index("stock")

rank = pd.read_csv(OUT / "ranking_volume.csv")
neg = pd.read_csv(OUT / "ranking_negocios.csv")
setores = pd.read_csv(OUT / "live_setores.csv")
blk = pd.read_csv(OUT / "block_trades.csv")
blue = pd.read_csv(OUT / "blue_chips.csv")
pesos = pd.read_csv(OUT / "ibov_pesos.csv")
resumo = json.loads((OUT / "resumo.json").read_text(encoding="utf-8"))

nomes = dict(zip(live["stock"], live["name"]))
rank["nome"] = rank["Ativo"].map(nomes).fillna(rank["CrpnNm"])
neg["nome"] = neg["Ativo"].map(nomes).fillna(neg["CrpnNm"])
blk["nome"] = blk["Ativo"].map(nomes).fillna("")

# ---------------------------------------------------------------- tabelas
t_hoje = table(
    ["#", "Ativo", "Setor", "Preço", "Var. dia", "Volume fin. (est.)", "vs. sexta"],
    [[i + 1, r.stock, sec(r.sector), f"R$ {r.close:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
      pct(r.change), brl(r.vol_fin_est), f"{r.vol_rel_sexta:,.1f}x".replace(".", ",") if pd.notna(r.vol_rel_sexta) else "—"]
     for i, r in live_top.head(15).iterrows()],
)

t_sexta = table(
    ["#", "Ativo", "Empresa", "Volume financeiro", "Negócios", "Var. 23/10"],
    [[i + 1, r.Ativo, r.nome, brl(r.volume), num(r.negocios), pct(r.oscilacao)]
     for i, r in rank.head(15).iterrows()],
)

t_neg = table(
    ["#", "Ativo", "Empresa", "Negócios", "Volume financeiro"],
    [[i + 1, r.Ativo, r.nome, num(r.negocios), brl(r.volume)]
     for i, r in neg.head(10).iterrows()],
)

setores["var_pond"] = setores["var_pond"].fillna(setores["var_media"])
t_set = table(
    ["Setor", "Nº de ações", "Variação pond. por volume", "Variação mediana", "Volume fin. (est.)"],
    [[sec(r.sector), int(r.n), pct(r.var_pond), pct(r.var_mediana), brl(r.vol_fin)]
     for _, r in setores[setores.vol_fin > 5e6].iterrows()],
)

liq = live[live.vol_fin > 100e6]
t_alta = table(
    ["Ativo", "Setor", "Var.", "Volume fin. (est.)"],
    [[r.stock, sec(r.sector), pct(r.change), brl(r.vol_fin)]
     for _, r in liq[liq.change > 0].nlargest(10, "change").iterrows()],
)
t_baixa = table(
    ["Ativo", "Setor", "Var.", "Volume fin. (est.)"],
    [[r.stock, sec(r.sector), pct(r.change), brl(r.vol_fin)]
     for _, r in liq.nsmallest(12, "change").iterrows()],
)

t_blk = table(
    ["Ativo", "Negócios", "Preço", "Quantidade", "Volume"],
    [[r.Ativo, int(r.negocios), f"R$ {r.preco:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
      num(r.quantidade), brl(r.volume)]
     for _, r in blk.head(9).iterrows()],
)

blue = blue.sort_values("peso_pct", ascending=False)
t_blue = table(
    ["Ativo", "Peso no IBOV", "Var. hoje", "Volume fin. (est.)", "vs. sexta"],
    [[r.Ativo, f"{r.peso_pct:.2f}%".replace(".", ","), pct(r.change), brl(r.vol_fin_est),
      f"{r.vol_rel_sexta:,.1f}x".replace(".", ",") if pd.notna(r.vol_rel_sexta) else "—"]
     for _, r in blue.head(15).iterrows()],
)

m = pesos.merge(live[["stock", "change"]], left_on="Ativo", right_on="stock", how="left")
m["contrib"] = m.peso_pct / 100 * m.change
t_contrib_neg = table(
    ["Ativo", "Peso", "Var.", "Contribuição"],
    [[r.Ativo, f"{r.peso_pct:.2f}%".replace(".", ","), pct(r.change),
      f"{r.contrib:+.2f}".replace(".", ",") + " p.p."]
     for _, r in m.nsmallest(8, "contrib").iterrows()],
)
t_contrib_pos = table(
    ["Ativo", "Peso", "Var.", "Contribuição"],
    [[r.Ativo, f"{r.peso_pct:.2f}%".replace(".", ","), pct(r.change),
      f"{r.contrib:+.2f}".replace(".", ",") + " p.p."]
     for _, r in m.nlargest(4, "contrib").iterrows()],
)

mediana_ibov = m.change.median()
var_pond = m.contrib.sum()
vol_ratio = f"{resumo['vol_hoje_vs_sexta']:.2f}".replace(".", ",")
var_pond_s = f"{var_pond:.1f}".replace(".", ",")
mediana_s = f"{mediana_ibov:.1f}".replace(".", ",")


def etf_row(ticker: str) -> list[str]:
    r = fundos.loc[ticker]
    return [ticker, pct(r.change), f"{r.volume/1e6:.1f} mi".replace(".", ","), brl(r.vol_fin)]


t_etf = table(["Ativo", "Var.", "Cotas negociadas", "Volume fin. (est.)"],
              [etf_row("BOVA11"), etf_row("SMAL11")])

# ---------------------------------------------------------------- texto
doc = f"""# Análise de mercado B3 — 26/10/2026

> **Snapshot intradiário às {resumo['atualizado_em'][11:]} BRT** (mercado aberto até 17h).
> Sessão de referência completa: **23/10/2026**. Fontes: arquivos abertos da B3, brapi.dev e
> Boletim Diário do Mercado (B3). Não é recomendação de investimento.

## Resumo executivo

- **Dia de estresse extremo:** o Ibovespa operava a **175.624 pontos (-14,4%)**, após mínima de
  **169.516 (-17,3%)** e acionamento de *circuit breaker* na abertura, com a reeleição de Lula
  (25/10) contrariando o posicionamento pró-alternância que dominou outubro.
- **Volume muito acima do normal:** **{brl(resumo['vol_fin_acoes_hoje_parcial'])}** em ações até as
  {resumo['atualizado_em'][11:]} — **{vol_ratio}x** o volume de sexta
  ({brl(resumo['vol_fin_acoes_sexta'])}). Concentração alta: **{resumo['concentracao_top10_hoje']*100:.0f}%**
  do volume nos 10 papéis mais negociados.
- **Quem está sendo mais negociado:** PETR4, ITUB4, VALE3, BPAC11, BBAS3, BBDC4, B3SA3, PETR3,
  SBSP3 e AXIA3 — bancos e Petrobras concentram o volume e a pressão vendedora; VALE3 é a exceção
  compradora.
- **Compra x venda:** não existe "volume de compra" separado de "volume de venda" (toda negociação
  tem dois lados). O que os dados mostram é **pressão de preço** e **fluxo por categoria**: no dia,
  exportadoras (VALE3 +5,5%, SUZB3 +10,2%, EMBJ3 +8,0%, WEGE3 +5,2%) foram compradas e o bloco
  doméstico (bancos, varejo, energia e saneamento, transporte) foi vendido com força.
- **"Mesmo investidor" nos dois lados:** os dados públicos da B3 **não identificam CPF/CNPJ**. O que
  existe de mais próximo são os **negócios em bloco** (bilaterais) e a atividade de **ETFs**
  (criação/resgate). Em 23/10, VALE3 teve R$ 92,9 mi em bloco; hoje BOVA11 e SMAL11 movimentaram
  volumes institucionais relevantes.
- **Blue chips:** a percepção segue intacta em liquidez, mas **o prêmio de risco doméstico subiu**.
  A dispersão dentro do Ibovespa foi de **-56,5% a +10,2%**; o índice caiu menos que a mediana
  (**{mediana_s}%**) porque VALE3 (11,2% do índice) subiu.

## 1. Contexto: o dia do circuit breaker

A eleição presidencial de 25/10 foi decidida pela reeleição de Lula, resultado que o mercado não
precificava: em outubro, o Ibovespa havia subido com a tese de alternância de poder (chegou a
209,6 mil pontos após o 1º turno). Na abertura desta segunda-feira:

- **Ibovespa:** 175.624 pts (-14,4%); mínima de 169.516 (-17,3%); *circuit breaker* acionado com
  queda de 10% e reabertura após 30 minutos.
- **Dólar:** alta de ~12%, perto de **R$ 5,60**.
- **Juros futuros:** alta forte, com mercado precificando deterioração fiscal e Selic mais alta.
- **EWZ** (ETF de ações brasileiras em NY) caiu ~26% no pré-mercado.

**Amplitude:** das **378 ações** em negociação, **{resumo['acoes_baixa']} caíram** e apenas
**{resumo['acoes_alta']} subiram**; a mediana foi de **{mediana_s}%** e
**{resumo['pct_queda_10']*100:.0f}%** dos papéis caíram 10% ou mais.

O movimento não é uniforme: é uma **reprecificação do risco doméstico**, que penaliza empresas
dependentes de juros, crédito e demanda interna, e favorece exportadoras e *commodities*.

## 2. Quais ações estão sendo mais negociadas

### 2.1 Hoje (parcial, até as {resumo['atualizado_em'][11:]})

{t_hoje}

*Volume financeiro estimado = quantidade negociada × último preço. A coluna "vs. sexta" compara com
o volume financeiro da sessão de 23/10.*

Leituras:

- **ITUB4** saltou para o 2º lugar, com **3,2x** o volume de sexta — é o 2º papel mais líquido
  do dia, atrás apenas de PETR4, e virou o centro da disputa.
- **PETR4** lidera em volume, mas caiu menos (-8,7%) que bancos: parte do fluxo foi de *hedge* e
  troca de posição, não de abandono.
- **VALE3** é o grande contraponto: **+5,5%** com **2,2x** o volume, beneficiada pelo dólar e pela
  demanda por *commodities*.
- **BPAC11 (-30,8%)**, **BBAS3 (-28,0%)**, **BBDC4 (-22,2%)** e **B3SA3 (-36,2%)** combinam queda
  violenta com volume 1,5–3,2x acima do normal: é liquidação, não ruído.

### 2.2 Última sessão completa (23/10)

{t_sexta}

Em **número de negócios** (a métrica de "quantas vezes o papel trocou de mãos"), a liderança é de
**BBDC4 (99.249)**, seguida por **PETR4 (96.136)** e **BBAS3 (86.793)**:

{t_neg}

### 2.3 Concentração

- Sexta: os 10 papéis mais negociados concentraram **43,6%** do volume financeiro; os 3 primeiros,
  **20,4%**.
- Hoje: os 10 primeiros concentram **{resumo['concentracao_top10_hoje']*100:.0f}%** — a liquidez
  migrou para os *mega caps* e para os nomes diretamente afetados pela eleição.
- Em quantidade de ações, o ranking muda: **BBDC4 (96,6 mi)** e **MGLU3 (69,3 mi)** lideram, à
  frente de PETR4 (58,5 mi) e B3SA3 (58,4 mi).

## 3. Compradas ou vendidas?

### 3.1 O que os dados permitem (e não permitem) medir

Toda negociação na bolsa tem **um comprador e um vendedor** pelo mesmo valor. Portanto, "volume de
compra" e "volume de venda" são iguais por definição. O que se pode medir é:

1. **Pressão de preço:** fechamento perto da máxima ou da mínima do dia, aceleração no fim do
   pregão, gap de abertura.
2. **Fluxo por categoria de investidor** (estrangeiro, institucional, pessoa física, instituições
   financeiras) — dado agregado de mercado, publicado pela B3 com D+2.
3. **Negócios em bloco:** operações bilaterais, em que se sabe que há um único comprador e um único
   vendedor (mas não quem são).
4. **Posição de aluguel/short** — a B3 publica em base separada (não incluída neste relatório).

Não existe, nos dados abertos, "quanto cada investidor comprou ou vendeu de cada ação".

### 3.2 Setores: o mapa da liquidação

{t_set}

*Variação ponderada pelo volume financeiro do dia; a mediana mostra o comportamento da empresa
"típica" do setor. Em defesa e eletrônicos, por exemplo, a ponderação é +6,2% por causa de EMBJ3,
enquanto a mediana é -26,8% — ou seja, a maioria do setor caiu, mas o volume se concentrou na
exportadora.*

O padrão é nítido: **varejo, consumo, transporte e financeiras** — o núcleo da economia doméstica —
lideram as quedas; **energia e mineração** caem menos; **process industries** (celulose, papel,
química) e **bens de capital/tecnologia exportadora** chegam a subir.

### 3.3 Ações: quem foi comprado e quem foi vendido

**Maiores altas entre papéis com volume acima de R$ 100 mi:**

{t_alta}

**Maiores quedas entre papéis com volume acima de R$ 100 mi:**

{t_baixa}

A leitura é de **duas pontas bem definidas**:

- **Ponta compradora:** exportadoras e *commodities* — SUZB3, EMBJ3, VALE3, WEGE3, KLBN11, GGBR4.
  O dólar mais alto melhora receita em reais e competitividade; o investidor busca proteção.
- **Ponta vendedora:** empresas alavancadas e dependentes de demanda interna — ECOR3, MOVI3, MGLU3,
  VAMO3, CEAB3, CSAN3, HAPV3, CYRE3, RENT3, ASAI3. Aqui o risco é juros altos + crédito + consumo.

### 3.4 Fluxo por tipo de investidor (dado oficial mais recente: 21/10)

| Categoria | Outubro até 21/10 | Último dia (21/10) |
|---|---:|---:|
| Estrangeiro | **+R$ 20,16 bi** | +R$ 157 mi |
| Institucional | **-R$ 10,87 bi** | -R$ 753 mi |
| Pessoa física | **-R$ 4,87 bi** | +R$ 970 mi |
| Instituições financeiras | -R$ 396 mi | -R$ 430 mi |
| Outros | -R$ 4,02 bi | +R$ 56 mi |

O dado é **anterior à eleição** e mostra o posicionamento que agora está sendo desfeito: o
estrangeiro comprou R$ 20 bi em outubro, movimento consistente com a tese de alternância de poder
que dominava o mercado. Com a reeleição, é razoável esperar **reversão (ao menos parcial) desse
fluxo** — os dados de 26/10 só estarão disponíveis em D+2 (28/10). É o principal indicador a
acompanhar nos próximos dias.

## 4. Ações com grande volume de compra e venda pelo mesmo investidor

### 4.1 O limite dos dados públicos

A B3 **não divulga** a identidade de compradores e vendedores por ação. O antigo "maiores
compradores e vendedores" por corretora não aparece nos arquivos abertos consultados (API de
arquivos e Boletim Diário do Mercado). Portanto, não é possível afirmar, com dados públicos, que
"o investidor X comprou e vendeu Y ações de Z".

O que existe de mais próximo:

1. **Negócios em bloco** (`Equity Block Trading Lot`): cada operação tem um comprador e um vendedor
   institucionais, geralmente com liquidação bilateral. É o retrato mais direto de "grande volume
   dos dois lados".
2. **ETFs e criação/resgate:** movimentos de BOVA11/SMAL11 envolvem poucos participantes
   institucionais fazendo compra e venda simultâneas (arbitragem/criação de cotas).
3. **Formulários da CVM:** negociações de administradores e acionistas controladores são públicas,
   mas cobrem apenas *insiders*, não o mercado em geral.

### 4.2 Negócios em bloco de 23/10

{t_blk}

Destaques: **VALE3** teve 1,5 milhão de ações em 3 blocos (R$ 92,9 mi, preço médio R$ 61,93);
**B3SA3**, 2,3 milhões de ações (R$ 53,3 mi); **RDOR3**, 940 mil ações (R$ 41,0 mi). São operações
tipicamente institucionais — o mesmo fundo pode aparecer comprando em um bloco e vendendo em outro
para ajustar carteira.

### 4.3 Hoje: ETFs no centro

{t_etf}

Esses volumes, somados aos blocos de VALE3/PETR4, são o que mais se aproxima de "um mesmo investidor
comprando e vendendo grande volume": são poucos participantes institucionais, com operações de
grande porte e, muitas vezes, dos dois lados do mercado.

## 5. Percepção de Blue Chips

### 5.1 Pesos e comportamento no dia

O Ibovespa tem **76 ações** na carteira vigente (set–dez/2026). Os maiores pesos e o comportamento
hoje:

{t_blue}

### 5.2 Contribuição para a queda do índice

**Pesos que mais puxaram o índice para baixo:**

{t_contrib_neg}

**Pesos que seguraram o índice:**

{t_contrib_pos}

A variação ponderada pelos pesos é de **{var_pond_s}%**, contra mediana de **{mediana_s}%**
entre as 76 ações (cálculo aproximado com os pesos teóricos; o índice oficial caía **-14,4%** no
mesmo horário). Ou seja: **a ação "típica" do Ibovespa caiu bem mais que o índice**, e o índice só
não caiu mais porque VALE3 (11,2% do total) subiu.

### 5.3 O que isso diz sobre a percepção dos blue chips

1. **Blue chip ≠ porto seguro.** O rótulo continua valendo para liquidez e tamanho, mas hoje o
   mercado separou **blue chips domésticos** (bancos, utilities, varejo) de **blue chips
   exportadores** (Vale, Suzano, Embraer, WEG). Os primeiros estão sendo tratados como risco fiscal;
   os segundos, como proteção cambial.
2. **O prêmio de risco subiu, não a qualidade.** B3SA3 (-36%), BPAC11 (-31%) e BBAS3 (-28%) não
   caíram por deterioração operacional no fim de semana; caíram porque o mercado exige agora um
   retorno maior para carregar risco Brasil. É reprecificação de *discount rate*, não de lucro.
3. **Liquidez segue concentrada.** Os 10 papéis mais negociados respondem por 51% do volume. Em
   momentos de estresse, o investidor corre para os nomes que consegue vender — o que explica o
   volume anormal em ITUB4, PETR4 e VALE3.
4. **O que monitorar:** fluxo estrangeiro (D+2), curva de juros, dólar e as primeiras sinalizações
   da equipe econômica. Se o fluxo estrangeiro virar para saída, a pressão sobre os blue chips
   domésticos continua; se houver entrada seletiva, exportadoras devem seguir como preferidas.

### 5.4 O que observar daqui para frente

1. **Fluxo estrangeiro de 26/10 (divulgação em 28/10):** é o dado que confirma se houve saída
   relevante ou apenas *repricing* com troca de mãos. Um saldo negativo forte em bancos e utilities
   validaria a tese de fuga de risco doméstico.
2. **Dólar e DI:** a alta do dólar e dos juros explica boa parte da queda. Se o câmbio estabilizar
   perto de R$ 5,50–5,60 e a curva parar de subir, o mercado começa a encontrar piso.
3. **Sinalizações da equipe econômica:** nomes e medidas do novo governo são o próximo gatilho de
   volatilidade. Qualquer sinal de responsabilidade fiscal alivia; o contrário aprofunda.
4. **Volume e amplitude:** em quedas saudáveis, o volume seca e o preço para de fazer novas mínimas.
   Volume alto com novas mínimas indica que a liquidação ainda não terminou.
5. **Short interest e aluguel:** se a posição de aluguel aumentar muito, há mais pressão vendedora
   estrutural — e também combustível para um *short squeeze* em caso de reversão.

## 6. Metodologia e limitações

**Dados de negociação (sessão completa):** arquivos "Negócios Consolidados do Pregão (Listado)" da
B3, 20 pregões (25/09 a 23/10/2026), mercado à vista (lote padrão + fracionário), agregados por
ativo. A classificação de "ação" usa o Cadastro de Instrumentos da B3 (`SHARES` + `UNIT`);
ETFs, FIIs e BDRs ficam de fora dos rankings de ações. "Negócios" = número de operações; "volume
financeiro" = `NtlFinVol`.

**Dados intradiários:** brapi.dev, cotação e volume em ações; volume financeiro estimado =
quantidade × último preço. Snapshot às {resumo['atualizado_em'][11:]}; preços podem variar até o
fechamento. Não inclui *after market* nem blocos.

**Carteira do Ibovespa:** Boletim Diário do Mercado de 23/10/2026 (carteira set–dez/2026),
participação teórica na abertura de 04/09/2026.

**Fluxo por investidor:** dados agregados da B3 compilados por fontes públicas (Rico aos Poucos),
último dado disponível 21/10/2026 (D+2).

**Limitações:** (i) não há identificação de investidor; (ii) não há *short interest* neste recorte;
(iii) o volume de hoje é parcial e estimado; (iv) a variação do Ibovespa é intradiária.

## 7. Arquivos gerados

| Arquivo | Conteúdo |
|---|---|
| `output/ranking_volume.csv` | Top 30 por volume financeiro (23/10) |
| `output/ranking_negocios.csv` | Top 30 por número de negócios (23/10) |
| `output/ranking_quantidade.csv` | Top 30 por quantidade de ações (23/10) |
| `output/variacao_liquidez.csv` | Volume vs. média de 20 pregões |
| `output/pressao.csv` | Proxy de pressão compradora/vendedora (23/10) |
| `output/block_trades.csv` | Negócios em bloco (23/10) |
| `output/live_top.csv` | Ranking intradiário de 26/10 |
| `output/live_setores.csv` | Desempenho por setor em 26/10 |
| `output/live_breadth.csv` | Amplitude do mercado em 26/10 |
| `output/blue_chips.csv` | Pesos do IBOV x liquidez x movimento |
| `output/ibov_pesos.csv` | Carteira teórica do Ibovespa |
| `dashboard/` | Painel em tempo real (altas/baixas, volume compra/venda) |
| `run_dashboard.py` | Inicia o painel e abre o navegador |
| `painel.bat` | Atalho para Windows |
| `scripts/collect_b3.py` | Coleta dos arquivos da B3 |
| `scripts/analyze_b3.py` | Cálculo dos rankings e indicadores |
| `scripts/build_report.py` | Geração deste relatório |

> **Aviso:** este material é analítico e informativo. Não constitui recomendação de compra ou venda
> de valores mobiliários.
"""

(ROOT / "relatorio_B3_2026-10-26.md").write_text(doc, encoding="utf-8")
print("relatorio_B3_2026-10-26.md gerado")
