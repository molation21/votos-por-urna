#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
votos_urna.py - cruzamento da votacao por urna de um candidato entre 2024 e 2026.

O que faz
  1. Baixa do TSE (dados abertos) a votacao por secao eleitoral de 2024 e de 2026
     para a UF escolhida (padrao: PR).
  2. Localiza o candidato em cada eleicao (2024: prefeito; 2026: deputado estadual).
  3. Cruza a votacao por secao (urna) e por local de votacao no municipio onde ele
     disputou a prefeitura.
  4. Gera, na pasta "saida": plataforma.html (painel completo, abre no navegador),
     por_secao.csv, por_local.csv e municipios_2026.csv.

Requisitos: Python 3.8 ou superior. Nao precisa instalar nenhum pacote.

Uso basico
  python3 votos_urna.py

Se os ZIPs ja estiverem no computador
  python3 votos_urna.py --zip-2024 votacao_secao_2024_PR.zip --zip-2026 votacao_secao_2026_PR.zip

Outras opcoes: python3 votos_urna.py --help

Fonte dos dados: TSE, Portal de Dados Abertos (licenca CC BY).
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

VERSAO = 1
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


def varrer(caminho_zip, cargo, turno, termos):
    """Le o CSV de votacao por secao uma unica vez (em fluxo, sem carregar tudo)."""
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
        ncol = len(cab)

        secoes = {}   # (mun, zona, secao) -> [total, nao_validos, nr_local, nm_local, endereco, nm_mun]
        cands = {}    # (nr, nome, sq) -> {(mun, zona, secao): votos}
        cargos_vistos = {}
        turnos_vistos = set()
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
    if not secoes:
        vistos = ", ".join("%s=%s" % (k, v) for k, v in sorted(cargos_vistos.items()))
        raise SystemExit(
            "Nenhuma linha com cargo %s e turno %s em %s.\n  Cargos no arquivo: %s\n  Turnos do cargo: %s\n"
            "Ajuste --cargo-AAAA / --turno-AAAA." % (cargo, turno, nome, vistos, sorted(turnos_vistos)))
    return {"secoes": secoes, "cands": cands, "ds_cargo": ds_cargo, "linhas": n, "arquivo": nome}


def varrer_com_cache(caminho_zip, ano, cargo, turno, termos, pasta_dados, usar_cache):
    assin = (os.path.getsize(caminho_zip), int(os.path.getmtime(caminho_zip)), cargo, turno, tuple(termos), VERSAO)
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
    res = varrer(caminho_zip, cargo, turno, termos)
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
        raise SystemExit("Nenhum candidato com os termos de busca em %s. Use --busca para ajustar os termos." % rotulo)
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
            g = loc[k] = {"zona": a["zona"], "nome": a["nm_local"], "end": a["end"],
                          "secoes": 0, "votos": 0, "validos": 0}
        g["secoes"] += 1
        g["votos"] += a["votos"]
        g["validos"] += a["validos"]
    return loc


def cruzar_locais(L24, L26):
    pares = []
    u24, u26 = set(), set()
    for k, g in L24.items():
        h = L26.get(k)
        if h is not None:
            met = "código" if norm(g["nome"]) == norm(h["nome"]) else "código (nome mudou)"
            pares.append((g, h, met))
            u24.add(k)
            u26.add(k)
    por_nome = defaultdict(list)
    for k, h in L26.items():
        if k not in u26:
            por_nome[norm(h["nome"])].append(k)
    for k, g in L24.items():
        if k in u24:
            continue
        cand = [x for x in por_nome.get(norm(g["nome"]), []) if x not in u26]
        if cand:
            mesma = [x for x in cand if x[0] == g["zona"]]
            k2 = (mesma or cand)[0]
            pares.append((g, L26[k2], "nome"))
            u24.add(k)
            u26.add(k2)
    for k, g in L24.items():
        if k not in u24:
            pares.append((g, None, "só 2024"))
    for k, h in L26.items():
        if k not in u26:
            pares.append((None, h, "só 2026"))
    return pares


