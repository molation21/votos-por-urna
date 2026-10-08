# Votos por urna: panorama de 2016 a 2026

Cruza a votação por urna (seção eleitoral) e por local de votação de um candidato em várias eleições, usando os dados abertos do TSE.

O padrão do script é o candidato Paulo Mac Donald Ghisi, em Foz do Iguaçu (PR): prefeito em 2016, 2020 e 2024 e deputado estadual em 2026. Dá para trocar o candidato com `--busca` e as eleições com `--eleicao`.

## Como rodar

Precisa só do Python 3.8 ou superior. Não há pacotes para instalar.

```
python3 votos_urna.py
```

O script baixa do TSE os arquivos de votação por seção do Paraná (cada um tem de 90 a 150 MB), localiza o candidato em cada eleição, cruza por seção e por local de votação e gera a pasta `saida/`. Se os ZIPs já estiverem no computador:

```
python3 votos_urna.py --zip 2016=votacao_secao_2016_PR.zip --zip 2020=votacao_secao_2020_PR.zip --zip 2024=votacao_secao_2024_PR.zip --zip 2026=votacao_secao_2026_PR.zip
```

Escolher as eleições (formato `ANO:CARGO`; cargo 11 é prefeito, 13 vereador, 7 deputado estadual, 6 deputado federal, 3 governador):

```
python3 votos_urna.py --eleicao 2020:13 --eleicao 2024:11 --eleicao 2026:7
```

Se o nome de urna do candidato for outro em algum ano, use `--busca` com outros termos ou fixe o número com `--numero 2020=19`. Se ele não tiver concorrido àquele cargo naquele ano, o script avisa e segue sem essa eleição (precisa de pelo menos duas). Outras opções: `python3 votos_urna.py --help`.

## O que sai em `saida/`

- `plataforma.html`: painel completo, abre no navegador e funciona sem internet. Tem visão geral (votos e % por eleição, correlação entre todas as eleições, comparação de dois anos à escolha), tabela por seção, tabela por local de votação, municípios e notas metodológicas.
- `por_secao.csv`, `por_local.csv` e `municipios.csv`: as mesmas tabelas, para abrir no Excel.

## Como ler os números

- **% válidos**: votos do candidato na urna dividido pelos votos válidos do cargo (total menos brancos e nulos).
- **Participação**: parcela do total de votos do candidato na cidade que veio daquela urna ou local.
- **Índice**: % válidos da urna dividido pelo % válidos da cidade inteira. Acima de 1,00 é desempenho acima da média da cidade.
- **Correlação**: coeficiente de Pearson entre o % válidos de duas eleições nos locais de votação presentes nas duas.

Eleição majoritária (prefeito) e proporcional (vereador, deputado) não são comparáveis em percentual bruto. Use participação e índice.

## Cuidados

- **Eleições suplementares.** O arquivo do TSE traz, junto com a eleição ordinária, as suplementares do mesmo cargo e turno. O arquivo de 2016 do Paraná, por exemplo, inclui a suplementar de Foz do Iguaçu de 02/04/2017. O script usa só as ordinárias e avisa o que ignorou. Sem esse filtro, os votos válidos e os percentuais saem errados.
- **As zonas e as seções mudam entre eleições.** Em Foz, as zonas 204 e 205 existiam em 2016, em 2020 só restavam a 46 e a 147 e em 2024 apareceu a 104. O número da seção só compara quando o local de votação é o mesmo, e a tabela marca isso na coluna Situação. A visão por local de votação é a mais confiável: casa por código do local, depois por nome e por endereço.
- Locais em que o número de seções mudou podem mostrar queda ou alta que é só redistribuição.
- Confira sempre os totais do candidato com o resultado oficial do TSE antes de usar os números.

## Fonte

TSE, Portal de Dados Abertos (licença CC BY):

- https://dadosabertos.tse.jus.br/dataset/resultados-2016
- https://dadosabertos.tse.jus.br/dataset/resultados-2020
- https://dadosabertos.tse.jus.br/dataset/resultados-2024
- https://dadosabertos.tse.jus.br/dataset/resultados-2026
