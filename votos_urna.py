#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
votos_urna.py - panorama da votacao por urna de um candidato em varias eleicoes.

O que faz
  1. Baixa do TSE (dados abertos) a votacao por secao eleitoral de cada eleicao
     escolhida para a UF (padrao: PR).
  2. Localiza o candidato em cada eleicao pelo nome de urna (ou pelo numero).
  3. Cruza a votacao por secao (urna) e por local de votacao no municipio de
     referencia (o de maior votacao do candidato).
  4. Gera, na pasta "saida": plataforma.html (painel completo, abre no navegador),
     por_secao.csv, por_local.csv e municipios.csv.

Requisitos: Python 3.8 ou superior. Nao precisa instalar nenhum pacote.

Uso basico (padrao: prefeito em 2016, 2020 e 2024 e deputado estadual em 2026)
  python3 votos_urna.py

Escolhendo as eleicoes (ANO:CARGO, com CARGO 11 = prefeito, 13 = vereador,
7 = deputado estadual, 6 = deputado federal)
  python3 votos_urna.py --eleicao 2020:13 --eleicao 2024:11 --eleicao 2026:7

Se os ZIPs ja estiverem no computador
  python3 votos_urna.py --zip 2016=votacao_secao_2016_PR.zip --zip 2020=votacao_secao_2020_PR.zip \\
      --zip 2024=votacao_secao_2024_PR.zip --zip 2026=votacao_secao_2026_PR.zip

Outras opcoes: python3 votos_urna.py --help

Fonte dos dados: TSE, Portal de Dados Abertos (licenca CC BY).
https://dadosabertos.tse.jus.br/dataset/resultados-2016
https://dadosabertos.tse.jus.br/dataset/resultados-2020
https://dadosabertos.tse.jus.br/dataset/resultados-2024
https://dadosabertos.tse.jus.br/dataset/resultados-2026
"""
import argparse
import codecs
import csv
import io
import json
import math
import os
import pickle
import re
import sys
import time
import unicodedata
import urllib.request
import webbrowser
import zipfile
from collections import defaultdict
from datetime import datetime

VERSAO = 3
URL_MODELO = "https://cdn.tse.jus.br/estatistica/sead/odsele/votacao_secao/votacao_secao_{ano}_{uf}.zip"
# Numeros de "votavel" que nao sao candidato nem legenda: branco, nulo e anulados.
NAO_VALIDOS = {"95", "96", "97", "98"}
COLUNAS_OBRIGATORIAS = [
    "NR_TURNO", "CD_MUNICIPIO", "NM_MUNICIPIO", "NR_ZONA", "NR_SECAO",
    "CD_CARGO", "DS_CARGO", "NR_VOTAVEL", "NM_VOTAVEL", "QT_VOTOS",
]


# ----------------------------------------------------------------------------
# utilidades
# ----------------------------------------------------------------------------
def norm(s):
    """Maiusculas, sem acentos e com espacos normalizados."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().upper()


def inteiro(s, padrao=0):
    try:
        return int(s)
    except (TypeError, ValueError):
        return padrao


def pct(v, t):
    return round(100.0 * v / t, 4) if t else None


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx == 0 or syy == 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def mil(n):
    """Inteiro com ponto de milhar (padrao brasileiro)."""
    return format(n, ",").replace(",", ".")


def br(v, casas=2):
    """Numero no padrao brasileiro (virgula decimal) para os CSV."""
    if v is None:
        return ""
    if isinstance(v, int):
        return str(v)
    return ("{:.%df}" % casas).format(v).replace(".", ",")