def montar(res24, esc24, res26, esc26, args):
    mun_ref = esc24["mun_top"]
    nome_ref = esc24["nome_mun_top"]
    if esc24["n_mun"] > 1:
        print("  aviso: o candidato de 2024 tem votos em %d municipios; usando %s (o mais votado)"
              % (esc24["n_mun"], nome_ref))
    s24 = indexar(res24["secoes"], esc24["votos"], mun_ref)
    s26 = indexar(res26["secoes"], esc26["votos"], mun_ref)
    if not s26:
        print("  aviso: nao ha secoes de %s no arquivo de 2026 para o cargo escolhido" % nome_ref)

    V24 = sum(a["votos"] for a in s24.values())
    T24 = sum(a["validos"] for a in s24.values())
    V26 = sum(a["votos"] for a in s26.values())
    T26 = sum(a["validos"] for a in s26.values())
    pc24, pc26 = pct(V24, T24), pct(V26, T26)

    # ---- por secao (urna)
    chaves = sorted(set(s24) | set(s26))
    secoes_out = []
    status_cont = defaultdict(int)
    for k in chaves:
        a, b = s24.get(k), s26.get(k)
        ca, cb = calc(a, V24, pc24), calc(b, V26, pc26)
        if a and b:
            if mesmo_local(a, b):
                status, local = "Comparável", a["nm_local"]
            else:
                status, local = "Local diferente", "%s → %s" % (a["nm_local"], b["nm_local"])
        elif a:
            status, local = "Só 2024", a["nm_local"]
        else:
            status, local = "Só 2026", b["nm_local"]
        status_cont[status] += 1
        ref = a or b
        dpart = round(cb["part"] - ca["part"], 4) if (ca["part"] is not None and cb["part"] is not None) else None
        secoes_out.append({
            "zona": k[0], "secao": k[1], "local": local, "end": ref["end"],
            "v24": ca["v"], "t24": ca["t"], "p24": ca["p"], "part24": ca["part"], "i24": ca["i"],
            "v26": cb["v"], "t26": cb["t"], "p26": cb["p"], "part26": cb["part"], "i26": cb["i"],
            "dpart": dpart, "status": status,
        })

    # ---- por local de votacao
    pares = cruzar_locais(agrupar_locais(s24), agrupar_locais(s26))
    locais_out = []
    for g, h, met in pares:
        ca, cb = calc(g, V24, pc24), calc(h, V26, pc26)
        ref = g or h
        nome = g["nome"] if g else h["nome"]
        if g and h and norm(g["nome"]) != norm(h["nome"]):
            nome = "%s → %s" % (g["nome"], h["nome"])
        dpart = round(cb["part"] - ca["part"], 4) if (ca["part"] is not None and cb["part"] is not None) else None
        locais_out.append({
            "zona": ref["zona"], "local": nome, "end": ref["end"],
            "sec24": g["secoes"] if g else None, "sec26": h["secoes"] if h else None,
            "v24": ca["v"], "t24": ca["t"], "p24": ca["p"], "part24": ca["part"], "i24": ca["i"],
            "v26": cb["v"], "t26": cb["t"], "p26": cb["p"], "part26": cb["part"], "i26": cb["i"],
            "dpart": dpart, "metodo": met,
        })
    locais_out.sort(key=lambda d: (d["zona"], d["local"]))
    comp = [(d["p24"], d["p26"]) for d in locais_out if d["p24"] is not None and d["p26"] is not None]
    r_locais = pearson([x for x, _ in comp], [y for _, y in comp])

    # ---- municipios em 2026 (todo o estado)
    agg = {}
    for (mun, zona, secao), reg in res26["secoes"].items():
        a = agg.get(mun)
        if a is None:
            a = agg[mun] = [reg[5], 0, 0, 0]
        a[1] += esc26["votos"].get((mun, zona, secao), 0)
        a[2] += reg[0] - reg[1]
        a[3] += 1
    mun_out = []
    for mun, (nm, v, t, n) in agg.items():
        mun_out.append({"municipio": nm, "v26": v, "t26": t, "p26": pct(v, t), "secoes": n,
                        "cidade_ref": "sim" if mun == mun_ref else "não"})
    mun_out.sort(key=lambda d: -d["v26"])

    meta = {
        "gerado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "uf": args.uf,
        "cidade": nome_ref,
        "c24": {"nome": esc24["nome"], "numero": esc24["nr"], "cargo": res24["ds_cargo"],
                "turno": args.turno_2024, "votos_total": esc24["total"]},
        "c26": {"nome": esc26["nome"], "numero": esc26["nr"], "cargo": res26["ds_cargo"],
                "turno": args.turno_2026, "votos_total": esc26["total"], "n_mun": esc26["n_mun"]},
        "v24": V24, "t24": T24, "p24": pc24, "v26": V26, "t26": T26, "p26": pc26,
        "r_locais": r_locais, "n_locais_comp": len(comp),
        "secoes_total": len(chaves), "secoes_comp": status_cont.get("Comparável", 0),
        "secoes_24": len(s24), "secoes_26": len(s26),
        "status_secoes": dict(status_cont),
    }
    return {"meta": meta, "secoes": secoes_out, "locais": locais_out, "municipios": mun_out}


