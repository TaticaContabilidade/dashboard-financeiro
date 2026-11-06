"""Perfis de teste: claims do JWT (D-26) para cada situação de acesso."""

# Perfis prontos (claims D-26). `empresa_interna` = Tática;
# `setor_financeiro` = setor Financeiro.
PERFIS = {
    'adm': {'papel': 'ADM', 'empresa_interna': True, 'setor_financeiro': False},
    'gerente_financeiro': {'papel': 'GERENTE', 'empresa_interna': True, 'setor_financeiro': True},
    'colaborador_financeiro': {'papel': 'COLABORADOR', 'empresa_interna': True, 'setor_financeiro': True},
    'diretor_tatica': {'papel': 'DIRETOR', 'empresa_interna': True, 'setor_financeiro': True},
    'gerente_outro_setor_tatica': {'papel': 'GERENTE', 'empresa_interna': True, 'setor_financeiro': False},
    'gerente_financeiro_cliente': {'papel': 'GERENTE', 'empresa_interna': False, 'setor_financeiro': True},
    'colaborador_cliente': {'papel': 'COLABORADOR', 'empresa_interna': False, 'setor_financeiro': False},
}
