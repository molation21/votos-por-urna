# Votos por urna: 2024 x 2026

Cruza a votação por urna (seção eleitoral) de um candidato entre a eleição municipal de 2024 (prefeito) e a eleição geral de 2026 (deputado estadual), usando os dados abertos do TSE.

O padrão do script é o candidato Paulo Mac Donald Ghisi, em Foz do Iguaçu (PR). Dá para trocar o candidato com `--busca` e `--numero-2024` / `--numero-2026`.

## Como rodar

Precisa só do Python 3.8 ou superior. Não há pacotes para instalar.

```
python3 votos_urna.py
```

O script baixa do TSE os arquivos de votação por seção do Paraná, localiza o candidato nos dois pleitos, cruza por seção e por local de votação e gera a pasta `saida/`. Se os ZIPs já estiverem no computador:

```
python3 votos_urna.py --zip-2024 votacao_secao_2024_PR.zip --zip-2026 votacao_secao_2026_PR.zip
```

Outras opções: `python3 votos_urna.py --help`.

## O que sai em `saida/`

- `plataforma.html`: painel completo, abre no navegador e funciona sem internet. Tem visão geral, tabela por seção, tabela por local de votação, municípios em 2026 e notas metodológicas.
- `por_secao.csv`, `por_local.csv` e `municipios_2026.csv`: as mesmas tabelas, para abrir no Excel.

## Como ler os números

- **% válidos**: votos do candidato na urna dividido pelos votos válidos do cargo (total menos brancos e nulos).
- **Participação**: parcela do total de votos do candidato na cidade que veio daquela urna ou local.
- **Índice**: % válidos da urna dividido pelo % válidos da cidade inteira. Acima de 1,00 é desempenho acima da média da cidade.
- **Correlação**: coeficiente de Pearson entre o % válidos de 2024 e o de 2026 nos locais comparáveis.

2024 foi eleição majoritária e 2026 é proporcional, então o percentual bruto não é comparável entre os anos. Use participação e índice.

## Cuidados

- As seções mudam entre eleições. O cruzamento por local de votação é mais estável que o por seção: casa por código do local e, na falta, pelo nome. Locais em que o número de seções mudou podem mostrar queda ou alta que é só redistribuição.
- Confira sempre os totais do candidato com o resultado oficial do TSE antes de usar os números.

## Fonte

TSE, Portal de Dados Abertos (licença CC BY):

- https://dadosabertos.tse.jus.br/dataset/resultados-2024
- https://dadosabertos.tse.jus.br/dataset/resultados-2026