# ----------------------------------------------------------------------------
# saida
# ----------------------------------------------------------------------------
def grava_csv(caminho, linhas, colunas):
    with open(caminho, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow([t for _, t in colunas])
        for d in linhas:
            w.writerow([br(d.get(k)) if isinstance(d.get(k), (int, float)) else (d.get(k) or "")
                        for k, _ in colunas])


COLS_SEC = [("zona", "Zona"), ("secao", "Seção"), ("local", "Local de votação"), ("end", "Endereço"),
            ("v24", "Votos 2024"), ("t24", "Votos válidos 2024"), ("p24", "% válidos 2024"),
            ("part24", "Participação 2024 (%)"), ("i24", "Índice 2024"),
            ("v26", "Votos 2026"), ("t26", "Votos válidos 2026"), ("p26", "% válidos 2026"),
            ("part26", "Participação 2026 (%)"), ("i26", "Índice 2026"),
            ("dpart", "Variação da participação (p.p.)"), ("status", "Situação")]
COLS_LOC = [("zona", "Zona"), ("local", "Local de votação"), ("end", "Endereço"),
            ("sec24", "Seções 2024"), ("sec26", "Seções 2026"),
            ("v24", "Votos 2024"), ("t24", "Votos válidos 2024"), ("p24", "% válidos 2024"),
            ("part24", "Participação 2024 (%)"), ("i24", "Índice 2024"),
            ("v26", "Votos 2026"), ("t26", "Votos válidos 2026"), ("p26", "% válidos 2026"),
            ("part26", "Participação 2026 (%)"), ("i26", "Índice 2026"),
            ("dpart", "Variação da participação (p.p.)"), ("metodo", "Cruzamento")]
COLS_MUN = [("municipio", "Município"), ("v26", "Votos 2026"), ("t26", "Votos válidos 2026"),
            ("p26", "% válidos 2026"), ("secoes", "Seções"), ("cidade_ref", "Cidade da prefeitura em 2024")]


def gerar_html(dados):
    texto = json.dumps(dados, ensure_ascii=False, separators=(",", ":"))
    texto = texto.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    titulo = "%s - %s" % (dados["meta"]["c24"]["nome"], dados["meta"]["cidade"])
    return HTML_MODELO.replace("__TITULO__", titulo.replace("<", "&lt;")).replace("__DADOS__", texto)


def main():
    ap = argparse.ArgumentParser(
        description="Cruza a votação por urna de um candidato entre 2024 (prefeito) e 2026 (deputado estadual).")
    ap.add_argument("--uf", default="PR", help="UF dos arquivos do TSE (padrão: PR)")
    ap.add_argument("--busca", nargs="+", default=["MAC DONALD", "MACDONALD", "GHISI"],
                    help="termos para achar o candidato pelo nome de urna (padrão: MAC DONALD, MACDONALD, GHISI)")
    ap.add_argument("--cargo-2024", default="11", help="código do cargo em 2024 (11 = prefeito)")
    ap.add_argument("--cargo-2026", default="7", help="código do cargo em 2026 (7 = deputado estadual)")
    ap.add_argument("--turno-2024", default="1", help="turno usado em 2024 (padrão: 1)")
    ap.add_argument("--turno-2026", default="1", help="turno usado em 2026 (padrão: 1)")
    ap.add_argument("--numero-2024", help="fixa o candidato de 2024 pelo número de urna (evita a pergunta)")
    ap.add_argument("--numero-2026", help="fixa o candidato de 2026 pelo número de urna (evita a pergunta)")
    ap.add_argument("--zip-2024", help="caminho do ZIP de 2024 já baixado")
    ap.add_argument("--zip-2026", help="caminho do ZIP de 2026 já baixado")
    ap.add_argument("--dados", default="dados", help="pasta dos arquivos baixados (padrão: dados)")
    ap.add_argument("--saida", default="saida", help="pasta dos resultados (padrão: saida)")
    ap.add_argument("--auto", action="store_true", help="não perguntar: usar o candidato mais votado")
    ap.add_argument("--refazer", action="store_true", help="ignorar o cache e reler os arquivos")
    ap.add_argument("--sem-abrir", action="store_true", help="não abrir o navegador no final")
    args = ap.parse_args()

    termos = [norm(t) for t in args.busca]
    resultados = {}
    for ano, cargo, turno, zip_arg in (("2024", args.cargo_2024, args.turno_2024, args.zip_2024),
                                       ("2026", args.cargo_2026, args.turno_2026, args.zip_2026)):
        print("\n== Eleição %s ==" % ano)
        if zip_arg:
            caminho = zip_arg
            if not os.path.exists(caminho):
                raise SystemExit("Arquivo não encontrado: %s" % caminho)
        else:
            caminho = baixar(URL_MODELO.format(ano=ano, uf=args.uf),
                             os.path.join(args.dados, "votacao_secao_%s_%s.zip" % (ano, args.uf)))
        res = varrer_com_cache(caminho, ano, cargo, turno, termos, args.dados, not args.refazer)
        esc = escolher(res, ano, args.auto, args.numero_2024 if ano == "2024" else args.numero_2026)
        resultados[ano] = (res, esc)

    print("\n== Cruzamento ==")
    dados = montar(resultados["2024"][0], resultados["2024"][1], resultados["2026"][0], resultados["2026"][1], args)

    os.makedirs(args.saida, exist_ok=True)
    grava_csv(os.path.join(args.saida, "por_secao.csv"), dados["secoes"], COLS_SEC)
    grava_csv(os.path.join(args.saida, "por_local.csv"), dados["locais"], COLS_LOC)
    grava_csv(os.path.join(args.saida, "municipios_2026.csv"), dados["municipios"], COLS_MUN)
    caminho_html = os.path.join(args.saida, "plataforma.html")
    with open(caminho_html, "w", encoding="utf-8") as f:
        f.write(gerar_html(dados))

    m = dados["meta"]
    print("  cidade de referência: %s" % m["cidade"])
    print("  2024 (%s): %s votos, %s%% dos válidos" % (m["c24"]["cargo"], mil(m["v24"]), br(m["p24"])))
    print("  2026 (%s): %s votos na cidade, %s%% dos válidos" % (m["c26"]["cargo"], mil(m["v26"]), br(m["p26"])))
    print("  seções: %d no total, %d comparáveis; correlação entre os pleitos por local: %s"
          % (m["secoes_total"], m["secoes_comp"], br(m["r_locais"])))
    print("\nConfira os totais acima com o resultado oficial do TSE antes de usar os números.")
    print("\nPronto. Arquivos em %s/ :" % args.saida)
    for nome in ("plataforma.html", "por_secao.csv", "por_local.csv", "municipios_2026.csv"):
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
:root{--bg:#f6f6f3;--card:#ffffff;--tx:#1d1d1f;--mut:#6a6a70;--bd:#e2e2dd;--a24:#2f6fb5;--a26:#d9822b;--pos:#23864f;--neg:#c2413b}
@media (prefers-color-scheme:dark){:root{--bg:#151517;--card:#1f1f22;--tx:#ececee;--mut:#9b9ba2;--bd:#34343a;--a24:#6aa6e6;--a26:#f0a45a;--pos:#52c08a;--neg:#ee7b73}}
*{box-sizing:border-box}
body{margin:0;font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--tx)}
header{max-width:1280px;margin:0 auto;padding:20px 24px 0}
h1{font-size:22px;margin:0 0 2px}
.sub{color:var(--mut);margin:0 0 14px;font-size:14px}
nav{display:flex;gap:4px;flex-wrap:wrap;border-bottom:1px solid var(--bd)}
nav button{background:none;border:0;border-bottom:2px solid transparent;color:var(--mut);padding:10px 14px;font:inherit;cursor:pointer}
nav button.on{color:var(--tx);border-bottom-color:var(--a26);font-weight:600}
main{max-width:1280px;margin:0 auto;padding:20px 24px 56px}
.aba[hidden]{display:none}
.grade{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-bottom:20px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:14px 16px}
.card .t{font-size:12.5px;color:var(--mut)}
.card .v{font-size:26px;font-weight:650;margin:2px 0}
.card .n{font-size:12.5px;color:var(--mut)}
.painel{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:16px;margin-bottom:20px}
.painel h2{font-size:16px;margin:0 0 4px}
.painel p{margin:4px 0 10px}
.duas{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:16px}
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
.num{text-align:right;font-variant-numeric:tabular-nums}
.pos{color:var(--pos)}.neg{color:var(--neg)}.mut{color:var(--mut)}
svg text{fill:var(--mut);font-size:11px}
svg .eixo{stroke:var(--bd)}
svg .ponto{fill:var(--a26);fill-opacity:.6;stroke:var(--a26)}
svg .reta{stroke:var(--a24);stroke-width:2;fill:none}
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
var M = D.meta;
var nf = new Intl.NumberFormat('pt-BR');
function num(v){ return v==null ? '-' : nf.format(v); }
function dec(v,c){ return v==null ? '-' : v.toLocaleString('pt-BR',{minimumFractionDigits:c,maximumFractionDigits:c}); }
function pct(v){ return v==null ? '-' : dec(v,2)+'%'; }
function pp(v){ return v==null ? '-' : (v>0?'+':'')+dec(v,2)+' p.p.'; }
function ind(v){ return v==null ? '-' : dec(v,2); }
function esc(s){ return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];}); }
function norm(s){ return String(s==null?'':s).normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase(); }
function sinal(v){ return v==null||v===0 ? '' : (v>0?'pos':'neg'); }