# ----------------------------------------------------------------------------
# download e leitura
# ----------------------------------------------------------------------------
def baixar(url, destino):
    if os.path.exists(destino) and zipfile.is_zipfile(destino):
        print("  arquivo ja baixado: %s" % destino)
        return destino
    os.makedirs(os.path.dirname(destino) or ".", exist_ok=True)
    parcial = destino + ".parte"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (votos_urna.py)"})
    print("  baixando %s" % url)
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(parcial, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            lido = 0
            t0 = time.time()
            while True:
                bloco = r.read(1 << 20)
                if not bloco:
                    break
                f.write(bloco)
                lido += len(bloco)
                vel = lido / max(time.time() - t0, 0.001) / 1e6
                if total:
                    sys.stdout.write("\r  %.0f%%  (%.0f de %.0f MB, %.1f MB/s)   "
                                     % (100.0 * lido / total, lido / 1e6, total / 1e6, vel))
                else:
                    sys.stdout.write("\r  %.0f MB (%.1f MB/s)   " % (lido / 1e6, vel))
                sys.stdout.flush()
            sys.stdout.write("\n")
    except Exception as e:  # noqa: BLE001
        if os.path.exists(parcial):
            os.remove(parcial)
        raise SystemExit(
            "\nFalha ao baixar %s\n  motivo: %s\n"
            "Baixe o arquivo pelo navegador e rode de novo com --zip-2024 e --zip-2026." % (url, e))
    os.replace(parcial, destino)
    if not zipfile.is_zipfile(destino):
        raise SystemExit("O arquivo baixado nao e um ZIP valido: %s" % destino)
    return destino


def abrir_csv(caminho_zip):
    zf = zipfile.ZipFile(caminho_zip)
    nomes = [n for n in zf.namelist() if n.lower().endswith(".csv") and "votacao_secao" in n.lower()]
    if not nomes:
        nomes = [n for n in zf.namelist() if n.lower().endswith(".csv")]
    if not nomes:
        zf.close()
        raise SystemExit("Nenhum CSV encontrado dentro de %s" % caminho_zip)
    nome = nomes[0]
    with zf.open(nome) as f:
        amostra = f.read(262144)
    # Os arquivos do TSE costumam ser latin-1. UTF-8 so se a amostra tiver acentos validos nele.
    enc, erros = "latin-1", "strict"
    if any(b > 127 for b in amostra):
        dec = codecs.getincrementaldecoder("utf-8")()
        try:
            dec.decode(amostra, final=False)
            enc, erros = "utf-8-sig", "replace"
        except UnicodeDecodeError:
            pass
    texto = io.TextIOWrapper(zf.open(nome), encoding=enc, errors=erros, newline="")
    return zf, nome, texto


def varrer(caminho_zip, cargo, turno, termos, numero=None):
    """Le o CSV de votacao por secao uma unica vez (em fluxo, sem carregar tudo).

    Se "numero" for dado, so esse numero de urna e candidato; senao vale o nome de urna.
    """
    zf, nome, texto = abrir_csv(caminho_zip)
    try:
        leitor = csv.reader(texto, delimiter=";", quotechar='"')
        cab = next(leitor)
        cab = [c.strip().lstrip("\ufeff").strip('"') for c in cab]
        ix = {c: i for i, c in enumerate(cab)}
        faltam = [c for c in COLUNAS_OBRIGATORIAS if c not in ix]
        if faltam:
            raise SystemExit("O CSV %s nao tem as colunas esperadas: %s\nColunas encontradas: %s"
                             % (nome, ", ".join(faltam), ", ".join(cab)))
        i_turno, i_mun, i_nmmun = ix["NR_TURNO"], ix["CD_MUNICIPIO"], ix["NM_MUNICIPIO"]
        i_zona, i_secao = ix["NR_ZONA"], ix["NR_SECAO"]
        i_cargo, i_dscargo = ix["CD_CARGO"], ix["DS_CARGO"]
        i_nr, i_nm, i_qt = ix["NR_VOTAVEL"], ix["NM_VOTAVEL"], ix["QT_VOTOS"]
        i_nrloc = ix.get("NR_LOCAL_VOTACAO")
        i_nmloc = ix.get("NM_LOCAL_VOTACAO")
        i_end = ix.get("DS_LOCAL_VOTACAO_ENDERECO")
        i_sq = ix.get("SQ_CANDIDATO")
        i_tipo, i_dsel, i_dtel = ix.get("NM_TIPO_ELEICAO"), ix.get("DS_ELEICAO"), ix.get("DT_ELEICAO")
        ncol = len(cab)

        secoes = {}   # (mun, zona, secao) -> [total, nao_validos, nr_local, nm_local, endereco, nm_mun]
        cands = {}    # (nr, nome, sq) -> {(mun, zona, secao): votos}
        cargos_vistos = {}
        turnos_vistos = set()
        ordinaria = {}   # (tipo, descricao da eleicao) -> True se for eleicao ordinaria
        ignoradas = {}   # (descricao, data) -> [linhas, {municipios}] das eleicoes extraordinarias/suplementares
        ds_cargo = ""
        cache_nome = {}
        n = 0
        t0 = time.time()
        for row in leitor:
            n += 1
            if n % 2000000 == 0:
                print("  %s milhoes de linhas lidas (%.0fs)" % (n // 1000000, time.time() - t0))
            if len(row) < ncol:
                continue
            cd = row[i_cargo]
            if cd not in cargos_vistos:
                cargos_vistos[cd] = row[i_dscargo]
            if cd != cargo:
                continue
            tr = row[i_turno]
            turnos_vistos.add(tr)
            if tr != turno:
                continue
            if i_tipo is not None and i_dsel is not None:
                tp = (row[i_tipo], row[i_dsel])
                ok_tipo = ordinaria.get(tp)
                if ok_tipo is None:
                    nt, nd = norm(tp[0]), norm(tp[1])
                    ok_tipo = not ("EXTRAORDINARIA" in nt or "SUPLEMENTAR" in nd)
                    ordinaria[tp] = ok_tipo
                if not ok_tipo:
                    g = ignoradas.setdefault((tp[1], row[i_dtel] if i_dtel is not None else ""), [0, set()])
                    g[0] += 1
                    g[1].add(row[i_nmmun])
                    continue
            if not ds_cargo:
                ds_cargo = row[i_dscargo]
            chave = (row[i_mun], row[i_zona], row[i_secao])
            qt = inteiro(row[i_qt])
            reg = secoes.get(chave)
            if reg is None:
                reg = [0, 0,
                       row[i_nrloc] if i_nrloc is not None else "",
                       row[i_nmloc] if i_nmloc is not None else "",
                       row[i_end] if i_end is not None else "",
                       row[i_nmmun]]
                secoes[chave] = reg
            reg[0] += qt
            nr = row[i_nr]
            if nr in NAO_VALIDOS:
                reg[1] += qt
                continue
            nm = row[i_nm]
            if numero:
                ok = (nr == numero)
            else:
                ok = cache_nome.get(nm)
                if ok is None:
                    nn = norm(nm)
                    ok = any(t in nn for t in termos)
                    cache_nome[nm] = ok
            if ok:
                ck = (nr, nm, row[i_sq] if i_sq is not None else "")
                d = cands.get(ck)
                if d is None:
                    d = cands[ck] = defaultdict(int)
                d[chave] += qt
    finally:
        texto.close()
        zf.close()
    print("  %s linhas lidas em %.0fs; %s secoes do cargo escolhido" % (n, time.time() - t0, len(secoes)))
    for (ds, dt), (linhas_ign, muns) in sorted(ignoradas.items()):
        print("  ignorei a eleicao extraordinaria/suplementar \"%s\" (%s): %s linhas em %s"
              % (ds, dt, mil(linhas_ign), ", ".join(sorted(muns)[:5]) + ("..." if len(muns) > 5 else "")))
    if not secoes:
        vistos = ", ".join("%s=%s" % (k, v) for k, v in sorted(cargos_vistos.items()))
        raise SystemExit(
            "Nenhuma linha com cargo %s e turno %s em %s.\n  Cargos no arquivo: %s\n  Turnos do cargo: %s\n"
            "Ajuste --cargo-AAAA / --turno-AAAA." % (cargo, turno, nome, vistos, sorted(turnos_vistos)))
    return {"secoes": secoes, "cands": cands, "ds_cargo": ds_cargo, "linhas": n, "arquivo": nome,
            "ignoradas": [{"eleicao": ds, "data": dt, "linhas": v[0], "municipios": sorted(v[1])}
                          for (ds, dt), v in sorted(ignoradas.items())]}


def varrer_com_cache(caminho_zip, ano, cargo, turno, termos, numero, pasta_dados, usar_cache):
    assin = (os.path.getsize(caminho_zip), int(os.path.getmtime(caminho_zip)), cargo, turno,
             tuple(termos), numero or "", VERSAO)
    cpath = os.path.join(pasta_dados, "cache_%s.pkl" % ano)
    if usar_cache and os.path.exists(cpath):
        try:
            with open(cpath, "rb") as f:
                d = pickle.load(f)
            if d.get("assin") == assin:
                print("  usando resultado em cache (use --refazer para reler o arquivo)")
                return d["res"]
        except Exception:  # noqa: BLE001
            pass
    res = varrer(caminho_zip, cargo, turno, termos, numero)
    try:
        os.makedirs(pasta_dados, exist_ok=True)
        with open(cpath, "wb") as f:
            pickle.dump({"assin": assin, "res": res}, f)
    except OSError:
        pass
    return res


def escolher(res, rotulo, auto, numero=None):
    cands = res["cands"]
    secoes = res["secoes"]
    if not cands:
        print("  aviso: nenhum candidato com os termos de busca em %s (cargo %s). "
              "Ele pode nao ter concorrido a esse cargo, ou o nome de urna e outro: use --busca ou --numero."
              % (rotulo, res["ds_cargo"]))
        return None
    nomes_mun = {k[0]: v[5] for k, v in secoes.items()}
    lista = []
    for (nr, nm, sq), v in cands.items():
        por_mun = defaultdict(int)
        for (mun, _z, _s), q in v.items():
            por_mun[mun] += q
        mun_top = max(por_mun, key=por_mun.get)
        lista.append({
            "nr": nr, "nome": nm, "sq": sq, "votos": v, "total": sum(v.values()),
            "mun_top": mun_top, "nome_mun_top": nomes_mun.get(mun_top, mun_top), "n_mun": len(por_mun),
        })
    lista.sort(key=lambda d: -d["total"])
    if numero:
        sel = [c for c in lista if c["nr"] == str(numero)]
        if not sel:
            raise SystemExit("Nenhum candidato com numero %s em %s. Encontrados: %s" % (
                numero, rotulo, ", ".join("%s (%s)" % (c["nome"], c["nr"]) for c in lista)))
        c = sel[0]
        print("  candidato: %s (numero %s), %s votos, mais votado em %s"
              % (c["nome"], c["nr"], mil(c["total"]), c["nome_mun_top"]))
        return c
    if len(lista) == 1:
        c = lista[0]
        print("  candidato: %s (numero %s), %s votos, mais votado em %s"
              % (c["nome"], c["nr"], mil(c["total"]), c["nome_mun_top"]))
        return c
    print("  Encontrei mais de um candidato com esses termos em %s:" % rotulo)
    for i, c in enumerate(lista, 1):
        print("   [%d] %s | numero %s | %s votos em %d municipio(s) | mais votado em %s"
              % (i, c["nome"], c["nr"], mil(c["total"]), c["n_mun"], c["nome_mun_top"]))
    if auto or not sys.stdin.isatty():
        print("  escolhendo o de maior votacao ([1])")
        return lista[0]
    while True:
        r = input("  Qual deles e o candidato? [1-%d] " % len(lista)).strip()
        if r.isdigit() and 1 <= int(r) <= len(lista):
            return lista[int(r) - 1]


# ----------------------------------------------------------------------------
# cruzamento
# ----------------------------------------------------------------------------
# Cargos de eleicao majoritaria (o restante e proporcional).
CARGOS_MAJORITARIOS = {"1", "2", "3", "4", "5", "11", "12"}


def tipo_cargo(cd_cargo):
    return "majoritária" if str(cd_cargo) in CARGOS_MAJORITARIOS else "proporcional"


def indexar(secoes, votos, mun_ref):
    out = {}
    for (mun, zona, secao), reg in secoes.items():
        if mun != mun_ref:
            continue
        total, nv, nr_local, nm_local, end, _nm = reg
        z, s = inteiro(zona), inteiro(secao)
        out[(z, s)] = {
            "zona": z, "secao": s, "total": total, "validos": total - nv,
            "votos": votos.get((mun, zona, secao), 0),
            "nr_local": nr_local, "nm_local": nm_local, "end": end,
        }
    return out


def calc(a, V, pcity):
    if not a:
        return {"v": None, "t": None, "p": None, "part": None, "i": None}
    p = pct(a["votos"], a["validos"])
    return {
        "v": a["votos"], "t": a["validos"], "p": p, "part": pct(a["votos"], V),
        "i": round(p / pcity, 3) if (p is not None and pcity) else None,
    }


def mesmo_local(a, b):
    if a["nr_local"] and a["nr_local"] == b["nr_local"]:
        return True
    return norm(a["nm_local"]) == norm(b["nm_local"])


def agrupar_locais(sdict):
    loc = {}
    for a in sdict.values():
        k = (a["zona"], a["nr_local"] or norm(a["nm_local"]))
        g = loc.get(k)
        if g is None:
            g = loc[k] = {"zona": a["zona"], "codigo": a["nr_local"], "nome": a["nm_local"], "end": a["end"],
                          "secoes": 0, "votos": 0, "validos": 0}
        g["secoes"] += 1
        g["votos"] += a["votos"]
        g["validos"] += a["validos"]
    return loc


def unificar_locais(por_ano):
    """Junta os locais de votacao de todas as eleicoes em linhas unicas.

    por_ano: [(ano, {chave: grupo})] em ordem cronologica. Para cada eleicao, tenta casar
    cada local com um ja visto: 1) pelo codigo do local dentro da zona, 2) pelo nome,
    3) pelo endereco. O que sobra vira um local novo.
    """
    unif = []
    por_codigo, por_nome, por_end = {}, defaultdict(list), defaultdict(list)

    def registra(i, ano, g, met):
        u = unif[i]
        u["e"][ano] = g
        u["zona"] = g["zona"]   # a zona exibida e a da eleicao mais recente em que o local aparece
        if met:
            u["metodos"].append(met)
        if norm(g["nome"]) not in [norm(x) for x in u["nomes"]]:
            u["nomes"].append(g["nome"])
        if not u["end"] and g["end"]:
            u["end"] = g["end"]
        if g["codigo"]:
            por_codigo[(g["zona"], g["codigo"])] = i
        nn = norm(g["nome"])
        if i not in por_nome[nn]:
            por_nome[nn].append(i)
        ne = norm(g["end"])
        if ne and i not in por_end[ne]:
            por_end[ne].append(i)

    for ano, L in por_ano:
        resto = []
        for g in L.values():
            i = por_codigo.get((g["zona"], g["codigo"])) if g["codigo"] else None
            if i is not None and ano not in unif[i]["e"]:
                ultimo = unif[i]["nomes"][-1]
                registra(i, ano, g, "código" if norm(g["nome"]) == norm(ultimo) else "código (nome mudou)")
            else:
                resto.append(g)
        resto2 = []
        for g in resto:
            cand = [i for i in por_nome.get(norm(g["nome"]), []) if ano not in unif[i]["e"]]
            if cand:
                mesma = [i for i in cand if unif[i]["zona"] == g["zona"]]
                registra((mesma or cand)[0], ano, g, "nome")
            else:
                resto2.append(g)
        for g in resto2:
            # endereco so vale se tiver numero (evita "ZONA RURAL") e apontar para um unico local
            ne = norm(g["end"])
            cand = [i for i in por_end.get(ne, []) if ano not in unif[i]["e"]] if re.search(r"\d", ne) else []
            if len(cand) > 1:
                cand = [i for i in cand if unif[i]["zona"] == g["zona"]]
            if len(cand) == 1:
                registra(cand[0], ano, g, "endereço")
            else:
                unif.append({"zona": g["zona"], "end": "", "e": {}, "metodos": [], "nomes": []})
                registra(len(unif) - 1, ano, g, None)
    return unif


def metodo_local(u):
    """Rotulo unico para a forma como o local foi casado entre as eleicoes."""
    if len(u["e"]) == 1:
        return "só %s" % next(iter(u["e"]))
    for rot in ("endereço", "nome", "código (nome mudou)", "código"):
        if rot in u["metodos"]:
            return rot
    return "código"


def montar(eleicoes, args):
    """eleicoes: lista de dicts {ano, cargo, turno, res, esc}, em qualquer ordem."""
    eleicoes = sorted(eleicoes, key=lambda e: e["ano"])
    anos = [e["ano"] for e in eleicoes]

    # ---- cidade de referencia
    nomes_mun = {}
    tot_mun = defaultdict(int)
    for e in eleicoes:
        for (mun, _z, _s), reg in e["res"]["secoes"].items():
            nomes_mun.setdefault(mun, reg[5])
        for (mun, _z, _s), q in e["esc"]["votos"].items():
            tot_mun[mun] += q
    if args.cidade:
        alvo = norm(args.cidade)
        achados = [m for m, n in nomes_mun.items() if norm(n) == alvo] or \
                  [m for m, n in nomes_mun.items() if alvo in norm(n)]
        if len(achados) != 1:
            raise SystemExit("Município '%s' não encontrado (ou ambíguo). Exemplos: %s"
                             % (args.cidade, ", ".join(sorted(nomes_mun.values())[:8])))
        mun_ref = achados[0]
    else:
        mun_ref = max(tot_mun, key=tot_mun.get)
    nome_ref = nomes_mun.get(mun_ref, mun_ref)

    # ---- indices por eleicao
    sd, tot = {}, {}
    for e in eleicoes:
        a = e["ano"]
        sd[a] = indexar(e["res"]["secoes"], e["esc"]["votos"], mun_ref)
        V = sum(x["votos"] for x in sd[a].values())
        T = sum(x["validos"] for x in sd[a].values())
        tot[a] = {"V": V, "T": T, "p": pct(V, T)}
        if not sd[a]:
            print("  aviso: não há seções de %s em %s para o cargo escolhido" % (nome_ref, a))

    # ---- por secao (urna)
    chaves = sorted(set().union(*[set(sd[a]) for a in anos]))
    secoes_out = []
    status_cont = defaultdict(int)
    for k in chaves:
        pres = [(a, sd[a][k]) for a in anos if k in sd[a]]
        base = pres[0][1]
        if len(pres) == 1:
            status, local = "Só %s" % pres[0][0], base["nm_local"]
        elif all(mesmo_local(base, x) for _, x in pres[1:]):
            status, local = "Comparável", base["nm_local"]
        else:
            nomes, vistos = [], set()
            for _, x in pres:
                if norm(x["nm_local"]) not in vistos:
                    vistos.add(norm(x["nm_local"]))
                    nomes.append(x["nm_local"])
            status, local = "Local diferente", " → ".join(nomes)
        status_cont[status] += 1
        e_out = {}
        for a, x in pres:
            e_out[a] = calc(x, tot[a]["V"], tot[a]["p"])
        secoes_out.append({"zona": k[0], "secao": k[1], "local": local, "end": base["end"],
                           "status": status, "e": e_out})

    # ---- por local de votacao
    unif = unificar_locais([(a, agrupar_locais(sd[a])) for a in anos])
    locais_out = []
    for u in unif:
        e_out = {}
        for a, g in u["e"].items():
            c = calc(g, tot[a]["V"], tot[a]["p"])
            c["s"] = g["secoes"]
            e_out[a] = c
        locais_out.append({"zona": u["zona"], "local": " → ".join(u["nomes"]), "end": u["end"],
                           "metodo": metodo_local(u), "n_anos": len(u["e"]), "e": e_out})
    locais_out.sort(key=lambda d: (d["zona"], d["local"]))

    # ---- correlacao entre cada par de eleicoes (desempenho % por local)
    correl = []
    for i, a in enumerate(anos):
        for b in anos[i + 1:]:
            par = [(l["e"][a]["p"], l["e"][b]["p"]) for l in locais_out
                   if a in l["e"] and b in l["e"] and l["e"][a]["p"] is not None and l["e"][b]["p"] is not None]
            correl.append({"a": a, "b": b, "r": pearson([x for x, _ in par], [y for _, y in par]), "n": len(par)})

    # ---- municipios (todo o estado)
    agg = {}
    for e in eleicoes:
        a = e["ano"]
        for (mun, zona, secao), reg in e["res"]["secoes"].items():
            m = agg.get(mun)
            if m is None:
                m = agg[mun] = {"municipio": reg[5], "e": {}}
            x = m["e"].get(a)
            if x is None:
                x = m["e"][a] = [0, 0, 0]
            x[0] += e["esc"]["votos"].get((mun, zona, secao), 0)
            x[1] += reg[0] - reg[1]
            x[2] += 1
    mun_out = []
    for mun, m in agg.items():
        if mun != mun_ref and not any(x[0] for x in m["e"].values()):
            continue
        mun_out.append({
            "municipio": m["municipio"], "cidade_ref": "sim" if mun == mun_ref else "não",
            "e": {a: {"v": x[0], "t": x[1], "p": pct(x[0], x[1]), "s": x[2]} for a, x in m["e"].items()},
        })
    mun_out.sort(key=lambda d: -sum(x["v"] for x in d["e"].values()))

    meta = {
        "gerado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "uf": args.uf,
        "cidade": nome_ref,
        "eleicoes": [{
            "ano": e["ano"], "cd_cargo": e["cargo"], "cargo": e["res"]["ds_cargo"].title(), "tipo": tipo_cargo(e["cargo"]),
            "nome": e["esc"]["nome"], "numero": e["esc"]["nr"], "turno": e["turno"],
            "votos_total": e["esc"]["total"], "n_mun": e["esc"]["n_mun"],
            "v": tot[e["ano"]]["V"], "t": tot[e["ano"]]["T"], "p": tot[e["ano"]]["p"],
            "secoes": len(sd[e["ano"]]),
            "ignoradas": [{"eleicao": x["eleicao"], "data": x["data"]}
                          for x in e["res"].get("ignoradas", []) if nome_ref in x["municipios"]],
        } for e in eleicoes],
        "correlacoes": correl,
        "secoes_total": len(chaves),
        "secoes_comp": status_cont.get("Comparável", 0),
        "status_secoes": dict(status_cont),
        "locais_total": len(locais_out),
        "locais_todos": sum(1 for l in locais_out if l["n_anos"] == len(anos)),
    }
    return {"meta": meta, "secoes": secoes_out, "locais": locais_out, "municipios": mun_out}


# ----------------------------------------------------------------------------
# saida
# ----------------------------------------------------------------------------
def casas_de(chave):
    if chave.startswith(("part", "dpart")):
        return 4
    if chave.startswith("i_"):
        return 3
    return 2


def grava_csv(caminho, linhas, colunas):
    with open(caminho, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow([t for _, t in colunas])
        for d in linhas:
            w.writerow([br(d.get(k), casas_de(k)) if isinstance(d.get(k), (int, float)) else (d.get(k) or "")
                        for k, _ in colunas])


def achatar(linha, anos, campos):
    """Copia linha["e"][ano][campo] para chaves planas campo_ano (para os CSV)."""
    out = dict(linha)
    for a in anos:
        x = linha["e"].get(a) or {}
        for c in campos:
            out["%s_%s" % (c, a)] = x.get(c)
    return out


def colunas_medidas(anos, campos):
    rot = {"v": "Votos", "t": "Votos válidos", "p": "% válidos", "part": "Participação (%)",
           "i": "Índice", "s": "Seções"}
    cols = []
    for a in anos:
        for c in campos:
            cols.append(("%s_%s" % (c, a), "%s %s" % (rot[c], a)))
    return cols


def variacao(linhas, anos):
    """Variacao da participacao (p.p.) entre a primeira e a ultima eleicao, em cada linha."""
    a, b = anos[0], anos[-1]
    for d in linhas:
        pa, pb = (d["e"].get(a) or {}).get("part"), (d["e"].get(b) or {}).get("part")
        d["dpart"] = round(pb - pa, 4) if (pa is not None and pb is not None) else None


def gerar_html(dados):
    texto = json.dumps(dados, ensure_ascii=False, separators=(",", ":"))
    texto = texto.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    ult = dados["meta"]["eleicoes"][-1]
    titulo = "%s - %s" % (ult["nome"], dados["meta"]["cidade"])
    return HTML_MODELO.replace("__TITULO__", titulo.replace("<", "&lt;")).replace("__DADOS__", texto)


ELEICOES_PADRAO = ["2016:11", "2020:11", "2024:11", "2026:7"]


def interpretar_eleicao(txt):
    p = txt.split(":")
    if len(p) not in (2, 3) or not p[0].isdigit() or not p[1].isdigit() or (len(p) == 3 and not p[2].isdigit()):
        raise SystemExit("Eleição inválida: '%s'. Use ANO:CARGO ou ANO:CARGO:TURNO, por exemplo 2020:13." % txt)
    return {"ano": p[0], "cargo": p[1], "turno": p[2] if len(p) == 3 else "1"}


def mapa_ano(itens, nome):
    out = {}
    for it in itens or []:
        if "=" not in it:
            raise SystemExit("Use %s ANO=VALOR, por exemplo %s 2020=11123." % (nome, nome))
        a, v = it.split("=", 1)
        out[a.strip()] = v.strip()
    return out


def main():
    ap = argparse.ArgumentParser(
        description="Panorama da votação por urna de um candidato em várias eleições (dados abertos do TSE).")
    ap.add_argument("--uf", default="PR", help="UF dos arquivos do TSE (padrão: PR)")
    ap.add_argument("--busca", nargs="+", default=["MAC DONALD", "MACDONALD", "GHISI"],
                    help="termos para achar o candidato pelo nome de urna (padrão: MAC DONALD, MACDONALD, GHISI)")
    ap.add_argument("--eleicao", action="append", metavar="ANO:CARGO[:TURNO]",
                    help="eleição a incluir; repita para várias. CARGO: 11 prefeito, 13 vereador, 7 dep. estadual, "
                         "6 dep. federal, 3 governador. Padrão: 2016:11 2020:11 2024:11 2026:7")
    ap.add_argument("--numero", action="append", metavar="ANO=NUMERO",
                    help="fixa o candidato daquele ano pelo número de urna (evita a pergunta); repita se precisar")
    ap.add_argument("--zip", action="append", metavar="ANO=CAMINHO",
                    help="ZIP já baixado daquele ano; repita se precisar")
    ap.add_argument("--cidade", help="município de referência (padrão: o de maior votação do candidato)")
    ap.add_argument("--dados", default="dados", help="pasta dos arquivos baixados (padrão: dados)")
    ap.add_argument("--saida", default="saida", help="pasta dos resultados (padrão: saida)")
    ap.add_argument("--auto", action="store_true", help="não perguntar: usar o candidato mais votado")
    ap.add_argument("--refazer", action="store_true", help="ignorar o cache e reler os arquivos")
    ap.add_argument("--sem-abrir", action="store_true", help="não abrir o navegador no final")
    args = ap.parse_args()

    lista = [interpretar_eleicao(t) for t in (args.eleicao or ELEICOES_PADRAO)]
    anos_lista = [x["ano"] for x in lista]
    if len(set(anos_lista)) != len(anos_lista):
        raise SystemExit("Há dois itens --eleicao para o mesmo ano.")
    numeros, zips = mapa_ano(args.numero, "--numero"), mapa_ano(args.zip, "--zip")
    termos = [norm(t) for t in args.busca]

    eleicoes, ausentes = [], []
    for x in lista:
        ano = x["ano"]
        print("\n== Eleição %s ==" % ano)
        if ano in zips:
            caminho = zips[ano]
            if not os.path.exists(caminho):
                raise SystemExit("Arquivo não encontrado: %s" % caminho)
        else:
            caminho = baixar(URL_MODELO.format(ano=ano, uf=args.uf),
                             os.path.join(args.dados, "votacao_secao_%s_%s.zip" % (ano, args.uf)))
        res = varrer_com_cache(caminho, ano, x["cargo"], x["turno"], termos, numeros.get(ano),
                               args.dados, not args.refazer)
        esc = escolher(res, ano, args.auto, numeros.get(ano))
        if esc is None:
            ausentes.append(ano)
            continue
        eleicoes.append({"ano": ano, "cargo": x["cargo"], "turno": x["turno"], "res": res, "esc": esc})

    if len(eleicoes) < 2:
        raise SystemExit("\nPreciso do candidato em pelo menos duas eleições para cruzar. Encontrado em: %s."
                         % (", ".join(e["ano"] for e in eleicoes) or "nenhuma"))

    print("\n== Cruzamento ==")
    dados = montar(eleicoes, args)
    m = dados["meta"]
    anos = [e["ano"] for e in m["eleicoes"]]
    variacao(dados["secoes"], anos)
    variacao(dados["locais"], anos)

    os.makedirs(args.saida, exist_ok=True)
    a0, a1 = anos[0], anos[-1]
    cols_sec = ([("zona", "Zona"), ("secao", "Seção"), ("local", "Local de votação"), ("end", "Endereço")]
                + colunas_medidas(anos, ["v", "t", "p", "part", "i"])
                + [("dpart", "Variação da participação %s→%s (p.p.)" % (a0, a1)), ("status", "Situação")])
    cols_loc = ([("zona", "Zona"), ("local", "Local de votação"), ("end", "Endereço")]
                + colunas_medidas(anos, ["s", "v", "t", "p", "part", "i"])
                + [("dpart", "Variação da participação %s→%s (p.p.)" % (a0, a1)), ("metodo", "Cruzamento")])
    cols_mun = ([("municipio", "Município")] + colunas_medidas(anos, ["v", "t", "p", "s"])
                + [("cidade_ref", "Cidade de referência")])
    grava_csv(os.path.join(args.saida, "por_secao.csv"),
              [achatar(d, anos, ["v", "t", "p", "part", "i"]) for d in dados["secoes"]], cols_sec)
    grava_csv(os.path.join(args.saida, "por_local.csv"),
              [achatar(d, anos, ["s", "v", "t", "p", "part", "i"]) for d in dados["locais"]], cols_loc)
    grava_csv(os.path.join(args.saida, "municipios.csv"),
              [achatar(d, anos, ["v", "t", "p", "s"]) for d in dados["municipios"]], cols_mun)
    caminho_html = os.path.join(args.saida, "plataforma.html")
    with open(caminho_html, "w", encoding="utf-8") as f:
        f.write(gerar_html(dados))

    print("  cidade de referência: %s" % m["cidade"])
    for e in m["eleicoes"]:
        print("  %s (%s, %s, n. %s): %s votos na cidade, %s%% dos válidos; %s votos no estado"
              % (e["ano"], e["cargo"], e["tipo"], e["numero"], mil(e["v"]), br(e["p"]), mil(e["votos_total"])))
    if ausentes:
        print("  sem o candidato (ignoradas): %s" % ", ".join(ausentes))
    print("  seções: %d no total, %d comparáveis; locais: %d, %d presentes em todas as eleições"
          % (m["secoes_total"], m["secoes_comp"], m["locais_total"], m["locais_todos"]))
    for c in m["correlacoes"]:
        print("  correlação %s x %s (por local): %s, em %d locais" % (c["a"], c["b"], br(c["r"]), c["n"]))
    print("\nConfira os totais acima com o resultado oficial do TSE antes de usar os números.")
    print("\nPronto. Arquivos em %s/ :" % args.saida)
    for nome in ("plataforma.html", "por_secao.csv", "por_local.csv", "municipios.csv"):
        print("  - %s" % nome)
    if not args.sem_abrir:
        try:
            webbrowser.open("file://" + os.path.abspath(caminho_html))
        except Exception:  # noqa: BLE001
            pass


# ----------------------------------------------------------------------------
# modelo da plataforma (HTML autossuficiente, funciona sem internet)
# ----------------------------------------------------------------------------
HTML_MODELO = r"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Votos por urna: __TITULO__</title>
<style>
:root{--bg:#f6f6f3;--card:#ffffff;--tx:#1d1d1f;--mut:#6a6a70;--bd:#e2e2dd;--maj:#2f6fb5;--prop:#d9822b;--pos:#23864f;--neg:#c2413b;--cor:47,111,181}
@media (prefers-color-scheme:dark){:root{--bg:#151517;--card:#1f1f22;--tx:#ececee;--mut:#9b9ba2;--bd:#34343a;--maj:#6aa6e6;--prop:#f0a45a;--pos:#52c08a;--neg:#ee7b73;--cor:106,166,230}}
*{box-sizing:border-box}
body{margin:0;font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--tx)}
header{max-width:1280px;margin:0 auto;padding:20px 24px 0}
h1{font-size:22px;margin:0 0 2px}
.sub{color:var(--mut);margin:0 0 14px;font-size:14px}
nav{display:flex;gap:4px;flex-wrap:wrap;border-bottom:1px solid var(--bd)}
nav button{background:none;border:0;border-bottom:2px solid transparent;color:var(--mut);padding:10px 14px;font:inherit;cursor:pointer}
nav button.on{color:var(--tx);border-bottom-color:var(--prop);font-weight:600}
main{max-width:1280px;margin:0 auto;padding:20px 24px 56px}
.aba[hidden],.barra[hidden]{display:none}
.grade{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin-bottom:20px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:14px 16px}
.card .t{font-size:12.5px;color:var(--mut)}
.card .v{font-size:26px;font-weight:650;margin:2px 0}
.card .n{font-size:12.5px;color:var(--mut)}
.painel{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:16px;margin-bottom:20px}
.painel h2{font-size:16px;margin:0 0 4px}
.painel p{margin:4px 0 10px}
.duas{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:16px}
.barra{display:flex;gap:8px;flex-wrap:wrap;align-items:center;padding:10px 14px}
.barra .sep{flex:1 1 20px}
.ctl{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px}
input[type=search],select{font:inherit;padding:7px 10px;border:1px solid var(--bd);border-radius:8px;background:var(--card);color:var(--tx)}
input[type=search]{min-width:240px;flex:1 1 240px}
button.exp{font:inherit;padding:7px 12px;border:1px solid var(--bd);border-radius:8px;background:var(--card);color:var(--tx);cursor:pointer;margin-left:auto}
.cont{color:var(--mut);font-size:13px}
.tw{overflow:auto;max-height:70vh;border:1px solid var(--bd);border-radius:10px;background:var(--card)}
.painel .tw{max-height:none}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{padding:7px 10px;border-bottom:1px solid var(--bd);text-align:left;white-space:nowrap}
td.largo{white-space:normal;min-width:240px}
th{position:sticky;top:0;background:var(--card);cursor:pointer;user-select:none;z-index:1}
.matriz th{position:static;cursor:default}
.num{text-align:right;font-variant-numeric:tabular-nums}
.pos{color:var(--pos)}.neg{color:var(--neg)}.mut{color:var(--mut)}
svg text{fill:var(--mut);font-size:11px}
svg text.val{fill:var(--tx);font-size:12px;font-weight:600}
svg .eixo{stroke:var(--bd)}
svg .ponto{fill:var(--prop);fill-opacity:.6;stroke:var(--prop)}
svg .reta{stroke:var(--maj);stroke-width:2;fill:none}
svg .bmaj{fill:var(--maj)}
svg .bprop{fill:var(--prop)}
.leg{display:flex;gap:14px;flex-wrap:wrap;font-size:12.5px;color:var(--mut);margin-top:6px}
.leg i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px}
.notas h3{font-size:14px;margin:16px 0 4px}
.notas li{margin:3px 0}
</style>
</head>
<body>
<header>
  <h1 id="titulo"></h1>
  <p class="sub" id="subtitulo"></p>
  <nav id="abas"></nav>
</header>
<main>
  <div class="painel barra" id="barra"></div>
  <section id="aba-geral" class="aba"></section>
  <section id="aba-secoes" class="aba" hidden></section>
  <section id="aba-locais" class="aba" hidden></section>
  <section id="aba-mun" class="aba" hidden></section>
  <section id="aba-notas" class="aba notas" hidden></section>
</main>
<script id="dados" type="application/json">__DADOS__</script>
<script>
(function(){
'use strict';
var D = JSON.parse(document.getElementById('dados').textContent);
var M = D.meta, E = M.eleicoes;
var anos = E.map(function(e){ return e.ano; });
var info = {}; E.forEach(function(e){ info[e.ano] = e; });
var nf = new Intl.NumberFormat('pt-BR');
function num(v){ return v==null ? '-' : nf.format(v); }
function dec(v,c){ return v==null ? '-' : v.toLocaleString('pt-BR',{minimumFractionDigits:c,maximumFractionDigits:c}); }
function pct(v){ return v==null ? '-' : dec(v,2)+'%'; }
function pp(v){ return v==null ? '-' : (v>0?'+':'')+dec(v,2)+' p.p.'; }
function ind(v){ return v==null ? '-' : dec(v,2); }
function esc(s){ return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];}); }
function norm(s){ return String(s==null?'':s).normalize('NFD').replace(/[̀-ͯ]/g,'').toLowerCase(); }
function sinal(v){ return v==null||v===0 ? '' : (v>0?'pos':'neg'); }

var ultimo = E[E.length-1];
document.title = 'Votos por urna: ' + ultimo.nome + ' - ' + M.cidade;
document.getElementById('titulo').textContent = ultimo.nome + ': votos por urna em ' + M.cidade;
document.getElementById('subtitulo').textContent =
  E.map(function(e){ return e.ano + ' (' + e.cargo + ', n. ' + e.numero + ')'; }).join(' | ') +
  ' | UF ' + M.uf + ' | dados do TSE | gerado em ' + M.gerado_em;

/* estado: par de eleições comparado (A -> B) e medida mostrada nas tabelas */
var S = {a: anos[anos.length-2], b: anos[anos.length-1], m: 'p'};

/* ---------- campos planos para as tabelas ---------- */
function achatar(linhas, campos){
  linhas.forEach(function(l){
    anos.forEach(function(y){
      var c = l.e[y];
      campos.forEach(function(f){ l[f+'_'+y] = c ? c[f] : null; });
    });
  });
}
achatar(D.secoes, ['v','t','p','part','i']);
achatar(D.locais, ['s','v','t','p','part','i']);
achatar(D.municipios, ['v','t','p','s']);
D.locais.forEach(function(l){
  l.secs = anos.map(function(y){ return l['s_'+y]==null ? '-' : String(l['s_'+y]); }).join(' / ');
});
function atualizarDelta(){
  [D.secoes, D.locais].forEach(function(arr){
    arr.forEach(function(l){
      var x = l['part_'+S.a], y = l['part_'+S.b];
      l.dpart = (x!=null && y!=null) ? Math.round((y-x)*10000)/10000 : null;
    });
  });
}

var cor = {};
M.correlacoes.forEach(function(c){ cor[c.a+'|'+c.b] = c; cor[c.b+'|'+c.a] = c; });

/* ---------- tabela genérica ---------- */
function criarTabela(id, linhas, colsFn, opt){
  var raiz = document.getElementById(id);
  var cols = colsFn();
  var st = {busca:'', zona:'', status:'', manual:false, key: opt.chave ? opt.chave() : null, dir: opt.dir||-1};
  var zonas = opt.zonas ? Array.from(new Set(linhas.map(function(l){return l.zona;}))).sort(function(a,b){return a-b;}) : [];
  var stats = opt.status ? Array.from(new Set(linhas.map(function(l){return l[opt.status];}))).sort() : [];
  raiz.innerHTML =
    '<div class="ctl">'+
    '<input type="search" class="busca" placeholder="'+esc(opt.ph||'Buscar...')+'">'+
    (zonas.length ? '<select class="zona"><option value="">Todas as zonas</option>'+zonas.map(function(z){return '<option value="'+z+'">Zona '+z+'</option>';}).join('')+'</select>' : '')+
    (stats.length ? '<select class="status"><option value="">'+esc(opt.rotStatus||'Todas as situações')+'</option>'+stats.map(function(s){return '<option>'+esc(s)+'</option>';}).join('')+'</select>' : '')+
    '<span class="cont"></span><button type="button" class="exp">Exportar CSV</button></div>'+
    '<div class="tw"><table><thead></thead><tbody></tbody></table></div>';
  var thead = raiz.querySelector('thead'), tbody = raiz.querySelector('tbody'), cont = raiz.querySelector('.cont');

  function colAtual(){
    for(var i=0;i<cols.length;i++){ if(cols[i].k===st.key) return cols[i]; }
    return null;
  }
  function filtradas(){
    var q = norm(st.busca);
    var r = linhas.filter(function(l){
      return (!st.zona || String(l.zona)===st.zona) && (!st.status || l[opt.status]===st.status) && (!q || norm(opt.texto(l)).indexOf(q)>=0);
    });
    var c = colAtual();
    if(c){
      r = r.slice().sort(function(a,b){
        var x=a[c.k], y=b[c.k];
        if(x==null && y==null) return 0;
        if(x==null) return 1;
        if(y==null) return -1;
        return (typeof x==='number' ? x-y : String(x).localeCompare(String(y),'pt-BR')) * st.dir;
      });
    }
    return r;
  }
  function render(){
    var r = filtradas();
    thead.innerHTML = '<tr>'+cols.map(function(c,i){
      return '<th class="'+(c.n?'num':'')+'" data-i="'+i+'">'+esc(c.t)+(st.key===c.k?(st.dir>0?' ▲':' ▼'):'')+'</th>';
    }).join('')+'</tr>';
    tbody.innerHTML = r.map(function(l){
      return '<tr>'+cols.map(function(c){
        var v = l[c.k];
        var txt = c.f ? c.f(v,l) : esc(v);
        var cl = (c.n?'num ':'')+(c.w?'largo ':'')+(c.cls?c.cls(v,l):'');
        return '<td class="'+cl+'">'+txt+'</td>';
      }).join('')+'</tr>';
    }).join('');
    cont.textContent = nf.format(r.length)+' de '+nf.format(linhas.length)+' linhas';
  }
  thead.addEventListener('click', function(e){
    var th = e.target.closest('th'); if(!th) return;
    var c = cols[+th.getAttribute('data-i')];
    st.manual = true;
    if(st.key===c.k){ st.dir = -st.dir; } else { st.key = c.k; st.dir = c.n ? -1 : 1; }
    render();
  });
  raiz.querySelector('.busca').addEventListener('input', function(e){ st.busca = e.target.value; render(); });
  var sz = raiz.querySelector('.zona'); if(sz) sz.addEventListener('change', function(e){ st.zona = e.target.value; render(); });
  var ss = raiz.querySelector('.status'); if(ss) ss.addEventListener('change', function(e){ st.status = e.target.value; render(); });
  raiz.querySelector('.exp').addEventListener('click', function(){
    var r = filtradas();
    function cel(v){ if(v==null) return ''; if(typeof v==='number') return String(v).replace('.',','); return '"'+String(v).replace(/"/g,'""')+'"'; }
    var out = [cols.map(function(c){return '"'+c.t+'"';}).join(';')].concat(r.map(function(l){ return cols.map(function(c){return cel(l[c.k]);}).join(';'); }));
    var blob = new Blob(['﻿'+out.join('\r\n')], {type:'text/csv;charset=utf-8'});
    var a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = (opt.arquivo||'tabela')+'.csv';
    document.body.appendChild(a); a.click(); a.remove();
  });
  render();
  return {refresh: function(){
    cols = colsFn();
    if(!st.manual && opt.chave){ st.key = opt.chave(); st.dir = opt.dir||-1; }
    if(!colAtual()){ st.key = null; }
    render();
  }};
}

var MED = {v:['Votos',num], p:['% válidos',pct], part:['Part.',pct], i:['Índice',ind]};
function colsMedida(){
  var d = MED[S.m];
  return anos.map(function(y){ return {k:S.m+'_'+y, t:d[0]+' '+y, n:1, f:d[1]}; });
}
function colDelta(){ return {k:'dpart', t:'Variação da part. '+S.a+'→'+S.b, n:1, f:pp, cls:sinal}; }
function colsSec(){
  return [{k:'zona',t:'Zona',n:1},{k:'secao',t:'Seção',n:1},{k:'local',t:'Local de votação',w:1}]
    .concat(colsMedida(), [colDelta(), {k:'status',t:'Situação'}]);
}
function colsLoc(){
  return [{k:'zona',t:'Zona',n:1},{k:'local',t:'Local de votação',w:1},{k:'end',t:'Endereço',w:1},
          {k:'secs',t:'Seções ('+anos.join(' / ')+')'}]
    .concat(colsMedida(), [colDelta(), {k:'metodo',t:'Cruzamento'}]);
}
function colsMun(){
  var c = [{k:'municipio',t:'Município'}];
  anos.forEach(function(y){
    c.push({k:'v_'+y,t:'Votos '+y,n:1,f:num});
    c.push({k:'p_'+y,t:'% válidos '+y,n:1,f:pct});
  });
  c.push({k:'cidade_ref',t:'Cidade de referência'});
  return c;
}

/* ---------- visão geral ---------- */
function cartao(t,v,n){ return '<div class="card"><div class="t">'+t+'</div><div class="v">'+v+'</div><div class="n">'+n+'</div></div>'; }

function barras(itens, fmt, rotulo){
  var W=520,H=230,ml=12,mr=12,mt=26,mb=34;
  var mx = Math.max.apply(null, itens.map(function(d){return d.val||0;})) || 1;
  var n = itens.length, passo = (W-ml-mr)/n, bw = Math.min(72, passo*0.6);
  var s = '<svg viewBox="0 0 '+W+' '+H+'" width="100%" role="img" aria-label="'+esc(rotulo)+'">';
  s += '<line class="eixo" x1="'+ml+'" y1="'+(H-mb)+'" x2="'+(W-mr)+'" y2="'+(H-mb)+'"/>';
  itens.forEach(function(d,i){
    var h = (H-mt-mb)*(d.val||0)/mx, x = ml+passo*i+(passo-bw)/2, y = H-mb-h, cx = x+bw/2;
    s += '<rect class="'+(d.tipo==='majoritária'?'bmaj':'bprop')+'" x="'+x.toFixed(1)+'" y="'+y.toFixed(1)+'" width="'+bw.toFixed(1)+'" height="'+Math.max(h,0).toFixed(1)+'"><title>'+esc(d.ano+' ('+d.cargo+'): '+fmt(d.val))+'</title></rect>';
    s += '<text class="val" x="'+cx.toFixed(1)+'" y="'+(y-6).toFixed(1)+'" text-anchor="middle">'+fmt(d.val)+'</text>';
    s += '<text x="'+cx.toFixed(1)+'" y="'+(H-mb+16)+'" text-anchor="middle">'+d.ano+'</text>';
  });
  return s+'</svg>';
}
function legenda(){
  var tipos = {}; E.forEach(function(e){ tipos[e.tipo] = 1; });
  var s = '<div class="leg">';
  if(tipos['majoritária']) s += '<span><i style="background:var(--maj)"></i>eleição majoritária</span>';
  if(tipos['proporcional']) s += '<span><i style="background:var(--prop)"></i>eleição proporcional</span>';
  return s+'</div>';
}

function matriz(){
  var s = '<div class="tw"><table class="matriz"><thead><tr><th></th>'+anos.map(function(y){return '<th class="num">'+y+'</th>';}).join('')+'</tr></thead><tbody>';
  anos.forEach(function(a){
    s += '<tr><th>'+a+' <span class="mut">'+esc(info[a].cargo)+'</span></th>';
    anos.forEach(function(b){
      if(a===b){ s += '<td class="num mut">·</td>'; return; }
      var c = cor[a+'|'+b];
      if(!c || c.r==null){ s += '<td class="num mut">-</td>'; return; }
      var al = Math.min(1, Math.abs(c.r))*0.45;
      s += '<td class="num" style="background:rgba(var(--cor),'+al.toFixed(2)+')" title="'+c.n+' locais">'+dec(c.r,2)+'</td>';
    });
    s += '</tr>';
  });
  return s+'</tbody></table></div>';
}

function mini(linhas){
  if(!linhas.length) return '<p class="mut">Nenhum local nesta situação.</p>';
  return '<div class="tw"><table><thead><tr><th>Local</th><th class="num">Part. '+S.a+'</th><th class="num">Part. '+S.b+'</th><th class="num">Variação</th></tr></thead><tbody>'+
    linhas.map(function(l){
      return '<tr><td class="largo">'+esc(l.local)+'</td><td class="num">'+pct(l['part_'+S.a])+'</td><td class="num">'+pct(l['part_'+S.b])+'</td><td class="num '+sinal(l.dpart)+'">'+pp(l.dpart)+'</td></tr>';
    }).join('')+'</tbody></table></div>';
}

function dispersao(pts){
  if(pts.length<3) return '<p class="mut">Poucos locais comparáveis para montar o gráfico.</p>';
  var W=640,H=420,ml=58,mr=16,mt=14,mb=48;
  var mx=Math.max.apply(null,pts.map(function(p){return p.x;}))*1.08 || 1;
  var my=Math.max.apply(null,pts.map(function(p){return p.y;}))*1.08 || 1;
  var mw=Math.max.apply(null,pts.map(function(p){return p.w;})) || 1;
  function X(v){return ml+(W-ml-mr)*v/mx;}
  function Y(v){return H-mb-(H-mt-mb)*v/my;}
  var n=pts.length, sx=0, sy=0, i;
  pts.forEach(function(p){sx+=p.x; sy+=p.y;});
  var mxx=sx/n, myy=sy/n, sxx=0, sxy=0;
  pts.forEach(function(p){sxx+=(p.x-mxx)*(p.x-mxx); sxy+=(p.x-mxx)*(p.y-myy);});
  var b=sxx?sxy/sxx:0, a=myy-b*mxx;
  var xmin=Math.min.apply(null,pts.map(function(p){return p.x;})), xmax=Math.max.apply(null,pts.map(function(p){return p.x;}));
  var s='<svg viewBox="0 0 '+W+' '+H+'" width="100%" role="img" aria-label="Dispersão do desempenho percentual por local de votação, '+S.a+' contra '+S.b+'">';
  for(i=0;i<=5;i++){
    var vx=mx*i/5, vy=my*i/5;
    s+='<line class="eixo" x1="'+X(vx)+'" y1="'+(H-mb)+'" x2="'+X(vx)+'" y2="'+mt+'"/>';
    s+='<line class="eixo" x1="'+ml+'" y1="'+Y(vy)+'" x2="'+(W-mr)+'" y2="'+Y(vy)+'"/>';
    s+='<text x="'+X(vx)+'" y="'+(H-mb+16)+'" text-anchor="middle">'+dec(vx,1)+'%</text>';
    s+='<text x="'+(ml-8)+'" y="'+(Y(vy)+4)+'" text-anchor="end">'+dec(vy,1)+'%</text>';
  }
  s+='<text x="'+((ml+W-mr)/2)+'" y="'+(H-8)+'" text-anchor="middle">% dos votos válidos em '+S.a+' ('+esc(info[S.a].cargo)+')</text>';
  s+='<text transform="translate(14,'+((mt+H-mb)/2)+') rotate(-90)" text-anchor="middle">% dos votos válidos em '+S.b+' ('+esc(info[S.b].cargo)+')</text>';
  pts.forEach(function(p){
    var r=3+7*Math.sqrt(p.w/mw);
    s+='<circle class="ponto" cx="'+X(p.x)+'" cy="'+Y(p.y)+'" r="'+r.toFixed(1)+'"><title>'+esc(p.t)+' | '+S.a+': '+pct(p.x)+' | '+S.b+': '+pct(p.y)+'</title></circle>';
  });
  s+='<line class="reta" x1="'+X(xmin)+'" y1="'+Y(Math.max(0,a+b*xmin))+'" x2="'+X(xmax)+'" y2="'+Y(Math.max(0,a+b*xmax))+'"/>';
  return s+'</svg>';
}

function geral(){
  var el = document.getElementById('aba-geral');
  var st = M.status_secoes || {};
  var soUm = 0; Object.keys(st).forEach(function(k){ if(k.indexOf('Só ')===0) soUm += st[k]; });
  var cards = E.map(function(e){
    return cartao(e.ano+' | '+esc(e.cargo), num(e.v),
      pct(e.p)+' dos válidos em '+esc(M.cidade)+' | n. '+esc(e.numero)+
      (e.n_mun>1 ? ' | '+num(e.votos_total)+' votos no estado, em '+num(e.n_mun)+' municípios' : ''));
  });
  cards.push(cartao('Urnas comparáveis', num(M.secoes_comp),
    'de '+num(M.secoes_total)+' seções ('+num(st['Local diferente']||0)+' com local diferente, '+num(soUm)+' em uma única eleição)'));
  cards.push(cartao('Locais de votação', num(M.locais_total), num(M.locais_todos)+' presentes em todas as eleições'));
  el.innerHTML =
    '<div class="grade">'+cards.join('')+'</div>'+
    '<div class="duas">'+
    '<div class="painel"><h2>Votos em '+esc(M.cidade)+'</h2><p class="mut">Total do candidato na cidade em cada eleição.</p>'+
      barras(E.map(function(e){return {ano:e.ano,cargo:e.cargo,tipo:e.tipo,val:e.v};}), num, 'Votos do candidato por eleição')+legenda()+'</div>'+
    '<div class="painel"><h2>% dos votos válidos em '+esc(M.cidade)+'</h2><p class="mut">Majoritária e proporcional não são comparáveis em percentual bruto; veja a participação e o índice por local.</p>'+
      barras(E.map(function(e){return {ano:e.ano,cargo:e.cargo,tipo:e.tipo,val:e.p};}), pct, 'Percentual dos votos válidos por eleição')+legenda()+'</div>'+
    '</div>'+
    '<div class="painel"><h2>Correlação entre as eleições</h2>'+
    '<p class="mut">Coeficiente de Pearson do % válidos por local de votação. Perto de 1 indica base geográfica parecida; passe o mouse para ver quantos locais entram.</p>'+matriz()+'</div>'+
    '<div id="cmp"></div>';
}

function renderCmp(){
  var el = document.getElementById('cmp');
  if(S.a===S.b){ el.innerHTML = '<div class="painel"><p class="mut">Escolha duas eleições diferentes para comparar.</p></div>'; return; }
  var pts = D.locais.filter(function(l){ return l['p_'+S.a]!=null && l['p_'+S.b]!=null; })
    .map(function(l){ return {x:l['p_'+S.a], y:l['p_'+S.b], w:(l['v_'+S.a]||0)+(l['v_'+S.b]||0), t:l.local}; });
  var c = cor[S.a+'|'+S.b];
  var mov = D.locais.filter(function(l){return l.dpart!=null;});
  var subiu = mov.filter(function(l){return l.dpart>0;}).sort(function(a,b){return b.dpart-a.dpart;}).slice(0,8);
  var caiu = mov.filter(function(l){return l.dpart<0;}).sort(function(a,b){return a.dpart-b.dpart;}).slice(0,8);
  el.innerHTML =
    '<div class="painel"><h2>Desempenho por local de votação: '+S.a+' x '+S.b+'</h2>'+
    '<p class="mut">Cada ponto é um local de votação de '+esc(M.cidade)+'. A linha azul é o ajuste linear. O tamanho do ponto acompanha o total de votos do candidato nas duas eleições. '+
    (c && c.r!=null ? 'Correlação: <b>'+dec(c.r,2)+'</b> em '+num(c.n)+' locais.' : '')+'</p>'+dispersao(pts)+'</div>'+
    '<div class="duas">'+
    '<div class="painel"><h2>Onde a participação mais cresceu</h2><p class="mut">Variação, em pontos percentuais, da fatia do total de votos do candidato na cidade, de '+S.a+' para '+S.b+'.</p>'+mini(subiu)+'</div>'+
    '<div class="painel"><h2>Onde a participação mais caiu</h2><p class="mut">Mesma medida, nos locais com maior queda.</p>'+mini(caiu)+'</div>'+
    '</div>';
}

/* ---------- notas ---------- */
function notas(){
  var lista = E.map(function(e){
    return '<li><b>'+e.ano+'</b>: '+esc(e.cargo)+' ('+e.tipo+'), '+e.turno+'º turno, candidato '+esc(e.nome)+' (n. '+esc(e.numero)+').'+
      (e.ignoradas.length ? ' Não entram as contas de <i>'+e.ignoradas.map(function(x){return esc(x.eleicao)+' ('+esc(x.data)+')';}).join(', ')+'</i>, que vem no mesmo arquivo do TSE.' : '')+'</li>';
  }).join('');
  document.getElementById('aba-notas').innerHTML =
  '<div class="painel"><h2>Como ler os números</h2>'+
  '<h3>Fonte e eleições incluídas</h3><p>Portal de Dados Abertos do TSE, arquivos de votação por seção eleitoral (votacao_secao).</p><ul>'+lista+'</ul>'+
  '<h3>Medidas</h3><ul>'+
  '<li><b>% válidos</b>: votos do candidato na urna dividido pelos votos válidos do cargo naquela urna (total menos brancos e nulos).</li>'+
  '<li><b>Participação (Part.)</b>: parcela do total de votos do candidato na cidade de referência que veio daquela urna ou local. Em eleição estadual, o total é só o da cidade, não o do estado.</li>'+
  '<li><b>Variação da part.</b>: participação na eleição B menos participação na eleição A, em pontos percentuais, com A e B escolhidas na barra do topo. Mostra onde a base geográfica do candidato se deslocou.</li>'+
  '<li><b>Índice</b>: % válidos da urna dividido pelo % válidos da cidade inteira. Acima de 1,00 significa desempenho acima da média da cidade.</li>'+
  '<li><b>Correlação</b>: coeficiente de Pearson entre o % válidos de duas eleições nos locais de votação presentes nas duas. Perto de 1 indica base geográfica parecida.</li></ul>'+
  '<h3>Situação das urnas</h3><ul>'+
  '<li><b>Comparável</b>: a seção existe em mais de uma eleição e funciona no mesmo local de votação em todas.</li>'+
  '<li><b>Local diferente</b>: o número da seção existe em mais de uma eleição, mas em locais distintos. Não use para comparar.</li>'+
  '<li><b>Só ANO</b>: a seção existe em uma única eleição (foi criada, extinta ou renumerada).</li></ul>'+
  '<h3>Como os locais de votação são casados</h3><ul>'+
  '<li><b>código</b>: mesmo código de local dentro da zona e mesmo nome.</li>'+
  '<li><b>código (nome mudou)</b>: mesmo código, nome diferente. Confira se é o mesmo prédio.</li>'+
  '<li><b>nome</b>: código diferente ou ausente, mas o mesmo nome. <b>endereço</b>: só o endereço coincide. Esses dois são os mais frágeis.</li>'+
  '<li><b>só ANO</b>: o local aparece em uma única eleição.</li></ul>'+
  '<h3>Limites</h3><ul>'+
  '<li>Eleições majoritárias (prefeito) e proporcionais (vereador, deputado) têm regras diferentes. O % bruto não é comparável entre elas; use participação e índice.</li>'+
  '<li>Em eleição proporcional o candidato disputa com muitos nomes e o % por urna é pequeno e mais instável. Em urnas com poucos eleitores, uma ou duas pessoas mudam muito o percentual.</li>'+
  '<li>Só entram eleições ordinárias. O arquivo do TSE traz também eleições suplementares, com o mesmo cargo e turno, e elas são deixadas de fora para não misturar os votos.</li>'+
  '<li>As zonas eleitorais também mudam entre eleições (extintas, criadas, fundidas). Por isso o número da seção só serve para comparar quando o local de votação é o mesmo, e a visão por local de votação é a mais confiável.</li>'+
  '<li>As seções e os locais mudam entre eleições, e mais ainda quanto mais distantes elas são. Locais em que o número de seções mudou podem mostrar queda ou alta que é só redistribuição.</li>'+
  '<li>Correlação não prova transferência de votos: o eleitorado também mudou entre as eleições.</li></ul></div>';
}

/* ---------- barra de comparação ---------- */
var barraEl = document.getElementById('barra');
var optAnos = anos.map(function(y){ return '<option value="'+y+'">'+y+' ('+esc(info[y].cargo)+')</option>'; }).join('');
barraEl.innerHTML =
  '<span>Comparar</span><select id="selA">'+optAnos+'</select><span>com</span><select id="selB">'+optAnos+'</select>'+
  '<span class="sep"></span><span>Medida nas tabelas</span>'+
  '<select id="selM"><option value="p">% dos válidos</option><option value="part">Participação</option><option value="i">Índice</option><option value="v">Votos</option></select>';
var selA = document.getElementById('selA'), selB = document.getElementById('selB'), selM = document.getElementById('selM');
selA.value = S.a; selB.value = S.b; selM.value = S.m;

/* ---------- abas e montagem ---------- */
var abas = [['geral','Visão geral'],['secoes','Por seção (urna)'],['locais','Por local de votação'],['mun','Municípios'],['notas','Notas']];
var nav = document.getElementById('abas');
nav.innerHTML = abas.map(function(a,i){ return '<button type="button" data-a="'+a[0]+'" class="'+(i===0?'on':'')+'">'+a[1]+'</button>'; }).join('');
nav.addEventListener('click', function(e){
  var b = e.target.closest('button'); if(!b) return;
  var alvo = b.getAttribute('data-a');
  nav.querySelectorAll('button').forEach(function(x){ x.classList.toggle('on', x===b); });
  abas.forEach(function(a){ document.getElementById('aba-'+a[0]).hidden = (a[0]!==alvo); });
  barraEl.hidden = (alvo==='mun' || alvo==='notas');
});

atualizarDelta();
geral();
var ult = anos[anos.length-1];
var tSec = criarTabela('aba-secoes', D.secoes, colsSec, {zonas:1, status:'status', rotStatus:'Todas as situações', dir:1, arquivo:'por_secao', ph:'Buscar seção, local ou endereço...',
  texto:function(l){return l.secao+' '+l.local+' '+l.end;}});
var tLoc = criarTabela('aba-locais', D.locais, colsLoc, {zonas:1, status:'metodo', rotStatus:'Todos os cruzamentos', chave:function(){return S.m+'_'+ult;}, dir:-1, arquivo:'por_local', ph:'Buscar local ou endereço...',
  texto:function(l){return l.local+' '+l.end;}});
criarTabela('aba-mun', D.municipios, colsMun, {status:'cidade_ref', rotStatus:'Todos', chave:function(){return 'v_'+ult;}, dir:-1, arquivo:'municipios', ph:'Buscar município...',
  texto:function(l){return l.municipio;}});
notas();
renderCmp();

function atualizar(){
  atualizarDelta();
  renderCmp();
  tSec.refresh();
  tLoc.refresh();
}
selA.addEventListener('change', function(){ S.a = selA.value; atualizar(); });
selB.addEventListener('change', function(){ S.b = selB.value; atualizar(); });
selM.addEventListener('change', function(){ S.m = selM.value; atualizar(); });
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
