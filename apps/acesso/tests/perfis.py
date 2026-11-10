"""Perfis de teste: claims do JWT (D-26) para cada situação de acesso.

Multiempresa (D-32): empresa 1 = Tática, empresa 2 = um cliente.
"""

TATICA = 1
CLIENTE = 2

PERFIS = {
    # ADM = só o desenvolvedor (D-31), lotado na Tática.
    'adm': {'papel': 'ADM', 'empresa_id': TATICA, 'empresa_interna': True, 'setor_financeiro': False},
    # Diretor = donos de cada empresa (D-30); acessa em qualquer setor.
    'diretor_tatica': {'papel': 'DIRETOR', 'empresa_id': TATICA, 'empresa_interna': True, 'setor_financeiro': False},
    'diretor_cliente': {'papel': 'DIRETOR', 'empresa_id': CLIENTE, 'empresa_interna': False, 'setor_financeiro': False},
    'gerente_financeiro': {'papel': 'GERENTE', 'empresa_id': TATICA, 'empresa_interna': True, 'setor_financeiro': True},
    'colaborador_financeiro': {'papel': 'COLABORADOR', 'empresa_id': TATICA, 'empresa_interna': True, 'setor_financeiro': True},
    'gerente_financeiro_cliente': {'papel': 'GERENTE', 'empresa_id': CLIENTE, 'empresa_interna': False, 'setor_financeiro': True},
    'colaborador_financeiro_cliente': {'papel': 'COLABORADOR', 'empresa_id': CLIENTE, 'empresa_interna': False, 'setor_financeiro': True},
    # Sem acesso: fora do setor financeiro.
    'gerente_outro_setor_tatica': {'papel': 'GERENTE', 'empresa_id': TATICA, 'empresa_interna': True, 'setor_financeiro': False},
    'colaborador_outro_setor_cliente': {'papel': 'COLABORADOR', 'empresa_id': CLIENTE, 'empresa_interna': False, 'setor_financeiro': False},
}

COM_ACESSO_E_ALTERA = ['adm', 'diretor_tatica', 'diretor_cliente', 'gerente_financeiro', 'gerente_financeiro_cliente']
COM_ACESSO_SO_LE = ['colaborador_financeiro', 'colaborador_financeiro_cliente']
SEM_ACESSO = ['gerente_outro_setor_tatica', 'colaborador_outro_setor_cliente']