document.title = 'Votos por urna: ' + M.c24.nome + ' - ' + M.cidade;
document.getElementById('titulo').textContent = M.c24.nome + ': votos por urna em ' + M.cidade;
document.getElementById('subtitulo').textContent =
  '2024 (' + M.c24.cargo + ', n. ' + M.c24.numero + ') x 2026 (' + M.c26.cargo + ', n. ' + M.c26.numero + ') | UF ' + M.uf +
  ' | dados do TSE | gerado em ' + M.gerado_em;

/* ---------- tabela generica ---------- */
function criarTabela(id, linhas, cols, opt){
  var raiz = document.getElementById(id);
  var st = {busca:'', zona:'', status:'', col: opt.col==null?null:opt.col, dir: opt.dir||-1};
  var zonas = opt.zonas ? Array.from(new Set(linhas.map(function(l){return l.zona;}))).sort(function(a,b){return a-b;}) : [];
  var stats = opt.status ? Array.from(new Set(linhas.map(function(l){return l[opt.status];}))).sort() : [];
  raiz.innerHTML =
    '<div class="ctl">'+
    '<input type="search" class="busca" placeholder="'+esc(opt.ph||'Buscar...')+'">'+
    (zonas.length ? '<select class="zona"><option value="">Todas as zonas</option>'+zonas.map(function(z){return '<option value="'+z+'">Zona '+z+'</option>';}).join('')+'</select>' : '')+
    (stats.length ? '<select class="status"><option value="">Todas as situações</option>'+stats.map(function(s){return '<option>'+esc(s)+'</option>';}).join('')+'</select>' : '')+
    '<span class="cont"></span><button type="button" class="exp">Exportar CSV</button></div>'+
    '<div class="tw"><table><thead></thead><tbody></tbody></table></div>';
  var thead = raiz.querySelector('thead'), tbody = raiz.querySelector('tbody'), cont = raiz.querySelector('.cont');

  function filtradas(){
    var q = norm(st.busca);
    var r = linhas.filter(function(l){
      return (!st.zona || String(l.zona)===st.zona) && (!st.status || l[opt.status]===st.status) && (!q || norm(opt.texto(l)).indexOf(q)>=0);
    });
    if(st.col!=null){
      var c = cols[st.col];
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
      return '<th class="'+(c.n?'num':'')+'" data-i="'+i+'">'+esc(c.t)+(st.col===i?(st.dir>0?' ▲':' ▼'):'')+'</th>';
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
    var i = +th.getAttribute('data-i');
    if(st.col===i){ st.dir = -st.dir; } else { st.col = i; st.dir = cols[i].n ? -1 : 1; }
    render();
  });
  raiz.querySelector('.busca').addEventListener('input', function(e){ st.busca = e.target.value; render(); });
  var sz = raiz.querySelector('.zona'); if(sz) sz.addEventListener('change', function(e){ st.zona = e.target.value; render(); });
  var ss = raiz.querySelector('.status'); if(ss) ss.addEventListener('change', function(e){ st.status = e.target.value; render(); });
  raiz.querySelector('.exp').addEventListener('click', function(){
    var r = filtradas();
    function cel(v){ if(v==null) return ''; if(typeof v==='number') return String(v).replace('.',','); return '"'+String(v).replace(/"/g,'""')+'"'; }
    var out = [cols.map(function(c){return '"'+c.t+'"';}).join(';')].concat(r.map(function(l){ return cols.map(function(c){return cel(l[c.k]);}).join(';'); }));
    var blob = new Blob(['\ufeff'+out.join('\r\n')], {type:'text/csv;charset=utf-8'});
    var a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = (opt.arquivo||'tabela')+'.csv';
    document.body.appendChild(a); a.click(); a.remove();
  });
  render();
}

var colsSec = [
  {k:'zona',t:'Zona',n:1},{k:'secao',t:'Seção',n:1},{k:'local',t:'Local de votação',w:1},
  {k:'v24',t:'Votos 2024',n:1,f:num},{k:'p24',t:'% válidos 2024',n:1,f:pct},
  {k:'v26',t:'Votos 2026',n:1,f:num},{k:'p26',t:'% válidos 2026',n:1,f:pct},
  {k:'part24',t:'Part. 2024',n:1,f:pct},{k:'part26',t:'Part. 2026',n:1,f:pct},
  {k:'dpart',t:'Variação da part.',n:1,f:pp,cls:sinal},
  {k:'i24',t:'Índice 2024',n:1,f:ind},{k:'i26',t:'Índice 2026',n:1,f:ind},
  {k:'status',t:'Situação'}
];
var colsLoc = [
  {k:'zona',t:'Zona',n:1},{k:'local',t:'Local de votação',w:1},{k:'end',t:'Endereço',w:1},
  {k:'sec24',t:'Seções 2024',n:1,f:num},{k:'sec26',t:'Seções 2026',n:1,f:num},
  {k:'v24',t:'Votos 2024',n:1,f:num},{k:'p24',t:'% válidos 2024',n:1,f:pct},
  {k:'v26',t:'Votos 2026',n:1,f:num},{k:'p26',t:'% válidos 2026',n:1,f:pct},
  {k:'part24',t:'Part. 2024',n:1,f:pct},{k:'part26',t:'Part. 2026',n:1,f:pct},
  {k:'dpart',t:'Variação da part.',n:1,f:pp,cls:sinal},
  {k:'i24',t:'Índice 2024',n:1,f:ind},{k:'i26',t:'Índice 2026',n:1,f:ind},
  {k:'metodo',t:'Cruzamento'}
];
var colsMun = [
  {k:'municipio',t:'Município'},{k:'v26',t:'Votos 2026',n:1,f:num},{k:'t26',t:'Votos válidos',n:1,f:num},
  {k:'p26',t:'% válidos',n:1,f:pct},{k:'secoes',t:'Seções',n:1,f:num},{k:'cidade_ref',t:'Cidade da prefeitura em 2024'}
];

/* ---------- visão geral ---------- */
function cartao(t,v,n){ return '<div class="card"><div class="t">'+t+'</div><div class="v">'+v+'</div><div class="n">'+n+'</div></div>'; }

function mini(linhas){
  if(!linhas.length) return '<p class="mut">Nenhum local nesta situação.</p>';
  return '<div class="tw"><table><thead><tr><th>Local</th><th class="num">Part. 2024</th><th class="num">Part. 2026</th><th class="num">Variação</th></tr></thead><tbody>'+
    linhas.map(function(l){
      return '<tr><td class="largo">'+esc(l.local)+'</td><td class="num">'+pct(l.part24)+'</td><td class="num">'+pct(l.part26)+'</td><td class="num '+sinal(l.dpart)+'">'+pp(l.dpart)+'</td></tr>';
    }).join('')+'</tbody></table></div>';
}

function dispersao(pts){
  if(pts.length<3) return '<p class="mut">Poucos locais comparáveis para montar o gráfico.</p>';
  var W=640,H=420,ml=58,mr=16,mt=14,mb=48;
  var mx=Math.max.apply(null,pts.map(function(p){return p.x;}))*1.08;
  var my=Math.max.apply(null,pts.map(function(p){return p.y;}))*1.08;
  var mw=Math.max.apply(null,pts.map(function(p){return p.w;}));
  function X(v){return ml+(W-ml-mr)*v/mx;}
  function Y(v){return H-mb-(H-mt-mb)*v/my;}
  var n=pts.length, sx=0, sy=0, i;
  pts.forEach(function(p){sx+=p.x; sy+=p.y;});
  var mxx=sx/n, myy=sy/n, sxx=0, sxy=0;
  pts.forEach(function(p){sxx+=(p.x-mxx)*(p.x-mxx); sxy+=(p.x-mxx)*(p.y-myy);});
  var b=sxx?sxy/sxx:0, a=myy-b*mxx;
  var xmin=Math.min.apply(null,pts.map(function(p){return p.x;})), xmax=Math.max.apply(null,pts.map(function(p){return p.x;}));
  var s='<svg viewBox="0 0 '+W+' '+H+'" width="100%" role="img" aria-label="Dispersão do desempenho percentual por local de votação, 2024 contra 2026">';
  for(i=0;i<=5;i++){
    var vx=mx*i/5, vy=my*i/5;
    s+='<line class="eixo" x1="'+X(vx)+'" y1="'+(H-mb)+'" x2="'+X(vx)+'" y2="'+mt+'"/>';
    s+='<line class="eixo" x1="'+ml+'" y1="'+Y(vy)+'" x2="'+(W-mr)+'" y2="'+Y(vy)+'"/>';
    s+='<text x="'+X(vx)+'" y="'+(H-mb+16)+'" text-anchor="middle">'+dec(vx,1)+'%</text>';
    s+='<text x="'+(ml-8)+'" y="'+(Y(vy)+4)+'" text-anchor="end">'+dec(vy,1)+'%</text>';
  }
  s+='<text x="'+((ml+W-mr)/2)+'" y="'+(H-8)+'" text-anchor="middle">% dos votos válidos em 2024 (prefeito)</text>';
  s+='<text transform="translate(14,'+((mt+H-mb)/2)+') rotate(-90)" text-anchor="middle">% dos votos válidos em 2026 (dep. estadual)</text>';
  pts.forEach(function(p){
    var r=3+7*Math.sqrt(p.w/mw);
    s+='<circle class="ponto" cx="'+X(p.x)+'" cy="'+Y(p.y)+'" r="'+r.toFixed(1)+'"><title>'+esc(p.t)+' | 2024: '+pct(p.x)+' | 2026: '+pct(p.y)+'</title></circle>';
  });
  s+='<line class="reta" x1="'+X(xmin)+'" y1="'+Y(Math.max(0,a+b*xmin))+'" x2="'+X(xmax)+'" y2="'+Y(Math.max(0,a+b*xmax))+'"/>';
  return s+'</svg>';
}

function geral(){
  var el = document.getElementById('aba-geral');
  var st = M.status_secoes || {};
  var cards = [
    cartao('Votos em '+esc(M.cidade)+' em 2024', num(M.v24), pct(M.p24)+' dos válidos ('+esc(M.c24.cargo)+')'),
    cartao('Votos em '+esc(M.cidade)+' em 2026', num(M.v26), pct(M.p26)+' dos válidos ('+esc(M.c26.cargo)+')'),
    cartao('Votos em 2026 no estado', num(M.c26.votos_total), 'em '+num(M.c26.n_mun)+' municípios'),
    cartao('Correlação entre os dois pleitos', M.r_locais==null?'-':dec(M.r_locais,2), 'desempenho % por local de votação ('+num(M.n_locais_comp)+' locais)'),
    cartao('Urnas comparáveis', num(M.secoes_comp), 'de '+num(M.secoes_total)+' seções ('+num(st['Local diferente']||0)+' com local diferente, '+num((st['Só 2024']||0)+(st['Só 2026']||0))+' só em um dos anos)')
  ];
  var comp = D.locais.filter(function(l){return l.p24!=null && l.p26!=null;});
  var pts = comp.map(function(l){return {x:l.p24,y:l.p26,w:(l.v24||0)+(l.v26||0),t:l.local};});
  var mov = D.locais.filter(function(l){return l.dpart!=null;}).slice();
  var subiu = mov.filter(function(l){return l.dpart>0;}).sort(function(a,b){return b.dpart-a.dpart;}).slice(0,8);
  var caiu = mov.filter(function(l){return l.dpart<0;}).sort(function(a,b){return a.dpart-b.dpart;}).slice(0,8);
  el.innerHTML =
    '<div class="grade">'+cards.join('')+'</div>'+
    '<div class="painel"><h2>Desempenho por local de votação: 2024 x 2026</h2>'+
    '<p class="mut">Cada ponto é um local de votação de '+esc(M.cidade)+'. A linha azul é o ajuste linear. O tamanho do ponto acompanha o total de votos do candidato nos dois pleitos.</p>'+
    dispersao(pts)+'</div>'+
    '<div class="duas">'+
    '<div class="painel"><h2>Onde a participação mais cresceu</h2><p class="mut">Variação, em pontos percentuais, da fatia do total de votos do candidato na cidade.</p>'+mini(subiu)+'</div>'+
    '<div class="painel"><h2>Onde a participação mais caiu</h2><p class="mut">Mesma medida, nos locais com maior queda.</p>'+mini(caiu)+'</div>'+
    '</div>';
}

/* ---------- notas ---------- */
function notas(){
  document.getElementById('aba-notas').innerHTML =
  '<div class="painel"><h2>Como ler os números</h2>'+
  '<h3>Fonte</h3><p>Portal de Dados Abertos do TSE, arquivos de votação por seção eleitoral (votacao_secao) de 2024 e 2026.</p>'+
  '<h3>Medidas</h3><ul>'+
  '<li><b>% válidos</b>: votos do candidato na urna dividido pelos votos válidos do cargo naquela urna (total menos brancos e nulos).</li>'+
  '<li><b>Participação (Part.)</b>: parcela do total de votos do candidato na cidade que veio daquela urna ou local.</li>'+
  '<li><b>Variação da part.</b>: participação em 2026 menos participação em 2024, em pontos percentuais. Mostra onde a base geográfica do candidato se deslocou.</li>'+
  '<li><b>Índice</b>: % válidos da urna dividido pelo % válidos da cidade inteira. Acima de 1,00 significa desempenho acima da média da cidade.</li>'+
  '<li><b>Correlação</b>: coeficiente de Pearson entre o % válidos de 2024 e o de 2026 nos locais comparáveis. Perto de 1 indica base geográfica parecida nos dois pleitos.</li></ul>'+
  '<h3>Situação das urnas</h3><ul>'+
  '<li><b>Comparável</b>: a seção existe nos dois anos e funciona no mesmo local de votação.</li>'+
  '<li><b>Local diferente</b>: o número da seção existe nos dois anos, mas em locais distintos. Não use para comparar.</li>'+
  '<li><b>Só 2024 / Só 2026</b>: a seção foi criada, extinta ou renumerada entre as eleições.</li></ul>'+
  '<h3>Limites</h3><ul>'+
  '<li>2024 foi eleição majoritária (prefeito) e 2026 é proporcional (deputado estadual). Por isso o % bruto não é comparável entre os anos; use participação e índice.</li>'+
  '<li>As seções são recompostas entre eleições. O cruzamento por local de votação é mais estável que o por seção: casa por código do local e, na falta, pelo nome.</li>'+
  '<li>Correlação não prova transferência de votos: o eleitorado também mudou entre 2024 e 2026.</li></ul></div>';
}

/* ---------- abas ---------- */
var abas = [['geral','Visão geral'],['secoes','Por seção (urna)'],['locais','Por local de votação'],['mun','Municípios em 2026'],['notas','Notas']];
var nav = document.getElementById('abas');
nav.innerHTML = abas.map(function(a,i){ return '<button type="button" data-a="'+a[0]+'" class="'+(i===0?'on':'')+'">'+a[1]+'</button>'; }).join('');
nav.addEventListener('click', function(e){
  var b = e.target.closest('button'); if(!b) return;
  nav.querySelectorAll('button').forEach(function(x){ x.classList.toggle('on', x===b); });
  abas.forEach(function(a){ document.getElementById('aba-'+a[0]).hidden = (a[0]!==b.getAttribute('data-a')); });
});

geral();
criarTabela('aba-secoes', D.secoes, colsSec, {zonas:1, status:'status', col:null, dir:1, arquivo:'por_secao', ph:'Buscar seção, local ou endereço...',
  texto:function(l){return l.secao+' '+l.local+' '+l.end;}});
criarTabela('aba-locais', D.locais, colsLoc, {zonas:1, status:'metodo', col:5, dir:-1, arquivo:'por_local', ph:'Buscar local ou endereço...',
  texto:function(l){return l.local+' '+l.end;}});
criarTabela('aba-mun', D.municipios, colsMun, {status:'cidade_ref', col:1, dir:-1, arquivo:'municipios_2026', ph:'Buscar município...',
  texto:function(l){return l.municipio;}});
notas();
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
