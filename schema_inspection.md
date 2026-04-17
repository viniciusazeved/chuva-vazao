# Inspecao do esquema extraido do MDB HidroFlu

Total de tabelas: **3**

Gerado automaticamente por `scripts/extract_mdb.py`.

## `estados_brasil`

- Nome original no MDB: `Estados_Brasil`
- Linhas: **26**
- Colunas: **1**

### Esquema

| Coluna | Tipo (pandas) |
|---|---|
| `estado` | `str` |

### Amostra (3 primeiras linhas)

| `estado` |
|---|
| AC |
| AL |
| AM |

## `postos_idf_coeficientes`

- Nome original no MDB: `Postos_IDF_Coeficientes`
- Linhas: **8**
- Colunas: **6**

### Esquema

| Coluna | Tipo (pandas) |
|---|---|
| `descricao` | `str` |
| `estado` | `str` |
| `a` | `float64` |
| `b` | `float64` |
| `c` | `float64` |
| `k` | `float64` |

### Amostra (3 primeiras linhas)

| `descricao` | `estado` | `a` | `b` | `c` | `k` |
|---|---|---|---|---|---|
| Santa Cruz | RJ | 0.186 | 0.687 | 7.0 | 711.3 |
| Campo Grande | RJ | 0.187 | 0.689 | 14.0 | 891.67 |
| Mendanha | RJ | 0.177 | 0.698 | 12.0 | 843.78 |

## `postos_pfafstetter_coeficientes`

- Nome original no MDB: `Postos_Pfafstetter_Coeficientes`
- Linhas: **98**
- Colunas: **9**

### Esquema

| Coluna | Tipo (pandas) |
|---|---|
| `descricao` | `str` |
| `estado` | `str` |
| `a` | `float64` |
| `b` | `float64` |
| `c` | `float64` |
| `beta5min` | `float64` |
| `beta15min` | `float64` |
| `beta30min` | `float64` |
| `beta1h_6dias` | `float64` |

### Amostra (3 primeiras linhas)

| `descricao` | `estado` | `a` | `b` | `c` | `beta5min` | `beta15min` | `beta30min` | `beta1h_6dias` |
|---|---|---|---|---|---|---|---|---|
| Curitiba | PR | 0.2 | 25.0 | 20.0 | 0.1599999964237213 | 0.1599999964237213 | 0.1599999964237213 | 0.07999999821186066 |
| Jacarezinho | PR | 0.3 | 25.0 | 20.0 | -0.07999999821186066 | 0.07999999821186066 | 0.11999999731779099 | 0.07999999821186066 |
| Paranaguá | PR | 0.3 | 42.0 | 10.0 | 0.03999999910593033 | 0.11999999731779099 | 0.11999999731779099 | 0.1599999964237213 |
